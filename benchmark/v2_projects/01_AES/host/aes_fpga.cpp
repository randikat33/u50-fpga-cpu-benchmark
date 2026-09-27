// aes_fpga.cpp - AES-256-CTR FPGA host (v2), XRT native C++ API.
//
// One xclbin, one kernel (aes256ctr) for encryption and decryption, N CUs (detected).
// Pipeline: one worker thread per CU pulls chunk indices from a shared counter and runs
//   pread -> bo.map() | sync H2D | run | sync D2H | pwrite from bo.map()
// with counter offset = chunk_offset / 16. Chunks are written with pwrite at their own
// offsets, so the CUs overlap freely (H2D of one CU while the other computes / D2H's).
// Every BO is <= 1 GiB and allocated once.
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <exception>
#include <string>
#include <thread>
#include <vector>

#include "bench_common.hpp"
#include "aes_io.hpp"
#include "aes_ref.hpp"
#include "aes_ttable.hpp"

namespace {

constexpr size_t MiB = size_t(1) << 20;
constexpr const char* KNAME = "aes256ctr";
// Kernel argument indices (must match kernel/aes256ctr.h)
enum { ARG_IN = 0, ARG_OUT = 1, ARG_PRM = 2, ARG_BLKOFF = 3, ARG_NWORDS = 4 };
#if defined(XRT_SIM_KERNEL)            // xrt_sim build: CUs cannot be probed, take --cus
constexpr int DEFAULT_MAX_CU = 2;
#else
constexpr int DEFAULT_MAX_CU = 16;
#endif

void usage() {
    std::puts(
        "aes_fpga - AES-256-CTR on Alveo U50 (v2 host, XRT native API)\n"
        "usage: aes_fpga --xclbin X --mode enc|dec (--in FILE [--out FILE] | --no-io --size-mb N)\n"
        "                [--key HEX64] [--iv HEX32] [--chunk-mb 256] [--device 0] [--cus N]\n"
        "                [--verify] [--checksum] [--fsync]\n"
        "  --chunk-mb  chunk size per kernel call (<= 1024; each BO has this size)\n"
        "  --cus N     use at most N compute units (default: all found)\n"
        "  --no-io     every chunk carries the same generated data (filled once, untimed)\n"
        "  --verify    compare with the CPU T-table reference (file mode: after the run,\n"
        "              untimed; --no-io: per chunk inside the window -> verify_in_window=1)\n"
        "  --checksum  out_fnv1a64 of the output file (file mode only, untimed)\n"
        "defaults: key/iv = NIST SP 800-38A F.5.5");
}

struct Cu {
    xrt::kernel k;
    xrt::bo bin, bout, bprm;
    uint8_t* min = nullptr;
    uint8_t* mout = nullptr;
    xrt::run run;
};

struct PhaseT { double read = 0, h2d = 0, kern = 0, d2h = 0, write = 0, verify = 0; size_t chunks = 0; bool bad = false; };

void run_chunk(Cu& c, uint64_t blk_off, uint32_t words) {
    c.run.set_arg(ARG_BLKOFF, blk_off);
    c.run.set_arg(ARG_NWORDS, words);
    c.run.start();
    c.run.wait2();   // throws on kernel error (real XRT)
}

// Parallel T-table reference over [0, n) with the given counter base offset (in blocks).
void ref_parallel(const aes::TTKey& tk, const uint8_t iv[16], uint64_t blk_base, const uint8_t* in, uint8_t* out,
                  size_t n, unsigned nthr) {
    const size_t seg = 4 * MiB;
    const size_t nseg = (n + seg - 1) / seg;
    std::atomic<size_t> next{0};
    std::vector<std::thread> th;
    for (unsigned t = 0; t < std::max(1u, nthr); ++t)
        th.emplace_back([&] {
            for (size_t s; (s = next++) < nseg;) {
                size_t off = s * seg, len = std::min(seg, n - off);
                aes::ctr_ttable(tk, iv, blk_base + off / 16, in + off, out + off, len);
            }
        });
    for (auto& x : th) x.join();
}

}  // namespace

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Args a(argc, argv);
    if (a.has("help") || argc == 1) { usage(); return argc == 1 ? 1 : 0; }
    bench::Report rep;
    rep.set("project", "aes");
    rep.set("platform", "fpga");
    rep.set("impl", "aes256ctr-u50");
    int rc = 0;
    try {
        const std::string xclbin_path = a.str("xclbin");
        if (xclbin_path.empty()) throw std::runtime_error("--xclbin PATH is required");
        const std::string mode = a.str("mode", "enc");
        if (mode != "enc" && mode != "dec") throw std::runtime_error("--mode must be enc or dec");
        const bool noio = a.has("no-io");
        const std::string in_path = a.str("in"), out_path = a.str("out");
        if (!noio && in_path.empty()) throw std::runtime_error("--in FILE or --no-io --size-mb N required");
        const bool verify = a.has("verify");
        const long long chunk_mb = a.i64("chunk-mb", 256);
        if (chunk_mb < 1 || chunk_mb > 1024) throw std::runtime_error("--chunk-mb must be in [1, 1024] (BO limit 1 GiB)");
        const size_t chunk = size_t(chunk_mb) * MiB;   // multiple of 64 -> block/word aligned
        uint8_t key[32], iv[16];
        aes::parse_hex(a.str("key", aes::DEFAULT_KEY), key, 32, "--key");
        aes::parse_hex(a.str("iv", aes::DEFAULT_IV), iv, 16, "--iv");
        const aes::RoundKeys K = aes::expand_key(key);
        const aes::TTKey tk = aes::tt_key(K);
        const unsigned hw_thr = std::max(1u, std::thread::hardware_concurrency());

        // ---- input size ----
        aesio::Fd fin;
        size_t n;
        if (noio) n = size_t(a.i64("size-mb", 256)) * MiB;
        else { fin = aesio::open_in(in_path); n = size_t(aesio::fd_size(fin)); }
        if (n == 0) throw std::runtime_error("empty input");
        const size_t nchunks = (n + chunk - 1) / chunk;
        size_t bo_bytes = std::min(chunk, (n + 63) / 64 * 64);

        // ---- device + xclbin ----
        double t0 = bench::now_s();
        xrt::device dev(static_cast<unsigned int>(a.i64("device", 0)));
        const xrt::uuid uuid = dev.load_xclbin(xclbin_path);
        rep.set("t_xclbin_s", bench::now_s() - t0);

        // ---- CU detection ----
        const int max_cu = static_cast<int>(a.i64("cus", DEFAULT_MAX_CU));
        std::vector<Cu> cus;
        for (int i = 1; i <= max_cu; ++i) {
            try {
                Cu c;
                c.k = xrt::kernel(dev, uuid, std::string(KNAME) + ":{" + KNAME + "_" + std::to_string(i) + "}");
                cus.push_back(std::move(c));
            } catch (const std::exception&) { break; }
        }
        if (cus.empty()) throw std::runtime_error(std::string("no ") + KNAME + " CU found in " + xclbin_path);
        const size_t ncu = std::min(cus.size(), nchunks);
        cus.resize(ncu);

        // ---- buffers (once) ----
        t0 = bench::now_s();
        for (size_t i = 0; i < ncu; ++i) {
            Cu& c = cus[i];
            try {
                c.bin  = xrt::bo(dev, bo_bytes, xrt::bo::flags::normal, c.k.group_id(ARG_IN));
                c.bout = xrt::bo(dev, bo_bytes, xrt::bo::flags::normal, c.k.group_id(ARG_OUT));
                c.bprm = xrt::bo(dev, aes::PARAM_BYTES, xrt::bo::flags::normal, c.k.group_id(ARG_PRM));
            } catch (const std::exception& e) {
                throw std::runtime_error("BO allocation failed for CU " + std::to_string(i + 1) + " (" +
                                         std::to_string(bo_bytes) + " bytes per data BO): " + e.what() +
                                         " -- reduce --chunk-mb or --cus");
            }
            c.min = c.bin.map<uint8_t*>();
            c.mout = c.bout.map<uint8_t*>();
            c.run = xrt::run(c.k);
            c.run.set_arg(ARG_IN, c.bin);
            c.run.set_arg(ARG_OUT, c.bout);
            c.run.set_arg(ARG_PRM, c.bprm);
        }
        rep.set("t_alloc_s", bench::now_s() - t0);

        // ---- per-CU known-answer test (NIST F.5.5, exactly one word), then load user params ----
        t0 = bench::now_s();
        bool kat_ok = true;
        const aes::RoundKeys KN = aes::expand_key(aes::NIST_KEY);
        for (auto& c : cus) {
            uint8_t* p = c.bprm.map<uint8_t*>();
            aes::pack_params(KN, aes::NIST_IV, p);
            c.bprm.sync(XCL_BO_SYNC_BO_TO_DEVICE, aes::PARAM_BYTES, 0);
            std::memcpy(c.min, aes::NIST_PT, 64);
            c.bin.sync(XCL_BO_SYNC_BO_TO_DEVICE, 64, 0);
            run_chunk(c, 0, 1);
            c.bout.sync(XCL_BO_SYNC_BO_FROM_DEVICE, 64, 0);
            kat_ok = kat_ok && std::memcmp(c.mout, aes::NIST_CT, 64) == 0;
            aes::pack_params(K, iv, p);
            c.bprm.sync(XCL_BO_SYNC_BO_TO_DEVICE, aes::PARAM_BYTES, 0);
        }
        rep.set("kat_ok", kat_ok ? 1 : 0);
        if (!kat_ok) rc = 2;
        if (noio) {   // same data in every chunk; generated once per CU, untimed
            const double tg = bench::now_s();
            for (auto& c : cus) aesio::fill_pattern(c.min, bo_bytes, aesio::NOIO_SEED);
            rep.set("t_gen_s", bench::now_s() - tg);
        }
        rep.set("t_setup_s", bench::now_s() - t0);

        aesio::Fd fout;
        if (!out_path.empty() && !noio) fout = aesio::open_out(out_path, n);

        // no-io verification reference (one chunk's input; the counter offset differs per chunk)
        bench::AlignedBuf<uint8_t> vin, vref;
        if (noio && verify) { vin.alloc(bo_bytes); vref.alloc(bo_bytes); aesio::fill_pattern(vin.data(), bo_bytes, aesio::NOIO_SEED); }

        // ---- accelerated section ----
        std::vector<PhaseT> ph(ncu);
        std::vector<std::exception_ptr> errs(ncu);
        std::atomic<size_t> next{0};
        const bool do_write = fout.fd >= 0;
        auto worker = [&](size_t ci) {
            Cu& c = cus[ci];
            PhaseT& T = ph[ci];
            try {
                for (size_t k; (k = next++) < nchunks;) {
                    const size_t off = k * chunk, len = std::min(chunk, n - off);
                    const uint32_t words = static_cast<uint32_t>((len + 63) / 64);
                    const size_t wbytes = size_t(words) * 64;
                    double t = bench::now_s(), u;
                    if (!noio) {
                        aesio::pread_full(fin, c.min, len, off);                 // straight into BO memory
                        if (wbytes > len) std::memset(c.min + len, 0, wbytes - len);   // pad last word
                        u = bench::now_s(); T.read += u - t; t = u;
                    }
                    c.bin.sync(XCL_BO_SYNC_BO_TO_DEVICE, wbytes, 0);
                    u = bench::now_s(); T.h2d += u - t; t = u;
                    run_chunk(c, off / 16, words);
                    u = bench::now_s(); T.kern += u - t; t = u;
                    c.bout.sync(XCL_BO_SYNC_BO_FROM_DEVICE, wbytes, 0);
                    u = bench::now_s(); T.d2h += u - t; t = u;
                    if (do_write) {
                        aesio::pwrite_full(fout, c.mout, len, off);              // extra keystream dropped
                        u = bench::now_s(); T.write += u - t; t = u;
                    }
                    if (noio && verify) {
                        // chunks are verified in different threads; each needs its own reference buffer
                        std::vector<uint8_t> ref(len);
                        aes::ctr_ttable(tk, iv, off / 16, vin.data(), ref.data(), len);
                        if (std::memcmp(ref.data(), c.mout, len) != 0) T.bad = true;
                        u = bench::now_s(); T.verify += u - t;
                    }
                    ++T.chunks;
                }
            } catch (...) { errs[ci] = std::current_exception(); next = nchunks; }
        };
        bench::mark("start");
        const double tw0 = bench::now_s();
        {
            std::vector<std::thread> th;
            for (size_t i = 0; i < ncu; ++i) th.emplace_back(worker, i);
            for (auto& x : th) x.join();
        }
        const double t_window = bench::now_s() - tw0;
        bench::mark("end");
        for (auto& e : errs) if (e) std::rethrow_exception(e);

        PhaseT S;
        for (auto& p : ph) {
            S.read += p.read; S.h2d += p.h2d; S.kern += p.kern; S.d2h += p.d2h; S.write += p.write;
            S.verify += p.verify; S.chunks += p.chunks; S.bad = S.bad || p.bad;
        }
        double t_fsync = 0;
        if (do_write && a.has("fsync")) { const double tf = bench::now_s(); aesio::fsync_fd(fout); t_fsync = bench::now_s() - tf; }

        // ---- verification / checksum (file mode, untimed) ----
        int verified = 0, ok = rc == 0 ? 1 : 0;
        if (noio && verify) { verified = 1; if (S.bad) { ok = 0; rc = 3; } }
        if (!noio && verify) {
            if (out_path.empty()) throw std::runtime_error("--verify in file mode needs --out");
            aesio::Fd fo = aesio::open_in(out_path);
            if (aesio::fd_size(fo) != n) { ok = 0; rc = 3; }
            else {
                const size_t vc = std::min(n, chunk);
                bench::AlignedBuf<uint8_t> bi(vc), bo_(vc), br(vc);
                for (size_t off = 0; off < n && ok; off += vc) {
                    const size_t len = std::min(vc, n - off);
                    aesio::pread_full(fin, bi.data(), len, off);
                    aesio::pread_full(fo, bo_.data(), len, off);
                    ref_parallel(tk, iv, off / 16, bi.data(), br.data(), len, hw_thr);
                    if (std::memcmp(br.data(), bo_.data(), len) != 0) { ok = 0; rc = 3; }
                }
            }
            verified = 1;
        }
        if (a.has("checksum") && !out_path.empty() && !noio) {
            aesio::Fd fo = aesio::open_in(out_path);
            bench::AlignedBuf<uint8_t> b(std::min(n, chunk));
            uint64_t h = 1469598103934665603ull;
            for (size_t off = 0; off < n; off += b.size()) {
                const size_t len = std::min(b.size(), n - off);
                aesio::pread_full(fo, b.data(), len, off);
                h = bench::fnv1a64(b.data(), len, h);
            }
            rep.set("out_fnv1a64", bench::hex64(h));
        }

        // ---- report ----
        const double accel_sum = S.h2d + S.kern + S.d2h;
        const double io_sum = S.read + S.write + S.verify;
        double t_compute = t_window;
        std::string cdef = "window";
        if (io_sum > 0) {   // I/O interleaved in the window: attribute the accelerated share
            t_compute = t_window * accel_sum / (accel_sum + io_sum);
            cdef = "window_x_accel_share";
        }
        rep.set("mode", mode);
        rep.set("xclbin", xclbin_path);
        rep.set("n_cu", static_cast<unsigned long long>(ncu));
        rep.set("n_cu_found", static_cast<unsigned long long>(cus.size()));
        rep.set("io", noio ? 0 : 1);
        rep.set("bytes", static_cast<unsigned long long>(n));
        rep.set("size_mb", static_cast<double>(n) / MiB);
        rep.set("chunk_mb", chunk_mb);
        rep.set("nchunks", static_cast<unsigned long long>(nchunks));
        rep.set("bo_bytes", static_cast<unsigned long long>(bo_bytes));
        rep.set("t_read_s", S.read);
        rep.set("t_h2d_s", S.h2d);
        rep.set("t_kernel_s", S.kern);
        rep.set("t_d2h_s", S.d2h);
        rep.set("t_write_s", S.write + t_fsync);
        rep.set("t_fsync_s", t_fsync);
        rep.set("t_accel_window_s", t_window);
        rep.set("t_io_in_window_s", io_sum);
        rep.set("t_compute_s", t_compute);
        rep.set("compute_def", cdef);
        rep.set("phase_sums_are_thread_sums", 1);
        rep.set("verify_in_window", (noio && verify) ? 1 : 0);
        rep.set("throughput_gbps", n / t_compute / 1e9);
        rep.set("e2e_gbps", n / (t_window + t_fsync) / 1e9);
        rep.set("kernel_gbps_per_cu", S.kern > 0 ? n / S.kern / 1e9 : 0.0);
        rep.set("h2d_gbps_per_stream", S.h2d > 0 ? n / S.h2d / 1e9 : 0.0);
        rep.set("d2h_gbps_per_stream", S.d2h > 0 ? n / S.d2h / 1e9 : 0.0);
        rep.set("verified", verified);
        rep.set("ok", ok);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ERROR: %s\n", e.what());
        rep.set("error", e.what());
        rep.set("ok", 0);
        rc = 1;
    }
    rep.set("t_total_s", bench::now_s() - t_start);
    rep.print();
    return rc;
}

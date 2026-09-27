// aes_cpu.cpp - AES-256-CTR CPU baselines (v2).
//   --impl vaes     AVX-512 VAES, 16 blocks / iteration      (hand-written)
//   --impl aesni    AES-NI, 8 blocks / iteration             (hand-written)
//   --impl ttable   portable 32-bit T-table, no crypto/SIMD instructions
//   --impl openssl  OpenSSL 3 EVP_aes_256_ctr, one context per thread
// All four: OpenMP segment-parallel (block-aligned segments, counter = IV + offset/16),
// parallel first touch with the same schedule, identical file I/O to the FPGA host.
#include <omp.h>
#include <openssl/evp.h>
#include <openssl/crypto.h>

#include <algorithm>
#include <cstdio>
#include <functional>
#include <string>

#include "bench_common.hpp"
#include "aes_io.hpp"
#include "aes_ref.hpp"
#include "aes_ttable.hpp"
#include "aes_x86.hpp"

namespace {

constexpr size_t MiB = size_t(1) << 20;

void usage() {
    std::puts(
        "aes_cpu - AES-256-CTR CPU baselines (v2)\n"
        "usage: aes_cpu --impl vaes|aesni|ttable|openssl --mode enc|dec\n"
        "               (--in FILE [--out FILE] | --no-io --size-mb N)\n"
        "               [--key HEX64] [--iv HEX32] [--threads N] [--chunk-mb 256] [--seg-kb 4096]\n"
        "               [--verify] [--checksum] [--fsync] [--repeat N]\n"
        "  --mode      enc or dec (CTR: identical operation, recorded in the report)\n"
        "  --in/--out  input/output files (output written only if --out is given)\n"
        "  --no-io     generate --size-mb MiB of pseudo-random data in memory, no files\n"
        "  --chunk-mb  I/O chunk size for pread/pwrite (same as the FPGA host)\n"
        "  --seg-kb    parallel segment size (block aligned, multiple of 256 bytes)\n"
        "  --verify    compare the output with an independent reference (untimed)\n"
        "  --checksum  print out_fnv1a64 of the output (untimed)\n"
        "  --fsync     fsync the output file inside t_write_s\n"
        "  --repeat N  encrypt the buffer N times inside the measured window; t_compute_s is\n"
        "              the mean per pass (for energy measurements of short runs; default 1)\n"
        "defaults: key/iv = NIST SP 800-38A F.5.5, threads = omp_get_max_threads()");
}

using SegFn = std::function<void(const uint8_t* in, uint8_t* out, size_t n, uint64_t blk_off)>;

// Parallel driver: static schedule over block-aligned segments.
void par_segments(size_t n, size_t seg, const std::function<void(size_t, size_t)>& f) {
    const long nseg = static_cast<long>((n + seg - 1) / seg);
    #pragma omp parallel for schedule(static)
    for (long s = 0; s < nseg; ++s) {
        size_t off = size_t(s) * seg;
        f(off, std::min(seg, n - off));
    }
}

}  // namespace

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Args a(argc, argv);
    if (a.has("help") || argc == 1) { usage(); return argc == 1 ? 1 : 0; }
    bench::Report rep;
    int rc = 0;
    try {
        const std::string impl = a.str("impl", "vaes");
        const std::string mode = a.str("mode", "enc");
        if (mode != "enc" && mode != "dec") throw std::runtime_error("--mode must be enc or dec");
        const bool noio = a.has("no-io");
        const std::string in_path = a.str("in"), out_path = a.str("out");
        if (!noio && in_path.empty()) throw std::runtime_error("--in FILE or --no-io --size-mb N required");
        const int threads = static_cast<int>(a.i64("threads", omp_get_max_threads()));
        omp_set_num_threads(threads);
        const size_t chunk = static_cast<size_t>(a.i64("chunk-mb", 256)) * MiB;
        size_t seg = static_cast<size_t>(a.i64("seg-kb", 4096)) * 1024;
        seg = std::max<size_t>(256, seg / 256 * 256);
        uint8_t key[32], iv[16];
        aes::parse_hex(a.str("key", aes::DEFAULT_KEY), key, 32, "--key");
        aes::parse_hex(a.str("iv", aes::DEFAULT_IV), iv, 16, "--iv");
        const aes::RoundKeys K = aes::expand_key(key);

        // ---- implementation selection ----
        SegFn fn;
        std::string isa;
        aesx::VKey vk = aesx::v_key(K);
        aesx::NiKey nk = aesx::ni_key(K);
        aes::TTKey tk = aes::tt_key(K);
        if (impl == "vaes") {
            if (!aesx::HAVE_VAES) throw std::runtime_error("vaes: binary built without AVX-512 VAES support");
            // Some hypervisors hide the VAES CPUID bit although the instructions work;
            // AES_FORCE_VAES=1 skips the check (the KAT below still validates the result).
            if ((!__builtin_cpu_supports("vaes") || !__builtin_cpu_supports("avx512bw")) &&
                bench::env_int("AES_FORCE_VAES", 0) != 1)
                throw std::runtime_error("vaes: CPUID reports no AVX-512 VAES (set AES_FORCE_VAES=1 to override)");
            isa = "avx512-vaes";
            fn = [&](const uint8_t* i, uint8_t* o, size_t n, uint64_t b) { aesx::ctr_vaes(vk, iv, b, i, o, n); };
        } else if (impl == "aesni") {
            if (!aesx::HAVE_AESNI) throw std::runtime_error("aesni: binary built without AES-NI support");
            if (!__builtin_cpu_supports("aes")) throw std::runtime_error("aesni: this CPU has no AES-NI");
            isa = "aesni-sse";
            fn = [&](const uint8_t* i, uint8_t* o, size_t n, uint64_t b) { aesx::ctr_aesni(nk, iv, b, i, o, n); };
        } else if (impl == "ttable") {
            isa = "scalar-ttable";
            fn = [&](const uint8_t* i, uint8_t* o, size_t n, uint64_t b) { aes::ctr_ttable(tk, iv, b, i, o, n); };
        } else if (impl == "openssl") {
            isa = "openssl-evp";
            // handled by its own parallel region below (one EVP context per thread)
        } else {
            throw std::runtime_error("unknown --impl " + impl + " (use scripts/run_all_cpu.sh for all)");
        }

        auto run_openssl = [&](const uint8_t* in, uint8_t* out, size_t n) {
            bool fail = false;
            const long nseg = static_cast<long>((n + seg - 1) / seg);
            #pragma omp parallel reduction(||:fail)
            {
                EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
                if (!ctx || EVP_EncryptInit_ex(ctx, EVP_aes_256_ctr(), nullptr, key, nullptr) != 1) fail = true;
                #pragma omp for schedule(static)
                for (long s = 0; s < nseg; ++s) {
                    if (fail) continue;
                    size_t off = size_t(s) * seg, len = std::min(seg, n - off);
                    uint8_t ivs[16];
                    aes::ctr_add_iv(iv, off / 16, ivs);
                    int outl = 0;
                    if (EVP_EncryptInit_ex(ctx, nullptr, nullptr, nullptr, ivs) != 1 ||
                        EVP_EncryptUpdate(ctx, out + off, &outl, in + off, static_cast<int>(len)) != 1 ||
                        size_t(outl) != len)
                        fail = true;
                }
                EVP_CIPHER_CTX_free(ctx);
            }
            if (fail) throw std::runtime_error("OpenSSL EVP failure");
        };
        auto run = [&](const uint8_t* in, uint8_t* out, size_t n) {
            if (impl == "openssl") { run_openssl(in, out, n); return; }
            par_segments(n, seg, [&](size_t off, size_t len) { fn(in + off, out + off, len, off / 16); });
        };

        // ---- known-answer self-test of the selected implementation (untimed) ----
        {
            // The lambdas capture key/iv/round keys by reference: switch them to the NIST
            // F.5.5 values, run 64 bytes through the full driver, restore.
            uint8_t ct[64], save_iv[16], save_key[32];
            std::memcpy(save_iv, iv, 16); std::memcpy(save_key, key, 32);
            std::memcpy(iv, aes::NIST_IV, 16); std::memcpy(key, aes::NIST_KEY, 32);
            const aes::RoundKeys KN = aes::expand_key(key);
            aesx::VKey vk0 = vk; aesx::NiKey nk0 = nk; aes::TTKey tk0 = tk;
            vk = aesx::v_key(KN); nk = aesx::ni_key(KN); tk = aes::tt_key(KN);
            size_t seg0 = seg; seg = 256;
            run(aes::NIST_PT, ct, 64);
            bool kat = std::memcmp(ct, aes::NIST_CT, 64) == 0;
            seg = seg0; vk = vk0; nk = nk0; tk = tk0;
            std::memcpy(iv, save_iv, 16); std::memcpy(key, save_key, 32);
            rep.set("kat_ok", kat ? 1 : 0);
            if (!kat) rc = 2;
        }

        // ---- buffers + input ----
        const double t_setup0 = bench::now_s();
        aesio::Fd fin;
        size_t n;
        if (noio) {
            n = static_cast<size_t>(a.i64("size-mb", 256)) * MiB;
        } else {
            fin = aesio::open_in(in_path);
            n = static_cast<size_t>(aesio::fd_size(fin));
        }
        if (n == 0) throw std::runtime_error("empty input");
        bench::AlignedBuf<uint8_t> bin(n), bout(n);
        par_segments(n, seg, [&](size_t off, size_t len) {   // NUMA first touch, same schedule as compute
            std::memset(bin.data() + off, 0, len);
            std::memset(bout.data() + off, 0, len);
        });
        rep.set("t_setup_s", bench::now_s() - t_setup0);
        double t_read = 0;
        if (noio) {
            const double t0 = bench::now_s();
            // every chunk-mb chunk holds pattern(0..len), same data as the FPGA host --no-io
            par_segments(n, seg, [&](size_t off, size_t len) {
                for (size_t i = 0; i < len;) {
                    const size_t rel = (off + i) % chunk, m = std::min(len - i, chunk - rel);
                    aesio::fill_pattern(bin.data() + off + i, m, aesio::NOIO_SEED, rel);
                    i += m;
                }
            });
            rep.set("t_gen_s", bench::now_s() - t0);
        } else {
            const double t0 = bench::now_s();
            for (size_t off = 0; off < n; off += chunk) aesio::pread_full(fin, bin.data() + off, std::min(chunk, n - off), off);
            t_read = bench::now_s() - t0;
        }
        // warm-up on one segment (code/tables paged in), output overwritten below
        run(bin.data(), bout.data(), std::min(n, seg));

        // ---- measured section ----
        const int repeat = std::max<int>(1, static_cast<int>(a.i64("repeat", 1)));
        bench::mark("start");
        const double tc0 = bench::now_s();
        for (int r = 0; r < repeat; ++r) run(bin.data(), bout.data(), n);   // same input -> same output
        const double t_compute = (bench::now_s() - tc0) / repeat;
        bench::mark("end");
        rep.set("repeat", repeat);

        double t_write = 0;
        if (!out_path.empty()) {
            const double t0 = bench::now_s();
            aesio::Fd fo = aesio::open_out(out_path, n);
            for (size_t off = 0; off < n; off += chunk) aesio::pwrite_full(fo, bout.data() + off, std::min(chunk, n - off), off);
            if (a.has("fsync")) aesio::fsync_fd(fo);
            t_write = bench::now_s() - t0;
        }

        // ---- verification (untimed) ----
        int verified = 0, ok = rc == 0 ? 1 : 0;
        if (a.has("verify")) {
            bench::AlignedBuf<uint8_t> ref(n);
            bool diff = false;
            const aes::TTKey tkv = aes::tt_key(K);
            par_segments(n, seg, [&](size_t off, size_t len) {
                if (impl == "ttable") aes::ctr_ref(K, iv, off / 16, bin.data() + off, ref.data() + off, len);
                else aes::ctr_ttable(tkv, iv, off / 16, bin.data() + off, ref.data() + off, len);
            });
            diff = std::memcmp(ref.data(), bout.data(), n) != 0;
            verified = 1;
            if (diff) { ok = 0; rc = 3; }
            rep.set("verify_ref", impl == "ttable" ? "textbook" : "ttable");
        }
        if (a.has("checksum")) rep.set("out_fnv1a64", bench::hex64(bench::fnv1a64(bout.data(), n)));

        rep.set("project", "aes");
        rep.set("platform", "cpu");
        rep.set("impl", impl);
        rep.set("mode", mode);
        rep.set("isa", isa);
        rep.set("threads", threads);
        rep.set("io", noio ? 0 : 1);
        rep.set("bytes", static_cast<unsigned long long>(n));
        rep.set("size_mb", static_cast<double>(n) / MiB);
        rep.set("chunk_mb", static_cast<unsigned long long>(chunk / MiB));
        rep.set("seg_kb", static_cast<unsigned long long>(seg / 1024));
        rep.set("t_read_s", t_read);
        rep.set("t_compute_s", t_compute);
        rep.set("t_write_s", t_write);
        rep.set("throughput_gbps", n / t_compute / 1e9);
        rep.set("e2e_gbps", n / (t_read + t_compute + t_write) / 1e9);
        rep.set("verified", verified);
        rep.set("openssl_version", OpenSSL_version(OPENSSL_VERSION));
        rep.set("ok", ok);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ERROR: %s\n", e.what());
        rep.set("project", "aes");
        rep.set("platform", "cpu");
        rep.set("impl", a.str("impl", "vaes"));
        rep.set("error", e.what());
        rep.set("ok", 0);
        rc = 1;
    }
    rep.set("t_total_s", bench::now_s() - t_start);
    rep.print();
    return rc;
}

// conv_cpu.cpp - CPU baselines for the v2 3x3 convolution benchmark.
//   --impl avx512 : hand-written AVX-512BW, row-parallel OpenMP, 3 filters in one pass per row
//   --impl opencv : cv::filter2D (IPP when OpenCV is built with it), band-parallel OpenMP
//   --impl ref    : scalar reference model, row-parallel OpenMP
// Same CLI, I/O code, checksums and RESULT keys as host/conv_fpga.cpp.
#include <omp.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include "bench_common.hpp"
#include "conv_avx512.hpp"
#include "conv_ref.hpp"
#include "img_io.hpp"

using namespace conv;

static void usage() {
    std::puts(
        "conv_cpu - CPU 3x3 convolution baseline (sharpen, edge, blur)\n"
        "  --impl avx512|opencv|ref     implementation (default avx512)\n"
        "  --variant rgb16|rgb8|gray8   data format (default rgb8)\n"
        "  --in IMAGE                   .png, or raw interleaved samples (needs --width --height)\n"
        "  --synthetic WxH              generated input instead of --in\n"
        "  --out-prefix P               write P_sharpen/P_edge/P_blur (.png, or .raw with --out-format raw)\n"
        "  --out-format png|raw         (default png)   --png-level N  zlib level (default 1)\n"
        "  --repeat N                   repeat the compute section N times (default 1)\n"
        "  --threads N                  OpenMP threads (default omp_get_max_threads())\n"
        "  --bands-per-thread N         opencv: bands per thread (default 4)\n"
        "  --verify                     compare with the scalar reference (untimed)\n"
        "RESULT keys: project platform impl isa variant width height channels bits mpix threads repeat\n"
        "  t_alloc_s t_read_s t_compute_s t_compute_min_s t_write_s t_cksum_s t_verify_s t_total_s\n"
        "  mpix_per_s cks_sharpen cks_edge cks_blur verify ok [cv_ipp cv_version]");
}

struct CpuImage {
    ImageSpec sp;
    bench::AlignedBuf<uint8_t> buf;
    std::vector<const uint8_t*> rows;
    uint8_t* row(int y) { return buf.data() + (size_t)y * sp.row_bytes(); }
    // allocate + first-touch in parallel with the compute schedule (rows, static)
    void alloc(const ImageSpec& s) {
        sp = s;
        buf.alloc(std::max<size_t>(1, sp.row_bytes() * sp.h));
        const size_t rb = sp.row_bytes();
        uint8_t* p = buf.data();
        #pragma omp parallel for schedule(static)
        for (int y = 0; y < sp.h; ++y) std::memset(p + (size_t)y * rb, 0, rb);
        rows.resize(sp.h);
        for (int y = 0; y < sp.h; ++y) rows[y] = row(y);
    }
};

static uint8_t* src_row(void* ctx, int y) { return static_cast<CpuImage*>(ctx)->row(y); }

// ---------------------------------------------------------------- OpenCV baseline
struct CvState {
    cv::Mat src, dst[3], tmp;
    cv::Mat k_sh, k_ed, k_bl;
    int bands = 1;
};

static void cv_setup(CvState& st, CpuImage& in, CpuImage* out[3], int threads, int bpt) {
    const ImageSpec& sp = in.sp;
    const int type = CV_MAKETYPE(sp.bps == 2 ? CV_16U : CV_8U, sp.C);
    st.src = cv::Mat(sp.h, sp.w, type, in.buf.data(), sp.row_bytes());
    for (int j = 0; j < 3; ++j) st.dst[j] = cv::Mat(sp.h, sp.w, type, out[j]->buf.data(), sp.row_bytes());
    // unnormalised blur: 8-bit -> CV_16S (max 4080), 16-bit -> CV_32F (max 1048560, exact in float)
    st.tmp.create(sp.h, sp.w, CV_MAKETYPE(sp.bps == 2 ? CV_32F : CV_16S, sp.C));
    st.k_sh = (cv::Mat_<float>(3, 3) << 0, -1, 0, -1, 5, -1, 0, -1, 0);
    st.k_ed = (cv::Mat_<float>(3, 3) << -1, -1, -1, -1, 8, -1, -1, -1, -1);
    st.k_bl = (cv::Mat_<float>(3, 3) << 1, 2, 1, 2, 4, 2, 1, 2, 1);
    st.bands = std::max(1, std::min(sp.h - 2, threads * bpt));
    // first-touch tmp with the same band schedule as the compute loop
    const int H = sp.h, nb = st.bands;
    #pragma omp parallel for schedule(static)
    for (int b = 0; b < nb; ++b) {
        int y0 = 1 + (int)((long)(H - 2) * b / nb), y1 = 1 + (int)((long)(H - 2) * (b + 1) / nb);
        if (y1 > y0) st.tmp.rowRange(y0, y1).setTo(cv::Scalar::all(0));
    }
    if (H >= 1) { st.tmp.row(0).setTo(cv::Scalar::all(0)); st.tmp.row(H - 1).setTo(cv::Scalar::all(0)); }
}

static void cv_compute(CvState& st, const ImageSpec& sp) {
    const int H = sp.h, W = sp.w, nb = st.bands, depth = sp.bps == 2 ? CV_16U : CV_8U;
    const int tdepth = sp.bps == 2 ? CV_32F : CV_16S;
    if (H < 3 || W < 3) return;   // outputs stay all-zero
    #pragma omp parallel for schedule(static)
    for (int b = 0; b < nb; ++b) {
        int y0 = 1 + (int)((long)(H - 2) * b / nb), y1 = 1 + (int)((long)(H - 2) * (b + 1) / nb);
        if (y1 <= y0) continue;
        // ROI bands: filter2D reads the rows above/below from the parent image (not isolated)
        cv::Mat s = st.src.rowRange(y0, y1);
        cv::Mat d0 = st.dst[0].rowRange(y0, y1), d1 = st.dst[1].rowRange(y0, y1), d2 = st.dst[2].rowRange(y0, y1);
        cv::Mat t = st.tmp.rowRange(y0, y1);
        // interior pixels never touch the border extrapolation; REPLICATE is the mode IPP accelerates
        cv::filter2D(s, d0, depth, st.k_sh, cv::Point(-1, -1), 0, cv::BORDER_REPLICATE);
        cv::filter2D(s, d1, depth, st.k_ed, cv::Point(-1, -1), 0, cv::BORDER_REPLICATE);
        cv::filter2D(s, t, tdepth, st.k_bl, cv::Point(-1, -1), 0, cv::BORDER_REPLICATE);
        // floor(sum/16) == round(sum/16 - 7.5/16) exactly (all values exact in float)
        t.convertTo(d2, depth, 1.0 / 16.0, -7.5 / 16.0);
        // zero 1-pixel left/right border
        for (cv::Mat* d : {&d0, &d1, &d2}) { d->col(0).setTo(cv::Scalar::all(0)); d->col(W - 1).setTo(cv::Scalar::all(0)); }
    }
}

int main(int argc, char** argv) {
    const double t_start = bench::now_s();
    bench::Args a(argc, argv);
    bench::Report rep;
    if (a.has("help") || a.has("h")) { usage(); return 0; }
    int ok = 1;
    try {
        const std::string impl = a.str("impl", "avx512");
        if (impl != "avx512" && impl != "opencv" && impl != "ref") throw std::runtime_error("--impl avx512|opencv|ref");
        const VariantInfo vi = variant_info(a.str("variant", "rgb8"));
        const int threads = (int)a.i64("threads", omp_get_max_threads());
        const int repeat = std::max(1, (int)a.i64("repeat", 1));
        omp_set_num_threads(threads);
        rep.set("project", "conv"); rep.set("platform", "cpu"); rep.set("impl", impl);
        rep.set("isa", impl == "avx512" ? row_isa() : (impl == "ref" ? "scalar" : "opencv"));
        rep.set("variant", vi.name); rep.set("threads", threads); rep.set("repeat", repeat);

        Source src; src.open(a, vi);
        const ImageSpec sp = src.spec;
        rep.set("width", sp.w); rep.set("height", sp.h); rep.set("channels", sp.C); rep.set("bits", sp.bps * 8);
        rep.set("mpix", sp.mpix()); rep.set("input", src.label());
        rep.set("bytes_in", (unsigned long long)(sp.row_bytes() * sp.h));
        rep.set("bytes_out", (unsigned long long)(3 * sp.row_bytes() * sp.h));

        CpuImage in, o[3];
        CpuImage* outs[3] = {&o[0], &o[1], &o[2]};
        CvState cvs;
        {
            bench::Timer t(rep, "t_alloc_s");
            in.alloc(sp); for (auto& x : o) x.alloc(sp);
            if (impl == "opencv") {
                cv::setNumThreads(1);   // we parallelise bands ourselves (filter2D is single-threaded)
                cv_setup(cvs, in, outs, threads, (int)a.i64("bands-per-thread", 4));
                rep.set("cv_ipp", (int)cv::ipp::useIPP());
                rep.set("cv_version", CV_VERSION);
                rep.set("cv_bands", cvs.bands);
            }
        }
        { bench::Timer t(rep, "t_read_s"); src.fill(src_row, &in, threads); }

        // ------------------------------------------------ measured section
        const int H = sp.h, W = sp.w, C = sp.C, bps = sp.bps;
        std::vector<double> tc;
        bench::mark("start");
        for (int it = 0; it < repeat; ++it) {
            const double t0 = bench::now_s();
            if (impl == "opencv") {
                cv_compute(cvs, sp);
            } else if (H >= 3) {
                const bool fast = impl == "avx512";
                #pragma omp parallel for schedule(static)
                for (int y = 1; y < H - 1; ++y) {
                    if (fast) row_fast(in.rows[y - 1], in.rows[y], in.rows[y + 1], o[0].row(y), o[1].row(y), o[2].row(y), W, C, bps);
                    else ref_row_bytes(in.rows[y - 1], in.rows[y], in.rows[y + 1], o[0].row(y), o[1].row(y), o[2].row(y), W, C, bps);
                }
            }
            tc.push_back(bench::now_s() - t0);
        }
        bench::mark("end");
        double tmean = 0; for (double x : tc) tmean += x; tmean /= tc.size();
        rep.set("t_compute_s", tmean);
        rep.set("t_compute_min_s", *std::min_element(tc.begin(), tc.end()));
        rep.set("mpix_per_s", tmean > 0 ? sp.mpix() / tmean : 0.0);

        const std::vector<const uint8_t*>* rows[3] = {&o[0].rows, &o[1].rows, &o[2].rows};
        {
            bench::Timer t(rep, "t_write_s");
            if (a.has("out-prefix"))
                write3(a.str("out-prefix"), a.str("out-format", "png"), (int)a.i64("png-level", 1), rows, sp);
        }
        {
            bench::Timer t(rep, "t_cksum_s");
            uint64_t ck[3]; checksum3(rows, sp, ck);
            rep.set("cks_sharpen", bench::hex64(ck[0])); rep.set("cks_edge", bench::hex64(ck[1])); rep.set("cks_blur", bench::hex64(ck[2]));
        }
        if (a.has("verify")) {
            bench::Timer t(rep, "t_verify_s");
            long bad = 0;
            const size_t rb = sp.row_bytes();
            #pragma omp parallel reduction(+:bad)
            {
                std::vector<uint8_t> r0(rb + 1), r1(rb + 1), r2(rb + 1), z(rb + 1, 0);
                #pragma omp for schedule(static)
                for (int y = 0; y < H; ++y) {
                    if (y == 0 || y == H - 1 || H < 3) { for (int j = 0; j < 3; ++j) bad += std::memcmp(o[j].rows[y], z.data(), rb) != 0; continue; }
                    ref_row_bytes(in.rows[y - 1], in.rows[y], in.rows[y + 1], r0.data(), r1.data(), r2.data(), W, C, bps);
                    bad += std::memcmp(r0.data(), o[0].rows[y], rb) != 0;
                    bad += std::memcmp(r1.data(), o[1].rows[y], rb) != 0;
                    bad += std::memcmp(r2.data(), o[2].rows[y], rb) != 0;
                }
            }
            rep.set("verify_bad_rows", bad);
            rep.set("verify", bad == 0 ? 1 : 0);
            if (bad) { ok = 0; std::fprintf(stderr, "VERIFY FAILED: %ld mismatching rows\n", bad); }
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ERROR: %s\n", e.what());
        ok = 0;
    }
    rep.set("ok", ok);
    rep.set("t_total_s", bench::now_s() - t_start);
    rep.print();
    return ok ? 0 : 1;
}

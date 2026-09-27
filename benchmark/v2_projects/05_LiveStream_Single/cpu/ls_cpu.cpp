// ls_cpu.cpp - v2 CPU baseline for the single-output live-stream benchmark.
// Same pipeline code as the FPGA host (common/ls_pipeline.hpp); the resize stage is
// OpenCV cv::resize(INTER_LINEAR) (universal intrinsics AVX2/AVX-512, IPP if the OpenCV
// build has it: see the cv_ipp result key). `--workers` frame-parallel resize threads,
// each call internally parallelised by OpenCV's thread pool (`--cv-threads`).
#include <thread>
#include "ls_pipeline.hpp"
#include "ls_verify.hpp"

class CpuResizer : public ls::Resizer {
    std::vector<bench::AlignedBuf<uint8_t>> in_, out_;
    int w_, spw_;
public:
    CpuResizer(int workers, int spw) : w_(workers), spw_(spw) {
        const size_t in_sz = ls::padded_bytes(LS_MAX_IN_W, LS_MAX_IN_H);
        const size_t out_sz = ls::padded_bytes(LS_MAX_OUT_W, LS_MAX_OUT_H);
        for (int s = 0; s < w_ * spw_; s++) {
            in_.emplace_back(in_sz);
            out_.emplace_back(out_sz);
            std::memset(in_.back().data(), 0, in_sz);
            std::memset(out_.back().data(), 0, out_sz);
        }
    }
    int workers() const override { return w_; }
    int slots_per_worker() const override { return spw_; }
    uint8_t* in_buf(int s) override { return in_[s].data(); }
    uint8_t* out_buf(int s) override { return out_[s].data(); }
    void process(int, int slot, const ls::Geometry& g, ls::StageTimes& t) override {
        double a = bench::now_s();
        cv::Mat src(g.in_h, g.in_w, CV_8UC3, in_[slot].data());
        cv::Mat dst(g.out_h, g.out_w, CV_8UC3, out_[slot].data());
        cv::resize(src, dst, cv::Size(g.out_w, g.out_h), 0, 0, cv::INTER_LINEAR);
        if (dst.data != out_[slot].data()) throw std::runtime_error("cv::resize reallocated the output");
        t.resize = bench::now_s() - a;
    }
};

int main(int argc, char** argv) {
    const double T0 = bench::now_s();
    bench::Report R;
    bench::Args a(argc, argv);
    if (a.has("help") || argc < 2) {
        ls::Options::usage(argv[0], "[--threads N] [--cv-threads N]");
        return a.has("help") ? 0 : 1;
    }
    int rc = 0;
    try {
        ls::Options o;
        o.parse(a);
        int hw = (int)std::thread::hardware_concurrency();
        int threads = (int)a.f64("threads", bench::env_int("OMP_NUM_THREADS", hw));
        int cv_threads = (int)a.f64("cv-threads", threads);
        cv::setNumThreads(cv_threads);
        int workers = o.workers > 0 ? o.workers : std::min(4, std::max(1, threads));
        R.set("platform", "cpu");
        R.set("impl", "opencv_resize_linear");
        R.set("threads", threads);
        R.set("isa", cv::checkHardwareSupport(CV_CPU_AVX512_SKX) ? "avx512" : (cv::checkHardwareSupport(CV_CPU_AVX2) ? "avx2" : "sse"));
        CpuResizer rz(workers, o.slots_per_worker);
        long n = o.resize_only ? ls::run_resize_only(o, rz, R, nullptr) : ls::run_pipeline(o, rz, R, nullptr);
        bool ok = n >= 0;
        if (o.verify) {
            cv::VideoCapture cap(o.video, cv::CAP_FFMPEG);
            ls::Geometry g{(int)cap.get(cv::CAP_PROP_FRAME_WIDTH), (int)cap.get(cv::CAP_PROP_FRAME_HEIGHT),
                           ls::kQualities[o.quality].w, ls::kQualities[o.quality].h};
            ok &= ls::verify_slots(rz, g, true, R);
        }
        R.set("ok", ok ? 1 : 0);
        rc = ok ? 0 : 2;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ERROR: %s\n", e.what());
        R.set("ok", 0);
        R.set("error", e.what());
        rc = 1;
    }
    R.set("t_total_s", bench::now_s() - T0);
    R.print();
    return rc;
}

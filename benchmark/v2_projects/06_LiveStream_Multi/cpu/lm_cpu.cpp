// lm_cpu.cpp - v2 CPU baseline for the multi-output (ABR ladder) live-stream benchmark.
// Same pipeline code as the FPGA host. Ladder stage = cv::resize(INTER_LINEAR) for every
// (frame, rung) of a job; the 5*B resizes of a job run in parallel on OpenCV's pool
// (`--rung-parallel 1`, default), `--workers` jobs run concurrently.
#include <thread>
#include "lm_pipeline.hpp"

class CpuLadder : public lm::Resizer {
    std::vector<bench::AlignedBuf<uint8_t>> in_, out_;
    int w_, spw_, batch_;
    bool rung_par_;
public:
    CpuLadder(int workers, int spw, int batch, bool rung_par) : w_(workers), spw_(spw), batch_(batch), rung_par_(rung_par) {
        const size_t in_sz = batch_ * lm::in_words(LM_MAX_IN_W, LM_MAX_IN_H) * LM_WORD_BYTES;
        const size_t out_sz = batch_ * lm::out_words_per_frame() * LM_WORD_BYTES;
        for (int s = 0; s < w_ * spw_; s++) {
            in_.emplace_back(in_sz);
            out_.emplace_back(out_sz);
            std::memset(in_.back().data(), 0, in_sz);
            std::memset(out_.back().data(), 0, out_sz);
        }
    }
    int workers() const override { return w_; }
    int slots_per_worker() const override { return spw_; }
    int batch() const override { return batch_; }
    uint8_t* in_buf(int s) override { return in_[s].data(); }
    uint8_t* out_buf(int s) override { return out_[s].data(); }
    void process(int, int slot, const lm::Geometry& g, int n, lm::StageTimes& t) override {
        double a = bench::now_s();
        const size_t in_step = lm::in_words(g.in_w, g.in_h) * LM_WORD_BYTES;
        auto one = [&](int task) {
            int f = task / LM_NOUT, k = task % LM_NOUT;
            const lm::Rung& r = lm::kRungs[k];
            cv::Mat src(g.in_h, g.in_w, CV_8UC3, in_[slot].data() + f * in_step);
            cv::Mat dst(r.h, r.w, CV_8UC3, (void*)lm::rung_ptr(*this, slot, f, k));
            cv::resize(src, dst, cv::Size(r.w, r.h), 0, 0, cv::INTER_LINEAR);
        };
        if (rung_par_)
            cv::parallel_for_(cv::Range(0, n * LM_NOUT), [&](const cv::Range& rg) { for (int i = rg.start; i < rg.end; i++) one(i); });
        else
            for (int i = 0; i < n * LM_NOUT; i++) one(i);
        t.resize = bench::now_s() - a;
    }
};

int main(int argc, char** argv) {
    const double T0 = bench::now_s();
    bench::Report R;
    bench::Args a(argc, argv);
    if (a.has("help") || argc < 2) {
        lm::Options::usage(argv[0], "[--threads N] [--cv-threads N] [--rung-parallel 0|1]");
        return a.has("help") ? 0 : 1;
    }
    int rc = 0;
    try {
        lm::Options o;
        o.parse(a);
        int hw = (int)std::thread::hardware_concurrency();
        int threads = (int)a.f64("threads", bench::env_int("OMP_NUM_THREADS", hw));
        cv::setNumThreads((int)a.f64("cv-threads", threads));
        int workers = o.workers > 0 ? o.workers : std::min(4, std::max(1, threads));
        bool rung_par = a.f64("rung-parallel", 1) != 0;
        R.set("platform", "cpu");
        R.set("impl", "opencv_resize_linear");
        R.set("threads", threads);
        R.set("rung_parallel", rung_par ? 1 : 0);
        R.set("isa", cv::checkHardwareSupport(CV_CPU_AVX512_SKX) ? "avx512" : (cv::checkHardwareSupport(CV_CPU_AVX2) ? "avx2" : "sse"));
        lm::Geometry g = lm::probe(o.video, nullptr, nullptr);
        CpuLadder rz(workers, o.slots_per_worker, o.batch, rung_par);
        long n = o.resize_only ? lm::run_resize_only(o, rz, R) : lm::run_pipeline(o, rz, R);
        bool ok = n >= 0;
        if (o.verify) ok &= lm::verify_slots(rz, g, o.batch, true, R);
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

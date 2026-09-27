// lm_fpga.cpp - v2 FPGA host for the multi-output (ABR ladder) live-stream benchmark.
// Pipeline, encoders and timers: common/lm_pipeline.hpp (shared with the CPU main).
// Per job (1..LM_MAX_BATCH frames): 1 H2D, 1 kernel start, 1 D2H. The same output BO is
// passed to the kernel's 5 write masters (all connected to one HBM bank).
#include <sys/mman.h>
#include <cerrno>
#include <cstring>
#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>
#include "lm_pipeline.hpp"

static const char* KNAME = "lm_ladder";

class FpgaLadder : public lm::Resizer {
    struct Slot { xrt::bo in, out; xrt::run run; uint8_t* pin = nullptr; uint8_t* pout = nullptr; };
    xrt::device dev_;
    xrt::uuid uuid_;
    std::vector<xrt::kernel> cus_;
    std::vector<Slot> slots_;
    int spw_, batch_;
public:
    FpgaLadder(int device, const std::string& xclbin, int want_cus, int spw, int batch, bench::Report& R)
        : spw_(spw), batch_(batch) {
        {
            bench::Timer t(R, "t_xclbin_s");
            dev_ = xrt::device((unsigned)device);
            uuid_ = dev_.load_xclbin(xclbin);
        }
        bench::Timer t(R, "t_alloc_s");
        const int max_probe = want_cus > 0 ? want_cus : 16;
        for (int i = 1; i <= max_probe; i++) {
            std::string name = std::string(KNAME) + ":{" + KNAME + "_" + std::to_string(i) + "}";
            try { cus_.emplace_back(dev_, uuid_, name); }
            catch (const std::exception& e) {
                if (want_cus > 0) throw std::runtime_error("cannot open CU " + name + ": " + e.what());
                break;
            }
        }
        if (cus_.empty()) throw std::runtime_error("no lm_ladder CU found in " + xclbin);
        const size_t in_sz = batch_ * lm::in_words(LM_MAX_IN_W, LM_MAX_IN_H) * LM_WORD_BYTES;
        const size_t out_sz = batch_ * lm::out_words_per_frame() * LM_WORD_BYTES;
        for (size_t c = 0; c < cus_.size(); c++)
            for (int k = 0; k < spw_; k++) {
                Slot s;
                try {
                    s.in = xrt::bo(dev_, in_sz, xrt::bo::flags::normal, cus_[c].group_id(0));
                    s.out = xrt::bo(dev_, out_sz, xrt::bo::flags::normal, cus_[c].group_id(1));
                } catch (const std::exception& e) {
                    throw std::runtime_error(std::string("BO allocation failed (HBM bank full?): ") + e.what());
                }
                s.pin = s.in.map<uint8_t*>();
                s.pout = s.out.map<uint8_t*>();
                // The ffmpeg sink is started with fork().  Without MADV_DONTFORK the mapped
                // buffer objects become copy-on-write in the parent, so the host writes to
                // private copies while the DMA engine keeps using the original pages: the
                // kernel then reads zeros and the host never sees what the device wrote
                // (measured as an all-zero pipeline output).  Keep these pages out of fork.
                if (madvise(s.pin, in_sz, MADV_DONTFORK) || madvise(s.pout, out_sz, MADV_DONTFORK))
                    std::fprintf(stderr, "warning: madvise(MADV_DONTFORK) failed: %s\n", std::strerror(errno));
                std::memset(s.pin, 0, in_sz);
                std::memset(s.pout, 0, out_sz);
                s.run = xrt::run(cus_[c]);
                s.run.set_arg(0, s.in);
                for (int a = 1; a <= 5; a++) s.run.set_arg(a, s.out);   // same BO on all 5 write masters
                slots_.push_back(std::move(s));
            }
        R.set("n_cu", (int)cus_.size());
        R.set("bo_in_bytes", (unsigned long long)in_sz);
        R.set("bo_out_bytes", (unsigned long long)out_sz);
    }
    int workers() const override { return (int)cus_.size(); }
    int slots_per_worker() const override { return spw_; }
    int batch() const override { return batch_; }
    uint8_t* in_buf(int s) override { return slots_[s].pin; }
    uint8_t* out_buf(int s) override { return slots_[s].pout; }
    void process(int, int slot, const lm::Geometry& g, int n, lm::StageTimes& t) override {
        Slot& s = slots_[slot];
        const size_t nin = n * lm::in_words(g.in_w, g.in_h) * LM_WORD_BYTES;
        const size_t nout = n * lm::out_words_per_frame() * LM_WORD_BYTES;
        double a = bench::now_s();
        s.in.sync(XCL_BO_SYNC_BO_TO_DEVICE, nin, 0);
        double b = bench::now_s();
        s.run.set_arg(6, g.in_h);
        s.run.set_arg(7, g.in_w);
        s.run.set_arg(8, n);
        s.run.start();
        auto st = s.run.wait();
        if (st != ERT_CMD_STATE_COMPLETED) throw std::runtime_error("kernel run failed, state " + std::to_string((int)st));
        double c = bench::now_s();
        s.out.sync(XCL_BO_SYNC_BO_FROM_DEVICE, nout, 0);
        double d = bench::now_s();
        t.h2d = b - a; t.kernel = c - b; t.d2h = d - c; t.resize = d - a;
    }
};

int main(int argc, char** argv) {
    const double T0 = bench::now_s();
    bench::Report R;
    bench::Args a(argc, argv);
    if (a.has("help") || argc < 2) {
        lm::Options::usage(argv[0], "--xclbin X [--device N] [--cus K]");
        return a.has("help") ? 0 : 1;
    }
    int rc = 0;
    try {
        lm::Options o;
        o.parse(a);
        o.fpga_markers = true;
        const std::string xclbin = a.str("xclbin");
        if (xclbin.empty()) throw std::runtime_error("--xclbin is required");
        R.set("platform", "fpga");
        R.set("impl", "vitis_vision_ladder");
        R.set("xclbin", xclbin);
        lm::Geometry g = lm::probe(o.video, nullptr, nullptr);
        int cus = a.has("cus") ? (int)a.f64("cus", 0) : (o.workers > 0 ? o.workers : 0);
        FpgaLadder rz((int)a.i64("device", 0), xclbin, cus, o.slots_per_worker, o.batch, R);
        long n = o.resize_only ? lm::run_resize_only(o, rz, R) : lm::run_pipeline(o, rz, R);
        bool ok = n >= 0;
        if (o.verify) ok &= lm::verify_slots(rz, g, o.batch, false, R);
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

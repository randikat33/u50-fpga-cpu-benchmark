// tb_kernel.cpp - C-simulation testbench for lm_ladder.
//  * every rung of every frame == Vitis Vision resize driven directly with MAX_DOWN_SCALE=16
//    (v1 setting): proves the gearboxes, the fan-out, the block layout and the per-rung
//    reduced MAX_DOWN_SCALE are all exact;
//  * batches of 1..3 frames, several input sizes, output padding zero, no writes outside
//    the block, invalid parameters leave the buffer untouched;
//  * quality vs cv::resize(INTER_LINEAR).
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <vector>
#include <opencv2/opencv.hpp>
#include "lm_ladder.h"

static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { fails++; std::printf("FAIL: " __VA_ARGS__); std::printf("\n"); } } while (0)

template <int OH, int OW>
static void lib_ref_rung(const cv::Mat& in, cv::Mat& out) {
    lm_in_mat_t im(in.rows, in.cols);
    xf::cv::Mat<XF_8UC3, OH, OW, XF_NPPC8, LM_DEPTH_OUT> om(out.rows, out.cols);
    const uint8_t* p = in.data;
    for (int i = 0; i < in.rows * in.cols / 8; i++, p += 24) {
        lm_pack_t g = 0;
        for (int b = 0; b < 24; b++) g.range(8 * b + 7, 8 * b) = p[b];
        im.write(i, g);
    }
#if LS_RESIZE_HAS_URAM_ARG
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LM_MAX_IN_H, LM_MAX_IN_W, OH, OW, XF_NPPC8, true, 16,
                   LM_DEPTH_IN, LM_DEPTH_OUT>(im, om);
#else
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LM_MAX_IN_H, LM_MAX_IN_W, OH, OW, XF_NPPC8, 16, LM_DEPTH_IN,
                   LM_DEPTH_OUT>(im, om);
#endif
    uint8_t* q = out.data;
    for (int i = 0; i < out.rows * out.cols / 8; i++, q += 24) {
        lm_pack_t g = om.read(i);
        for (int b = 0; b < 24; b++) q[b] = (uint8_t)g.range(8 * b + 7, 8 * b);
    }
}
static void lib_ref(const cv::Mat& in, int k, cv::Mat& out) {
    out.create(lm::kRungs[k].h, lm::kRungs[k].w, CV_8UC3);
    switch (k) {
        case 0: lib_ref_rung<LM_H0, LM_W0>(in, out); break;
        case 1: lib_ref_rung<LM_H1, LM_W1>(in, out); break;
        case 2: lib_ref_rung<LM_H2, LM_W2>(in, out); break;
        case 3: lib_ref_rung<LM_H3, LM_W3>(in, out); break;
        default: lib_ref_rung<LM_H4, LM_W4>(in, out); break;
    }
}

static cv::Mat make_img(int w, int h, int kind, uint32_t seed) {
    cv::Mat m(h, w, CV_8UC3);
    std::mt19937 rng(seed);
    for (int y = 0; y < h; y++) {
        uint8_t* p = m.ptr<uint8_t>(y);
        for (int x = 0; x < w; x++)
            for (int c = 0; c < 3; c++)
                p[3 * x + c] = kind == 0 ? (uint8_t)rng() : kind == 1 ? (uint8_t)((x * (c + 1) + y * 3 + seed) & 0xff)
                                                            : (uint8_t)((((x >> 4) + (y >> 4) + c + seed) & 1) ? 255 : 0);
    }
    if (kind == 3) cv::GaussianBlur(make_img(w, h, 0, seed), m, cv::Size(0, 0), 3.0);
    return m;
}

static void to_words(const cv::Mat& m, lm_word_t* w) {
    size_t n = lm::in_words(m.cols, m.rows);
    std::vector<uint8_t> buf(n * 64, 0);
    std::memcpy(buf.data(), m.data, lm::frame_bytes(m.cols, m.rows));
    for (size_t i = 0; i < n; i++) std::memcpy((void*)&w[i], &buf[i * 64], 64);
}

static void run_case(int iw, int ih, int nf, int kind, bool vs_cv) {
    std::printf("  case %dx%d x%d kind %d\n", iw, ih, nf, kind); std::fflush(stdout);
    size_t niw = lm::in_words(iw, ih), nwf = lm::out_words_per_frame();
    std::vector<lm_word_t> src(niw * nf);
    std::vector<cv::Mat> ins;
    for (int f = 0; f < nf; f++) { ins.push_back(make_img(iw, ih, kind, 7 * f + kind)); to_words(ins[f], &src[niw * f]); }
    std::vector<lm_word_t> out(nwf * nf + 4);
    lm_word_t ones = 0; ones = ~ones;
    for (auto& w : out) w = ones;
    lm_word_t* o = out.data();
    lm_ladder(src.data(), o, o, o, o, o, ih, iw, nf);
    CHECK(out[nwf * nf] == ones && out[nwf * nf + 3] == ones, "wrote past the block");
    bool all = true;
    for (int f = 0; f < nf; f++)
        for (int k = 0; k < LM_NOUT; k++) {
            const auto& R = lm::kRungs[k];
            size_t off = f * nwf + lm::rung_offset(k), nw = lm::rung_words(k), fb = lm::frame_bytes(R.w, R.h);
            std::vector<uint8_t> raw(nw * 64);
            for (size_t i = 0; i < nw; i++) std::memcpy(&raw[i * 64], (void*)&out[off + i], 64);
            bool pad0 = true;
            for (size_t i = fb; i < raw.size(); i++) pad0 &= raw[i] == 0;
            CHECK(pad0, "frame %d rung %s padding not zero", f, R.name);
            cv::Mat ref;
            lib_ref(ins[f], k, ref);
            bool same = std::memcmp(ref.data, raw.data(), fb) == 0;
            all &= same;
            CHECK(same, "%dx%d frame %d rung %s != library reference (MAX_DOWN 16)", iw, ih, f, R.name);
            if (vs_cv && f == 0) {
                cv::Mat got(R.h, R.w, CV_8UC3, raw.data()), cvo, d;
                cv::resize(ins[f], cvo, cv::Size(R.w, R.h), 0, 0, cv::INTER_LINEAR);
                cv::absdiff(got, cvo, d);
                double mx; cv::minMaxLoc(d.reshape(1), nullptr, &mx);
                double psnr = mx == 0 ? 999 : cv::PSNR(got, cvo);
                std::printf("    %-5s vs cv::resize: max|diff| %.0f  PSNR %.1f dB\n", R.name, mx, psnr);
                CHECK(mx <= 2 && psnr > 40, "rung %s too far from OpenCV", R.name);
            }
        }
    std::printf("  ok %dx%d x%d kind %d (%s)\n", iw, ih, nf, kind, all ? "all rungs bit-exact" : "MISMATCH");
}

int main(int argc, char** argv) {
    bool full = argc > 1 && std::string(argv[1]) == "--full";
    run_case(1920, 1080, 1, 1, false);   // smallest legal input (1080p rung = copy)
    run_case(2048, 1090, 2, 0, false);   // odd height, batch of 2, noise
    run_case(2560, 1440, 1, 2, false);   // 2.5K checkerboard
    if (full) {
        run_case(3840, 2160, 3, 3, true);   // paper geometry, batch 3, smooth content
        run_case(3840, 2160, 1, 2, true);
    } else {
        run_case(3840, 2160, 1, 3, true);
    }
    {   // invalid parameters -> buffer untouched
        std::vector<lm_word_t> src(lm::in_words(1920, 1080)), out(64);
        const int bad[][3] = {{1080, 1920, 0}, {1080, 1920, 9}, {1080, 1912, 1}, {1072, 1920, 1}, {1080, 1924, 1},
                              {2168, 3840, 1}, {2160, 3848, 1}, {1, 1920, 1},   {1080, -1920, 1}};   // h, w, n
        for (auto& b : bad) {
            for (auto& w : out) w = 0x77;
            lm_word_t* o = out.data();
            lm_ladder(src.data(), o, o, o, o, o, b[0], b[1], b[2]);
            bool un = true;
            for (auto& w : out) un &= w == 0x77;
            CHECK(un, "invalid (h=%d w=%d n=%d) wrote to the output", b[0], b[1], b[2]);
            CHECK(!lm::dims_ok(b[1], b[0]) || b[2] < 1 || b[2] > LM_MAX_BATCH, "dims_ok disagrees with kernel for %dx%d",
                  b[1], b[0]);
        }
        CHECK(lm::dims_ok(3840, 2160) && lm::dims_ok(1920, 1080) && lm::dims_ok(2048, 1090), "dims_ok rejects legal sizes");
        std::printf("  ok invalid-parameter guard\n");
    }
    std::printf("tb_kernel: %s (%d failures)\n", fails ? "FAILED" : "PASSED", fails);
    return fails ? 1 : 0;
}

// tb_kernel.cpp - C-simulation testbench for ls_resize.
//  1. Gearbox check: kernel output == Vitis Vision resize driven directly pixel-by-pixel
//     (bit-exact), for many sizes incl. word/group remainders.
//  2. Padding bytes of the last output word are zero; dst beyond nwords untouched.
//  3. Invalid dimensions leave dst untouched.
//  4. Quality vs OpenCV cv::resize(INTER_LINEAR) (the CPU baseline): max/mean abs diff, PSNR.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <random>
#include <vector>
#include <opencv2/opencv.hpp>
#include "ls_resize.h"

static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { fails++; std::printf("FAIL: " __VA_ARGS__); std::printf("\n"); } } while (0)

// Direct library reference (no gearbox): pixels -> Mat -> resize -> pixels
static void lib_ref(const cv::Mat& in, cv::Mat& out) {
    ls_in_mat_t im(in.rows, in.cols);
    ls_out_mat_t om(out.rows, out.cols);
    const uint8_t* p = in.data;
    int ng = in.rows * in.cols / 8;
    for (int i = 0; i < ng; i++) {
        ls_pack_t g = 0;
        for (int b = 0; b < 24; b++) g.range(8 * b + 7, 8 * b) = p[b];
        p += 24;
        im.write(i, g);
    }
#if LS_RESIZE_HAS_URAM_ARG
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LS_MAX_IN_H, LS_MAX_IN_W, LS_MAX_OUT_H, LS_MAX_OUT_W,
                   XF_NPPC8, true, LS_MAX_DOWN, LS_DEPTH_IN, LS_DEPTH_OUT>(im, om);
#else
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LS_MAX_IN_H, LS_MAX_IN_W, LS_MAX_OUT_H, LS_MAX_OUT_W,
                   XF_NPPC8, LS_MAX_DOWN, LS_DEPTH_IN, LS_DEPTH_OUT>(im, om);
#endif
    uint8_t* q = out.data;
    int go = out.rows * out.cols / 8;
    for (int i = 0; i < go; i++) {
        ls_pack_t g = om.read(i);
        for (int b = 0; b < 24; b++) q[b] = (uint8_t)g.range(8 * b + 7, 8 * b);
        q += 24;
    }
}

static cv::Mat make_img(int w, int h, int kind, uint32_t seed) {
    cv::Mat m(h, w, CV_8UC3);
    std::mt19937 rng(seed);
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            for (int c = 0; c < 3; c++) {
                uint8_t v;
                if (kind == 0) v = (uint8_t)rng();                                    // noise
                else if (kind == 1) v = (uint8_t)((x * (c + 1) + y * 3) & 0xff);      // gradients
                else v = (uint8_t)((((x >> 3) + (y >> 3) + c) & 1) ? 255 : 0);        // checkerboard
                m.at<cv::Vec3b>(y, x)[c] = v;
            }
    if (kind == 3) { cv::GaussianBlur(make_img(w, h, 0, seed), m, cv::Size(0, 0), 3.0); }   // smooth "natural"
    return m;
}

static std::vector<ls_word_t> to_words(const cv::Mat& m) {
    size_t n = ls::words_for(ls::frame_bytes(m.cols, m.rows));
    std::vector<ls_word_t> w(n);
    std::vector<uint8_t> buf(n * 64, 0);
    std::memcpy(buf.data(), m.data, ls::frame_bytes(m.cols, m.rows));
    for (size_t i = 0; i < n; i++) std::memcpy((void*)&w[i], &buf[i * 64], 64);   // ap_uint<512> = 8 LE limbs
    return w;
}

static void run_case(int iw, int ih, int ow, int oh, int kind, bool vs_cv) {
    std::printf("  case %dx%d -> %dx%d kind %d\n", iw, ih, ow, oh, kind); std::fflush(stdout);
    cv::Mat in = make_img(iw, ih, kind, iw * 31 + ih + kind);
    std::vector<ls_word_t> src = to_words(in);
    size_t nw = ls::words_for(ls::frame_bytes(ow, oh));
    std::vector<ls_word_t> dst(nw + 2);
    for (auto& d : dst) { d = 0; d = ~d; }   // canary: all ones
    ls_resize(src.data(), dst.data(), ih, iw, oh, ow);
    std::vector<uint8_t> raw(nw * 64);
    for (size_t i = 0; i < nw; i++) std::memcpy(&raw[i * 64], (void*)&dst[i], 64);
    ls_word_t ones = 0; ones = ~ones;
    CHECK(dst[nw] == ones && dst[nw + 1] == ones, "%dx%d->%dx%d wrote past end", iw, ih, ow, oh);
    size_t fb = ls::frame_bytes(ow, oh);
    bool pad0 = true;
    for (size_t i = fb; i < raw.size(); i++) pad0 &= raw[i] == 0;
    CHECK(pad0, "%dx%d->%dx%d padding not zero", iw, ih, ow, oh);
    cv::Mat ref(oh, ow, CV_8UC3);
    lib_ref(in, ref);
    bool same = std::memcmp(ref.data, raw.data(), fb) == 0;
    CHECK(same, "%dx%d->%dx%d kind %d kernel != library reference", iw, ih, ow, oh, kind);
    if (vs_cv) {
        cv::Mat cvo;
        cv::resize(in, cvo, cv::Size(ow, oh), 0, 0, cv::INTER_LINEAR);
        cv::Mat kout(oh, ow, CV_8UC3, raw.data());
        cv::Mat diff; cv::absdiff(kout, cvo, diff);
        double mx; cv::minMaxLoc(diff.reshape(1), nullptr, &mx);
        double mean = cv::mean(diff)[0];
        double psnr = cv::PSNR(kout, cvo);
        std::printf("  quality %4dx%-4d <- %dx%d kind %d: max|diff| %3.0f  mean %.3f  PSNR %.1f dB\n", ow, oh, iw, ih,
                    kind, mx, mean, psnr);
        CHECK(psnr > 30.0, "%dx%d PSNR too low vs OpenCV (%.1f)", ow, oh, psnr);
    }
    std::printf("  ok %dx%d -> %dx%d kind %d (%s)\n", iw, ih, ow, oh, kind, same ? "bit-exact" : "MISMATCH");
}

int main(int argc, char** argv) {
    bool full = argc > 1 && std::string(argv[1]) == "--full";
    // small / remainder cases (groups % 8 and bytes % 64 take all values)
    const int sizes[][4] = {{8, 2, 8, 2},    {16, 3, 8, 2},    {24, 5, 8, 3},    {64, 9, 16, 7},
                            {96, 11, 24, 5}, {128, 17, 56, 9}, {200, 37, 96, 31}, {256, 130, 184, 77},
                            {320, 180, 40, 23}, {640, 360, 72, 41}, {504, 99, 504, 99}};
    for (auto& s : sizes)
        for (int kind = 0; kind < 3; kind++) run_case(s[0], s[1], s[2], s[3], kind, false);
    // invalid dimensions -> dst untouched
    {
        cv::Mat in = make_img(64, 8, 0, 1);
        auto src = to_words(in);
        std::vector<ls_word_t> dst(8);
        // (ih, iw, oh, ow): width not /8, zero, upscale, >9x downscale, 1-row, too large
        const int bad[][4] = {{8, 63, 8, 8}, {8, 64, 8, 7},  {8, 64, 0, 8},  {8, 64, 16, 128}, {8, 64, 8, 0},
                              {8, 80, 8, 8}, {20, 64, 2, 8}, {1, 64, 1, 8},  {8, 64, 1, 8},    {-8, 64, 8, 8},
                              {2161, 3840, 1080, 1920}, {2160, 3848, 1080, 1920}, {2160, 3840, 1080, 1928}};
        for (auto& b : bad) {
            for (auto& d : dst) d = 0x5a;
            ls_resize(src.data(), dst.data(), b[0], b[1], b[2], b[3]);
            bool un = true;
            for (auto& d : dst) un &= d == 0x5a;
            CHECK(un, "invalid dims %d %d %d %d modified dst", b[0], b[1], b[2], b[3]);
        }
        std::printf("  ok invalid-dimension guard\n");
    }
    // the 5 production qualities from 4K (smooth + checker content)
    const int nq = full ? 5 : 2;
    for (int q = 0; q < nq; q++) {
        const auto& Q = ls::kQualities[full ? q : (q == 0 ? 0 : 4)];
        run_case(LS_MAX_IN_W, LS_MAX_IN_H, Q.w, Q.h, 3, true);
    }
    if (full) run_case(LS_MAX_IN_W, LS_MAX_IN_H, 1920, 1080, 2, true);
    std::printf("tb_kernel: %s (%d failures)\n", fails ? "FAILED" : "PASSED", fails);
    return fails ? 1 : 0;
}

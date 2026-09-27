// tb_kernel.cpp - C-simulation testbench: kernel tops vs the scalar reference model.
// Covers all variants, minimum sizes, widths not divisible by the word size, the maximum
// width (line-buffer depth), a ~1.3 MP image, garbage in the row padding, multi-strip
// operation with 2-row overlap, back-to-back calls, and invalid parameters (no-op).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>
#include "conv3.hpp"
#include "word_io.hpp"
#include "conv_ref.hpp"
#include "conv_plan.hpp"
#include "img_io.hpp"

using top_fn = void (*)(const conv3::word_t*, conv3::word_t*, conv3::word_t*, conv3::word_t*,
                        unsigned, unsigned, unsigned, unsigned);
struct V { const char* name; top_fn f; int C, bps; };
static const V kV[] = {{"rgb16", conv3_rgb16, 3, 2}, {"rgb8", conv3_rgb8, 3, 1}, {"gray8", conv3_gray8, 1, 1}};

static int g_fail = 0, g_cases = 0;
static std::mt19937_64 rng(12345);

struct Img {
    conv::ImageSpec sp; std::vector<uint8_t> px;  // unpadded rows
    const uint8_t* row(int y) const { return px.data() + (size_t)y * sp.row_bytes(); }
};

static Img make_img(const V& v, int W, int H, int pattern) {
    Img im; im.sp = {W, H, v.C, v.bps};
    im.px.resize(im.sp.row_bytes() * H);
    for (int y = 0; y < H; ++y) {
        uint8_t* r = im.px.data() + (size_t)y * im.sp.row_bytes();
        if (pattern == 0) conv::synth_row(r, y, im.sp);
        else if (pattern == 1) for (size_t i = 0; i < im.sp.row_bytes(); ++i) r[i] = (uint8_t)rng();
        else if (pattern == 2) std::memset(r, 0xff, im.sp.row_bytes());               // all max
        else for (size_t i = 0; i < im.sp.row_bytes(); ++i) r[i] = ((i / v.bps + y) & 1) ? 0xff : 0; // checker
    }
    return im;
}

// Runs the kernel on input rows [y0, y0+n) and returns (n-2) output rows per filter (unpadded).
static bool run_kernel(const V& v, const Img& im, int y0, int n, std::vector<uint8_t> out[3], bool garbage_pad) {
    const size_t S = im.sp.stride_words(), rb = im.sp.row_bytes();
    std::vector<uint8_t> in(n * S * 64);
    for (auto& b : in) b = garbage_pad ? (uint8_t)rng() : 0;
    for (int i = 0; i < n; ++i) std::memcpy(&in[i * S * 64], im.row(y0 + i), rb);
    const size_t nin = n * S, nout = (n - 2) * S;
    std::vector<conv3::word_t> wi(nin), wo[3];
    bytes_to_words(in.data(), wi.data(), nin);
    for (int j = 0; j < 3; ++j) wo[j].assign(nout, conv3::word_t(0));
    v.f(wi.data(), wo[0].data(), wo[1].data(), wo[2].data(), im.sp.w, n, nin, nout);
    bool pad_ok = true;
    for (int j = 0; j < 3; ++j) {
        std::vector<uint8_t> ob(nout * 64);
        words_to_bytes(wo[j].data(), ob.data(), nout);
        out[j].resize((n - 2) * rb);
        for (int i = 0; i < n - 2; ++i) {
            std::memcpy(&out[j][i * rb], &ob[i * S * 64], rb);
            for (size_t b = rb; b < S * 64; ++b) if (ob[i * S * 64 + b]) pad_ok = false;
        }
    }
    return pad_ok;
}

static bool compare_rows(const V& v, const Img& im, int out_y0, const std::vector<uint8_t> out[3], const char* tag) {
    const size_t rb = im.sp.row_bytes();
    std::vector<uint8_t> r[3]; for (auto& x : r) x.resize(rb);
    int nrows = (int)(out[0].size() / (rb ? rb : 1));
    for (int i = 0; i < nrows; ++i) {
        int y = out_y0 + i;
        conv::ref_row_bytes(im.row(y - 1), im.row(y), im.row(y + 1), r[0].data(), r[1].data(), r[2].data(),
                            im.sp.w, v.C, v.bps);
        for (int j = 0; j < 3; ++j)
            if (std::memcmp(r[j].data(), &out[j][i * rb], rb)) {
                size_t b = 0; while (r[j][b] == out[j][i * rb + b]) ++b;
                std::printf("  MISMATCH %s %s %dx%d filter=%s row=%d byte=%zu (px %zu) ref=%u got=%u\n", tag, v.name,
                            im.sp.w, im.sp.h, conv::kOutNames[j], y, b, b / (v.C * v.bps), r[j][b], out[j][i * rb + b]);
                return false;
            }
    }
    return true;
}

static void check(bool ok, const std::string& what) {
    ++g_cases;
    if (!ok) { ++g_fail; std::printf("FAIL %s\n", what.c_str()); }
}

static void test_full(const V& v, int W, int H, int pattern) {
    Img im = make_img(v, W, H, pattern);
    std::vector<uint8_t> out[3];
    bool pad = run_kernel(v, im, 0, H, out, true);
    bool ok = compare_rows(v, im, 1, out, "full") && pad;
    char b[128]; std::snprintf(b, sizeof b, "full %s %dx%d pat%d%s", v.name, W, H, pattern, pad ? "" : " (padding not zero)");
    check(ok, b);
}

static void test_strips(const V& v, int W, int H, int rows_per_strip) {
    Img im = make_img(v, W, H, 0);
    auto plan = conv::plan_strips(H, im.sp.stride_bytes(), 2, 1ull << 30, 0, rows_per_strip);
    bool ok = true;
    int covered = 1;
    for (auto& s : plan) {
        std::vector<uint8_t> out[3];
        ok &= run_kernel(v, im, s.in_y0, s.n_in, out, true);
        ok &= compare_rows(v, im, s.out_y0, out, "strip");
        ok &= (s.out_y0 == covered); covered += s.n_out;
    }
    ok &= covered == H - 1;
    char b[128]; std::snprintf(b, sizeof b, "strips %s %dx%d rows/strip=%d (%zu strips)", v.name, W, H, rows_per_strip, plan.size());
    check(ok, b);
}

// Invalid parameters must be a no-op: nothing read, nothing written, no hang.
static void test_invalid(const V& v) {
    std::vector<conv3::word_t> wi(64, conv3::word_t(7)), wo[3];
    for (auto& w : wo) w.assign(64, conv3::word_t(0x5a));
    struct { unsigned W, rows, nin, nout; const char* why; } cases[] = {
        {16, 2, 64, 64, "rows<3"}, {0, 4, 64, 64, "width=0"}, {8193, 3, 64, 64, "width>max"},
        {64, 8, 7, 64, "in_words too small"}, {64, 8, 64, 5, "out_words too small"}};
    for (auto& c : cases) {
        v.f(wi.data(), wo[0].data(), wo[1].data(), wo[2].data(), c.W, c.rows, c.nin, c.nout);
        bool ok = true;
        for (auto& w : wo) for (auto& x : w) ok &= (x == conv3::word_t(0x5a));
        check(ok, std::string("invalid ") + v.name + " " + c.why);
    }
}

int main(int argc, char** argv) {
    bool quick = argc > 1 && std::string(argv[1]) == "--quick";
    const int sizes[][2] = {{3, 3}, {1, 3}, {2, 5}, {4, 3}, {3, 7}, {5, 4}, {10, 3}, {11, 4}, {21, 5}, {22, 5},
                            {31, 6}, {32, 6}, {33, 6}, {63, 4}, {64, 4}, {65, 4}, {37, 19}, {257, 131}, {1000, 9}};
    for (const V& v : kV) {
        for (auto& s : sizes)
            for (int p = 0; p < 4; ++p) test_full(v, s[0], s[1], p);
        test_full(v, CONV_MAX_WIDTH, 4, 0);          // line-buffer depth
        test_full(v, CONV_MAX_WIDTH - 1, 3, 1);
        test_invalid(v);
        test_strips(v, 37, 19, 1);                   // 1 output row per strip (3-strip row sharing)
        test_strips(v, 257, 131, 7);
        test_strips(v, 100, 50, 48);                 // one strip = whole image
        test_strips(v, 129, 40, 13);
        // back-to-back calls with different geometry (no stale state)
        test_full(v, 300, 10, 1);
        test_full(v, 17, 12, 0);
        if (!quick) {
            if (v.bps == 2) test_full(v, 1100, 1000, 0);   // ~1.1 MP
            else test_full(v, 1280, 1024, 0);               // ~1.3 MP
        }
        std::printf("variant %-5s done (%d cases so far, %d failures)\n", v.name, g_cases, g_fail);
    }
    std::printf("tb_kernel: %d cases, %d failures -> %s\n", g_cases, g_fail, g_fail ? "FAIL" : "PASS");
    return g_fail ? 1 : 0;
}

// img_io.hpp - image I/O shared by the FPGA host, the CPU baselines and the tests.
// The SAME code reads/writes images on both platforms (fairness rule 4.6).
//   * PNG via libpng, decoded row by row straight into caller-provided row memory
//     (for the FPGA host that is buffer-object memory: no extra copy, no packing);
//   * raw: interleaved samples, little-endian, W*C*bps bytes per row, no header;
//   * synthetic: deterministic pattern (noise + gradients + saturated blocks).
// Channel order in memory is B,G,R (the OpenCV convention used by v1), 16-bit samples are
// little-endian in memory (PNG files are big-endian; libpng swaps).
#pragma once
#include <png.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include "bench_common.hpp"

namespace conv {

// ------------------------------------------------------------------ variants
enum class Variant { rgb16, rgb8, gray8 };
struct VariantInfo {
    Variant v; const char* name; int C; int bps; int L; const char* top;
};
inline VariantInfo variant_info(const std::string& s) {
    if (s == "rgb16") return {Variant::rgb16, "rgb16", 3, 2, 32, "conv3_rgb16"};
    if (s == "rgb8") return {Variant::rgb8, "rgb8", 3, 1, 64, "conv3_rgb8"};
    if (s == "gray8" || s == "bw") return {Variant::gray8, "gray8", 1, 1, 64, "conv3_gray8"};
    throw std::runtime_error("unknown --variant '" + s + "' (rgb16|rgb8|gray8)");
}

struct ImageSpec {
    int w = 0, h = 0, C = 0, bps = 0;
    size_t row_bytes() const { return (size_t)w * C * bps; }
    size_t stride_words() const { return (row_bytes() + 63) / 64; }
    size_t stride_bytes() const { return stride_words() * 64; }
    double mpix() const { return (double)w * h / 1e6; }
};

// ------------------------------------------------------------------ PNG
namespace detail {
[[noreturn]] inline void png_err(png_structp p, png_const_charp msg) {
    std::fprintf(stderr, "libpng error: %s\n", msg);
    png_longjmp(p, 1);
}
inline void png_warn(png_structp, png_const_charp) {}
}  // namespace detail

inline bool has_ext(const std::string& p, const char* ext) {
    size_t n = std::strlen(ext);
    if (p.size() < n) return false;
    std::string e = p.substr(p.size() - n);
    for (auto& ch : e) ch = (char)std::tolower((unsigned char)ch);
    return e == ext;
}

// Reads the PNG header only.
inline bool png_probe(const char* path, int& w, int& h, int& depth, int& ctype) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return false;
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, nullptr, detail::png_err, detail::png_warn);
    png_infop info = png_create_info_struct(png);
    if (setjmp(png_jmpbuf(png))) { png_destroy_read_struct(&png, &info, nullptr); std::fclose(f); return false; }
    png_init_io(png, f);
    png_read_info(png, info);
    w = (int)png_get_image_width(png, info); h = (int)png_get_image_height(png, info);
    depth = png_get_bit_depth(png, info); ctype = png_get_color_type(png, info);
    png_destroy_read_struct(&png, &info, nullptr);
    std::fclose(f);
    return true;
}

// Row destination callback: returns the address where row y must be stored.
using RowDst = uint8_t* (*)(void* ctx, int y);

// Decodes a PNG into rows (C/bps given by the variant). Returns an error string or "".
// Transforms: palette->RGB, low-bit gray->8, alpha stripped, gray->BGR (colour variants),
// RGB->gray with 0.299/0.587/0.114 (gray variant), 16-bit swapped to little-endian.
inline std::string png_read_rows(const char* path, const ImageSpec& sp, RowDst dst, void* ctx) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return std::string("cannot open ") + path;
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, nullptr, detail::png_err, detail::png_warn);
    png_infop info = png_create_info_struct(png);
    const char* volatile err = nullptr;   // modified after setjmp -> volatile
    if (setjmp(png_jmpbuf(png))) {
        png_destroy_read_struct(&png, &info, nullptr); std::fclose(f);
        return err ? err : "PNG decode failed";
    }
    png_init_io(png, f);
    png_read_info(png, info);
    int depth = png_get_bit_depth(png, info), ct = png_get_color_type(png, info);
    if ((int)png_get_image_width(png, info) != sp.w || (int)png_get_image_height(png, info) != sp.h) {
        err = "PNG size changed"; png_longjmp(png, 1);
    }
    if (sp.bps == 2 && depth != 16) { err = "variant rgb16 needs a 16-bit PNG"; png_longjmp(png, 1); }
    if (sp.bps == 1 && depth == 16) { err = "8-bit variants need an 8-bit PNG (use --variant rgb16)"; png_longjmp(png, 1); }
    if (ct == PNG_COLOR_TYPE_PALETTE) png_set_palette_to_rgb(png);
    if (ct == PNG_COLOR_TYPE_GRAY && depth < 8) png_set_expand_gray_1_2_4_to_8(png);
    if (ct & PNG_COLOR_MASK_ALPHA) png_set_strip_alpha(png);
    bool is_color = (ct & PNG_COLOR_MASK_COLOR) || ct == PNG_COLOR_TYPE_PALETTE;
    if (sp.C == 3) {
        if (!is_color) png_set_gray_to_rgb(png);
        png_set_bgr(png);
    } else if (is_color) {
        png_set_rgb_to_gray_fixed(png, 1, 29900, 58700);
    }
    if (depth == 16) png_set_swap(png);
    int passes = png_set_interlace_handling(png);
    png_read_update_info(png, info);
    if ((int)png_get_channels(png, info) != sp.C || png_get_rowbytes(png, info) != sp.row_bytes()) {
        err = "unexpected PNG row format after transforms"; png_longjmp(png, 1);
    }
    for (int p = 0; p < passes; ++p)
        for (int y = 0; y < sp.h; ++y) png_read_row(png, dst(ctx, y), nullptr);
    png_read_end(png, nullptr);
    png_destroy_read_struct(&png, &info, nullptr);
    std::fclose(f);
    return "";
}

// Writes rows[0..h-1] (row_bytes each) as PNG. level = zlib level (0-9).
inline std::string png_write_rows(const char* path, const ImageSpec& sp, const uint8_t* const* rows, int level) {
    FILE* f = std::fopen(path, "wb");
    if (!f) return std::string("cannot create ") + path;
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, detail::png_err, detail::png_warn);
    png_infop info = png_create_info_struct(png);
    if (setjmp(png_jmpbuf(png))) { png_destroy_write_struct(&png, &info); std::fclose(f); return "PNG encode failed"; }
    png_init_io(png, f);
    png_set_IHDR(png, info, sp.w, sp.h, sp.bps * 8, sp.C == 3 ? PNG_COLOR_TYPE_RGB : PNG_COLOR_TYPE_GRAY,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);
    png_set_compression_level(png, level);
    png_set_filter(png, 0, PNG_FILTER_SUB);   // fast, identical on both platforms
    png_write_info(png, info);
    if (sp.C == 3) png_set_bgr(png);
    if (sp.bps == 2) png_set_swap(png);
    for (int y = 0; y < sp.h; ++y) png_write_row(png, rows[y]);   // libpng copies the row before transforming
    png_write_end(png, info);
    png_destroy_write_struct(&png, &info);
    if (std::fclose(f) != 0) return std::string("write error on ") + path;
    return "";
}

// ------------------------------------------------------------------ raw
inline std::string raw_read_rows(const char* path, const ImageSpec& sp, RowDst dst, void* ctx) {
    size_t need = sp.row_bytes() * sp.h;
    if (bench::file_size(path) < need) return "raw file too small for --width/--height/variant";
    FILE* f = std::fopen(path, "rb");
    if (!f) return std::string("cannot open ") + path;
    for (int y = 0; y < sp.h; ++y)
        if (std::fread(dst(ctx, y), 1, sp.row_bytes(), f) != sp.row_bytes()) { std::fclose(f); return "short read"; }
    std::fclose(f);
    return "";
}
inline std::string raw_write_rows(const char* path, const ImageSpec& sp, const uint8_t* const* rows) {
    FILE* f = std::fopen(path, "wb");
    if (!f) return std::string("cannot create ") + path;
    setvbuf(f, nullptr, _IOFBF, 1 << 22);
    for (int y = 0; y < sp.h; ++y)
        if (std::fwrite(rows[y], 1, sp.row_bytes(), f) != sp.row_bytes()) { std::fclose(f); return "short write"; }
    return std::fclose(f) ? "write error" : "";
}

// ------------------------------------------------------------------ synthetic
inline uint64_t mix64(uint64_t x) {
    x ^= x >> 33; x *= 0xff51afd7ed558ccdull; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ull; x ^= x >> 33; return x;
}
// Deterministic test pattern: smooth gradient + noise, plus saturated/black tiles so that
// every clamp path is exercised. Row-independent -> can be generated in parallel.
inline void synth_row(uint8_t* dst, int y, const ImageSpec& sp) {
    const int maxv = sp.bps == 2 ? 65535 : 255;
    uint64_t s = mix64(0x9E3779B97F4A7C15ull ^ (uint64_t)y);
    for (int x = 0; x < sp.w; ++x) {
        const int tile = ((x >> 5) + (y >> 5)) % 7;
        for (int c = 0; c < sp.C; ++c) {
            s = s * 6364136223846793005ull + 1442695040888963407ull;
            int noise = (int)((s >> 33) & 0xffff);
            int v;
            if (tile == 0) v = maxv;                                   // saturated
            else if (tile == 1) v = 0;                                 // black
            else if (tile == 2) v = (noise & 1) ? maxv : 0;            // salt & pepper
            else {
                long g = ((long)x * (c + 1) * 97 + (long)y * 61) % (maxv + 1);
                v = (int)((g + ((long)noise * (maxv + 1) >> 18)) % (maxv + 1));
            }
            if (sp.bps == 2) { dst[(x * sp.C + c) * 2] = (uint8_t)v; dst[(x * sp.C + c) * 2 + 1] = (uint8_t)(v >> 8); }
            else dst[x * sp.C + c] = (uint8_t)v;
        }
    }
}

// ------------------------------------------------------------------ input source
// Parses --in / --synthetic / --width / --height and fills rows via a RowDst callback.
struct Source {
    enum Kind { PNG, RAW, SYNTH } kind = SYNTH;
    std::string path;
    ImageSpec spec;

    void open(const bench::Args& a, const VariantInfo& vi) {
        spec.C = vi.C; spec.bps = vi.bps;
        if (a.has("synthetic")) {
            kind = SYNTH;
            std::string s = a.str("synthetic");
            if (std::sscanf(s.c_str(), "%dx%d", &spec.w, &spec.h) != 2) throw std::runtime_error("--synthetic WxH");
        } else if (a.has("in")) {
            path = a.str("in");
            if (has_ext(path, ".png")) {
                kind = PNG;
                int d, ct;
                if (!png_probe(path.c_str(), spec.w, spec.h, d, ct)) throw std::runtime_error("cannot read PNG " + path);
            } else {
                kind = RAW;
                spec.w = (int)a.i64("width", 0); spec.h = (int)a.i64("height", 0);
                if (spec.w <= 0 || spec.h <= 0) throw std::runtime_error("raw input needs --width and --height");
            }
        } else throw std::runtime_error("need --in IMAGE or --synthetic WxH");
        if (spec.w <= 0 || spec.h <= 0) throw std::runtime_error("bad image size");
    }
    std::string label() const { return kind == SYNTH ? "synthetic" : path; }

    // For SYNTH, rows are generated with `threads` threads (dst must be thread-safe).
    void fill(RowDst dst, void* ctx, int threads) const {
        std::string e;
        if (kind == PNG) e = png_read_rows(path.c_str(), spec, dst, ctx);
        else if (kind == RAW) e = raw_read_rows(path.c_str(), spec, dst, ctx);
        else {
            std::atomic<int> next{0};
            auto work = [&] { for (int y; (y = next++) < spec.h;) synth_row(dst(ctx, y), y, spec); };
            std::vector<std::thread> th;
            for (int t = 1; t < std::max(1, threads); ++t) th.emplace_back(work);
            work();
            for (auto& t : th) t.join();
        }
        if (!e.empty()) throw std::runtime_error(e);
    }
};

// ------------------------------------------------------------------ outputs
inline uint64_t checksum_rows(const uint8_t* const* rows, const ImageSpec& sp) {
    uint64_t h = 1469598103934665603ull;
    for (int y = 0; y < sp.h; ++y) h = bench::fnv1a64(rows[y], sp.row_bytes(), h);
    return h;
}

static const char* const kOutNames[3] = {"sharpen", "edge", "blur"};

// Checksums of the three outputs, computed in parallel (one thread per output).
inline void checksum3(const std::vector<const uint8_t*>* rows[3], const ImageSpec& sp, uint64_t out[3]) {
    std::vector<std::thread> th;
    for (int j = 0; j < 3; ++j) th.emplace_back([&, j] { out[j] = checksum_rows(rows[j]->data(), sp); });
    for (auto& t : th) t.join();
}

// Writes the three outputs in parallel: <prefix>_<name>.png (or .raw with fmt=="raw").
inline void write3(const std::string& prefix, const std::string& fmt, int level,
                   const std::vector<const uint8_t*>* rows[3], const ImageSpec& sp) {
    std::string err[3];
    std::vector<std::thread> th;
    for (int j = 0; j < 3; ++j)
        th.emplace_back([&, j] {
            std::string p = prefix + "_" + kOutNames[j] + (fmt == "raw" ? ".raw" : ".png");
            err[j] = fmt == "raw" ? raw_write_rows(p.c_str(), sp, rows[j]->data())
                                  : png_write_rows(p.c_str(), sp, rows[j]->data(), level);
        });
    for (auto& t : th) t.join();
    for (auto& e : err) if (!e.empty()) throw std::runtime_error(e);
}

}  // namespace conv

// mkimg.cpp - writes a synthetic test image (same generator as --synthetic) as PNG or raw.
//   mkimg --variant rgb16|rgb8|gray8 --size WxH --out FILE(.png|.raw)
#include <cstdio>
#include <vector>
#include "img_io.hpp"
int main(int argc, char** argv) {
    bench::Args a(argc, argv);
    try {
        const conv::VariantInfo vi = conv::variant_info(a.str("variant", "rgb8"));
        conv::ImageSpec sp; sp.C = vi.C; sp.bps = vi.bps;
        if (std::sscanf(a.str("size").c_str(), "%dx%d", &sp.w, &sp.h) != 2) throw std::runtime_error("--size WxH");
        std::vector<uint8_t> px(sp.row_bytes() * sp.h);
        std::vector<const uint8_t*> rows(sp.h);
        for (int y = 0; y < sp.h; ++y) { rows[y] = px.data() + y * sp.row_bytes(); conv::synth_row(px.data() + y * sp.row_bytes(), y, sp); }
        const std::string out = a.str("out");
        std::string e = conv::has_ext(out, ".png") ? conv::png_write_rows(out.c_str(), sp, rows.data(), 1)
                                                   : conv::raw_write_rows(out.c_str(), sp, rows.data());
        if (!e.empty()) throw std::runtime_error(e);
    } catch (const std::exception& e) { std::fprintf(stderr, "mkimg: %s\n", e.what()); return 1; }
    return 0;
}

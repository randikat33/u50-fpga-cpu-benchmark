// sim_register.cpp - registers the three kernel tops with xrt_sim (software XRT) so the
// unmodified host program runs end-to-end in the sandbox. Buffers are converted between
// the device byte image and ap_uint<512> words; words the kernel does not write keep the
// previous device content (as on hardware).
#include <xrt/xrt_sim.h>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "conv3.hpp"
#include "word_io.hpp"

using top_fn = void (*)(const conv3::word_t*, conv3::word_t*, conv3::word_t*, conv3::word_t*,
                        unsigned, unsigned, unsigned, unsigned);

static void run_top(top_fn top, std::vector<xrt_sim::arg_t>& a) {
    const size_t nin = a[6].scalar, nout = a[7].scalar;
    for (int i = 0; i < 4; ++i)
        if (!a[i].bo) { std::fprintf(stderr, "[sim] buffer arg %d not set\n", i); std::abort(); }
    if (a[0].bo->size < nin * 64 || a[1].bo->size < nout * 64 || a[2].bo->size < nout * 64 || a[3].bo->size < nout * 64) {
        std::fprintf(stderr, "[sim] in_words/out_words exceed the BO sizes (AXI access out of range)\n");
        std::abort();
    }
    std::vector<conv3::word_t> in(nin), o[3];
    bytes_to_words(a[0].as<uint8_t>(), in.data(), nin);
    for (int j = 0; j < 3; ++j) { o[j].resize(nout); bytes_to_words(a[1 + j].as<uint8_t>(), o[j].data(), nout); }
    top(in.data(), o[0].data(), o[1].data(), o[2].data(), (unsigned)a[4].scalar, (unsigned)a[5].scalar,
        (unsigned)nin, (unsigned)nout);
    for (int j = 0; j < 3; ++j) words_to_bytes(o[j].data(), a[1 + j].as<uint8_t>(), nout);
}

XRT_SIM_KERNEL(conv3_rgb16, 8) { run_top(conv3_rgb16, a); }
XRT_SIM_KERNEL(conv3_rgb8, 8) { run_top(conv3_rgb8, a); }
XRT_SIM_KERNEL(conv3_gray8, 8) { run_top(conv3_gray8, a); }

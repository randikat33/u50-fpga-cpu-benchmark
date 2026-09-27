// lm_ladder.cpp - v2 multi-output (ABR ladder) resize kernel, Alveo U50, Vitis HLS 2023.1
//
//            +--> resize 240p  --> gearbox --> burst write o0 --+
//  burst rd  +--> resize 360p  --> gearbox --> burst write o1 --+
//  src --> gearbox (fan-out) --> resize 480p  --> gearbox --> burst write o2 --+--> one output BO
//            +--> resize 720p  --> gearbox --> burst write o3 --+    (HBM[1])
//            +--> resize 1080p --> gearbox --> burst write o4 --+
//
// v1 used 6 kernels (broadcast + 5 resizers over AXI-stream), 256-bit words that the HOST
// packed pixel by pixel, 5 separate output buffers and 6 enqueues per frame. v2 is one
// kernel: 1 H2D + 1 D2H + 1 start per call, and a call can carry up to LM_MAX_BATCH frames.
#include "lm_ladder.h"

// ---- burst reader (word offset `base`) --------------------------------------------------
static void read_words(const lm_word_t* src, ap_uint<32> base, hls::stream<lm_word_t>& out, int nwords) {
    for (int i = 0; i < nwords; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=388800
        out.write(src[base + i]);   // sequential, unconditional -> read burst
    }
}

// ---- words -> NPPC8 groups, fanned out to the 5 resizers ---------------------------------
// Fixed-offset gearbox (3 x 512 = 8 x 192 bits), see 05_LiveStream_Single for the derivation.
static void words_to_mats(hls::stream<lm_word_t>& in, lm_in_mat_t& m0, lm_in_mat_t& m1, lm_in_mat_t& m2,
                          lm_in_mat_t& m3, lm_in_mat_t& m4, int ngroups) {
    lm_word_t w0 = 0, w1 = 0, w2 = 0;
    ap_uint<3> k = 0;
    for (int i = 0; i < ngroups; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=1036800
        lm_word_t n0 = w0, n1 = w1, n2 = w2;
        if (k == 0) n0 = in.read();
        else if (k == 2) n1 = in.read();
        else if (k == 5) n2 = in.read();
        lm_pack_t g;
        switch (k) {   // 8:1 mux over constant offsets
            case 0: g = n0.range(191, 0); break;
            case 1: g = n0.range(383, 192); break;
            case 2: g = (n1.range(63, 0), n0.range(511, 384)); break;
            case 3: g = n1.range(255, 64); break;
            case 4: g = n1.range(447, 256); break;
            case 5: g = (n2.range(127, 0), n1.range(511, 448)); break;
            case 6: g = n2.range(319, 128); break;
            default: g = n2.range(511, 320); break;
        }
        m0.write(i, g);   // same group to every rung (replaces v1's broadcast kernel)
        m1.write(i, g);
        m2.write(i, g);
        m3.write(i, g);
        m4.write(i, g);
        w0 = n0; w1 = n1; w2 = n2;
        k++;
    }
}

// ---- NPPC8 groups -> words (one instance per rung) ----------------------------------------
template <typename MAT>
static void mat_to_words(MAT& mat, hls::stream<lm_word_t>& out, int ngroups) {
    lm_word_t w0 = 0, w1 = 0, w2 = 0;
    ap_uint<3> k = 0;
    for (int i = 0; i < ngroups; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=259200
        lm_pack_t g = mat.read(i);
        switch (k) {   // 1:8 demux into constant offsets
            case 0: w0 = 0; w1 = 0; w2 = 0; w0.range(191, 0) = g; break;
            case 1: w0.range(383, 192) = g; break;
            case 2: w0.range(511, 384) = g.range(127, 0); w1.range(63, 0) = g.range(191, 128); break;
            case 3: w1.range(255, 64) = g; break;
            case 4: w1.range(447, 256) = g; break;
            case 5: w1.range(511, 448) = g.range(63, 0); w2.range(127, 0) = g.range(191, 64); break;
            case 6: w2.range(319, 128) = g; break;
            default: w2.range(511, 320) = g; break;
        }
        if (k == 2) out.write(w0);
        else if (k == 5) out.write(w1);
        else if (k == 7) out.write(w2);
        k++;
    }
    int r = ngroups & 7;
    if (r == 1 || r == 2) out.write(w0);
    else if (r >= 3 && r <= 5) out.write(w1);
    else if (r >= 6) out.write(w2);
}

// ---- burst writer (word offset `base`) ---------------------------------------------------
static void write_words(hls::stream<lm_word_t>& in, lm_word_t* dst, ap_uint<32> base, int nwords) {
    for (int i = 0; i < nwords; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=97200
        dst[base + i] = in.read();   // sequential, unconditional -> write burst
    }
}

// ---- the 5 resizers (bilinear, MAX_DOWN_SCALE sized per rung) ----------------------------
#if LS_RESIZE_HAS_URAM_ARG
#define LM_RESIZE(OH, OW, MD, IN, OUT) \
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LM_MAX_IN_H, LM_MAX_IN_W, OH, OW, XF_NPPC8, true, MD, \
                   LM_DEPTH_IN, LM_DEPTH_OUT>(IN, OUT)
#else
#define LM_RESIZE(OH, OW, MD, IN, OUT) \
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LM_MAX_IN_H, LM_MAX_IN_W, OH, OW, XF_NPPC8, MD, LM_DEPTH_IN, \
                   LM_DEPTH_OUT>(IN, OUT)
#endif
static void resize0(lm_in_mat_t& i, lm_out0_t& o) { LM_RESIZE(LM_H0, LM_W0, LM_MD0, i, o); }
static void resize1(lm_in_mat_t& i, lm_out1_t& o) { LM_RESIZE(LM_H1, LM_W1, LM_MD1, i, o); }
static void resize2(lm_in_mat_t& i, lm_out2_t& o) { LM_RESIZE(LM_H2, LM_W2, LM_MD2, i, o); }
static void resize3(lm_in_mat_t& i, lm_out3_t& o) { LM_RESIZE(LM_H3, LM_W3, LM_MD3, i, o); }
static void resize4(lm_in_mat_t& i, lm_out4_t& o) { LM_RESIZE(LM_H4, LM_W4, LM_MD4, i, o); }

// Groups/words of each rung (constants: widths are multiples of 8).
#define LM_G(k) ((LM_W##k * LM_H##k) / 8)
#define LM_NW(k) ((LM_G(k) * 3 + 7) / 8)

static void ladder_frame(const lm_word_t* src, lm_word_t* o0, lm_word_t* o1, lm_word_t* o2, lm_word_t* o3,
                         lm_word_t* o4, int in_h, int in_w, int ng_in, int nw_in, ap_uint<32> in_base,
                         ap_uint<32> b0, ap_uint<32> b1, ap_uint<32> b2, ap_uint<32> b3, ap_uint<32> b4) {
#pragma HLS DATAFLOW
    hls::stream<lm_word_t> win("win"), w0("w0"), w1("w1"), w2("w2"), w3("w3"), w4("w4");
#pragma HLS STREAM variable=win depth=LM_WORD_FIFO   // absorbs read bursts
#pragma HLS STREAM variable=w0 depth=LM_WORD_FIFO    // let each writer issue full bursts
#pragma HLS STREAM variable=w1 depth=LM_WORD_FIFO
#pragma HLS STREAM variable=w2 depth=LM_WORD_FIFO
#pragma HLS STREAM variable=w3 depth=LM_WORD_FIFO
#pragma HLS STREAM variable=w4 depth=LM_WORD_FIFO
    lm_in_mat_t i0(in_h, in_w), i1(in_h, in_w), i2(in_h, in_w), i3(in_h, in_w), i4(in_h, in_w);
    lm_out0_t r0(LM_H0, LM_W0);
    lm_out1_t r1(LM_H1, LM_W1);
    lm_out2_t r2(LM_H2, LM_W2);
    lm_out3_t r3(LM_H3, LM_W3);
    lm_out4_t r4(LM_H4, LM_W4);
    read_words(src, in_base, win, nw_in);
    words_to_mats(win, i0, i1, i2, i3, i4, ng_in);
    resize0(i0, r0);
    resize1(i1, r1);
    resize2(i2, r2);
    resize3(i3, r3);
    resize4(i4, r4);
    mat_to_words(r0, w0, LM_G(0));
    mat_to_words(r1, w1, LM_G(1));
    mat_to_words(r2, w2, LM_G(2));
    mat_to_words(r3, w3, LM_G(3));
    mat_to_words(r4, w4, LM_G(4));
    write_words(w0, o0, b0, LM_NW(0));
    write_words(w1, o1, b1, LM_NW(1));
    write_words(w2, o2, b2, LM_NW(2));
    write_words(w3, o3, b3, LM_NW(3));
    write_words(w4, o4, b4, LM_NW(4));
}

extern "C" {
void lm_ladder(const lm_word_t* src, lm_word_t* o0, lm_word_t* o1, lm_word_t* o2, lm_word_t* o3, lm_word_t* o4,
               int in_h, int in_w, int nframes) {
// One read master (HBM[0]) and five write masters that all map to the same bank (HBM[1]),
// so the host allocates ONE output BO and syncs it once per call.
#pragma HLS INTERFACE m_axi port=src offset=slave bundle=gin depth=3110400 max_widen_bitwidth=512 max_read_burst_length=256 num_read_outstanding=16 max_write_burst_length=2 num_write_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=o0 offset=slave bundle=go0 depth=90000 max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=o1 offset=slave bundle=go1 depth=90000 max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=o2 offset=slave bundle=go2 depth=90000 max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=o3 offset=slave bundle=go3 depth=90000 max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=o4 offset=slave bundle=go4 depth=1400000 max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE s_axilite port=src bundle=control
#pragma HLS INTERFACE s_axilite port=o0 bundle=control
#pragma HLS INTERFACE s_axilite port=o1 bundle=control
#pragma HLS INTERFACE s_axilite port=o2 bundle=control
#pragma HLS INTERFACE s_axilite port=o3 bundle=control
#pragma HLS INTERFACE s_axilite port=o4 bundle=control
#pragma HLS INTERFACE s_axilite port=in_h bundle=control
#pragma HLS INTERFACE s_axilite port=in_w bundle=control
#pragma HLS INTERFACE s_axilite port=nframes bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    int ih = in_h, iw = in_w, nf = nframes;
    // Same checks as lm::dims_ok (each rung: no upscaling, ratio <= MAX_DOWN-1).
    bool ok = iw > 0 && ih > 1 && iw <= LM_MAX_IN_W && ih <= LM_MAX_IN_H && (iw & (LM_NPPC - 1)) == 0 && nf >= 1 &&
              nf <= LM_MAX_BATCH &&
              iw >= LM_W4 && ih >= LM_H4 &&   // the largest rung bounds all "no upscale" checks
              iw <= LM_W0 * (LM_MD0 - 1) && ih <= LM_H0 * (LM_MD0 - 1) && iw <= LM_W1 * (LM_MD1 - 1) &&
              ih <= LM_H1 * (LM_MD1 - 1) && iw <= LM_W2 * (LM_MD2 - 1) && ih <= LM_H2 * (LM_MD2 - 1) &&
              iw <= LM_W3 * (LM_MD3 - 1) && ih <= LM_H3 * (LM_MD3 - 1) && iw <= LM_W4 * (LM_MD4 - 1) &&
              ih <= LM_H4 * (LM_MD4 - 1);
    if (!ok) return;
    ap_uint<23> pix_in = (ap_uint<12>)ih * (ap_uint<12>)iw;
    int ng_in = pix_in >> 3;
    int nw_in = (ng_in * 3 + 7) >> 3;
    // output block layout (words): rung k of frame f at f*NWF + OFF_k
    const ap_uint<32> OFF1 = LM_NW(0), OFF2 = OFF1 + LM_NW(1), OFF3 = OFF2 + LM_NW(2), OFF4 = OFF3 + LM_NW(3),
                      NWF = OFF4 + LM_NW(4);
    ap_uint<32> in_base = 0, out_base = 0;
    for (int f = 0; f < nf; f++) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=8
        ladder_frame(src, o0, o1, o2, o3, o4, ih, iw, ng_in, nw_in, in_base, out_base, out_base + OFF1,
                     out_base + OFF2, out_base + OFF3, out_base + OFF4);
        in_base += nw_in;     // adders instead of f * n multipliers
        out_base += NWF;
    }
}
}

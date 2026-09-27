// ls_resize.cpp - v2 single-output live-stream resize kernel (Alveo U50, Vitis HLS 2023.1)
//
//   HBM --burst read--> [word FIFO] --gearbox 3 words:8 groups--> xf::cv::Mat (NPPC8)
//       --> xf::cv::resize (bilinear) --> xf::cv::Mat --gearbox 8 groups:3 words-->
//       [word FIFO] --burst write--> HBM
//
// Differences from v1 (Live_Stream_Kernal.cpp):
//  * 512-bit words (v1: 256) -> half the AXI beats per frame.
//  * Fixed-position gearboxes: 3 x 512 = 8 x 192 bits, so every group sits at a constant
//    offset inside a 3-word block. v1 inserted words at a *variable* bit offset
//    (shift_reg(bits_in_reg+255, bits_in_reg)), i.e. a 512-bit barrel shifter, plus a
//    512-bit shift every cycle, on the path that failed timing (WNS -2.12 ns).
//  * The m_axi accesses are unconditional, fixed-trip-count loops in their own dataflow
//    processes, so HLS infers 256-beat bursts (v1 read/wrote conditionally inside the
//    pixel loop, which blocks burst inference).
//  * MAX_DOWN_SCALE = 10 instead of 16 -> 6 instead of 9 line-buffer copies in resize.
#include "ls_resize.h"

// ---- 1. burst reader ---------------------------------------------------------------
static void read_words(const ls_word_t* src, hls::stream<ls_word_t>& out, int nwords) {
    for (int i = 0; i < nwords; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=388800
        out.write(src[i]);   // sequential, unconditional -> read burst
    }
}

// ---- 2. words -> NPPC8 groups (3 words : 8 groups) -----------------------------------
// Group k of a block occupies bits [192k, 192k+192). Word 0 is needed by groups 0-1,
// word 1 by groups 2-4 and word 2 by groups 5-7, so words are taken at k = 0, 2, 5.
// The number of words consumed for n groups is 3*(n/8) + {0,1,1,2,2,2,3,3}[n%8]
// = ceil(3n/8) = ceil(24n/64), exactly what read_words produced.
static void words_to_mat(hls::stream<ls_word_t>& in, ls_in_mat_t& mat, int ngroups) {
    ls_word_t w0 = 0, w1 = 0, w2 = 0;   // the 3 words of the current 1536-bit block
    ap_uint<3> k = 0;
    for (int i = 0; i < ngroups; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=1036800
        ls_word_t n0 = w0, n1 = w1, n2 = w2;
        if (k == 0) n0 = in.read();
        else if (k == 2) n1 = in.read();
        else if (k == 5) n2 = in.read();
        ls_pack_t g;
        switch (k) {   // 8:1 mux over constant offsets (no barrel shifter)
            case 0: g = n0.range(191, 0); break;
            case 1: g = n0.range(383, 192); break;
            case 2: g = (n1.range(63, 0), n0.range(511, 384)); break;
            case 3: g = n1.range(255, 64); break;
            case 4: g = n1.range(447, 256); break;
            case 5: g = (n2.range(127, 0), n1.range(511, 448)); break;
            case 6: g = n2.range(319, 128); break;
            default: g = n2.range(511, 320); break;
        }
        mat.write(i, g);
        w0 = n0; w1 = n1; w2 = n2;
        k++;   // 3-bit counter wraps 7 -> 0
    }
}

// ---- 3. NPPC8 groups -> words (8 groups : 3 words) -----------------------------------
// Word 0 is complete after group 2, word 1 after group 5, word 2 after group 7. The words
// are cleared at the start of each block, so the padding bytes of a partial last word are
// always zero (deterministic output checksums).
static void mat_to_words(ls_out_mat_t& mat, hls::stream<ls_word_t>& out, int ngroups) {
    ls_word_t w0 = 0, w1 = 0, w2 = 0;
    ap_uint<3> k = 0;
    for (int i = 0; i < ngroups; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=259200
        ls_pack_t g = mat.read(i);
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
    // flush the word that holds the last (partial) block's tail
    int r = ngroups & 7;
    if (r == 1 || r == 2) out.write(w0);
    else if (r >= 3 && r <= 5) out.write(w1);
    else if (r >= 6) out.write(w2);
}

// ---- 4. burst writer -------------------------------------------------------------------
static void write_words(hls::stream<ls_word_t>& in, ls_word_t* dst, int nwords) {
    for (int i = 0; i < nwords; i++) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=97200
        dst[i] = in.read();   // sequential, unconditional -> write burst
    }
}

static void resize_bilinear(ls_in_mat_t& in, ls_out_mat_t& out) {
#if LS_RESIZE_HAS_URAM_ARG
    // USE_URAM=true: line buffers in URAM (the U50 has 640 URAMs and few are used)
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LS_MAX_IN_H, LS_MAX_IN_W, LS_MAX_OUT_H, LS_MAX_OUT_W,
                   XF_NPPC8, true, LS_MAX_DOWN, LS_DEPTH_IN, LS_DEPTH_OUT>(in, out);
#else
    xf::cv::resize<XF_INTERPOLATION_BILINEAR, XF_8UC3, LS_MAX_IN_H, LS_MAX_IN_W, LS_MAX_OUT_H, LS_MAX_OUT_W,
                   XF_NPPC8, LS_MAX_DOWN, LS_DEPTH_IN, LS_DEPTH_OUT>(in, out);
#endif
}

static void resize_core(const ls_word_t* src, ls_word_t* dst, int in_h, int in_w, int out_h, int out_w,
                        int nw_in, int ng_in, int nw_out, int ng_out) {
#pragma HLS DATAFLOW
    hls::stream<ls_word_t> win("win"), wout("wout");
#pragma HLS STREAM variable=win depth=LS_WORD_FIFO   // absorbs 256-beat read bursts
#pragma HLS STREAM variable=wout depth=LS_WORD_FIFO  // lets the writer issue full bursts
    ls_in_mat_t in_mat(in_h, in_w);
    ls_out_mat_t out_mat(out_h, out_w);
    read_words(src, win, nw_in);
    words_to_mat(win, in_mat, ng_in);
    resize_bilinear(in_mat, out_mat);
    mat_to_words(out_mat, wout, ng_out);
    write_words(wout, dst, nw_out);
}

extern "C" {
void ls_resize(const ls_word_t* src, ls_word_t* dst, int in_h, int in_w, int out_h, int out_w) {
// Separate bundles -> separate AXI masters, each mapped to its own HBM bank in link.cfg.
#pragma HLS INTERFACE m_axi port=src offset=slave bundle=gmem0 depth=388800 max_widen_bitwidth=512 max_read_burst_length=256 num_read_outstanding=16 max_write_burst_length=2 num_write_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=dst offset=slave bundle=gmem1 depth=97200 max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE s_axilite port=src bundle=control
#pragma HLS INTERFACE s_axilite port=dst bundle=control
#pragma HLS INTERFACE s_axilite port=in_h bundle=control
#pragma HLS INTERFACE s_axilite port=in_w bundle=control
#pragma HLS INTERFACE s_axilite port=out_h bundle=control
#pragma HLS INTERFACE s_axilite port=out_w bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    // Register the control inputs and all derived counts before the dataflow region:
    // multiplications/shifts stay off the dataflow start path (v1 timing note).
    int ih = in_h, iw = in_w, oh = out_h, ow = out_w;
    bool ok = iw > 0 && ih > 1 && ow > 0 && oh > 1 && iw <= LS_MAX_IN_W && ih <= LS_MAX_IN_H &&
              ow <= LS_MAX_OUT_W && oh <= LS_MAX_OUT_H && (iw & (LS_NPPC - 1)) == 0 && (ow & (LS_NPPC - 1)) == 0 &&
              ow <= iw && oh <= ih && iw <= ow * (LS_MAX_DOWN - 1) && ih <= oh * (LS_MAX_DOWN - 1);
    if (!ok) return;
    ap_uint<23> pix_in = (ap_uint<12>)ih * (ap_uint<12>)iw;    // <= 8,294,400
    ap_uint<22> pix_out = (ap_uint<11>)oh * (ap_uint<11>)ow;   // <= 2,073,600
    int ng_in = pix_in >> 3;                 // groups of 8 pixels (width divisible by 8)
    int ng_out = pix_out >> 3;
    int nw_in = (ng_in * 3 + 7) >> 3;        // ceil(24*groups/64)
    int nw_out = (ng_out * 3 + 7) >> 3;
    resize_core(src, dst, ih, iw, oh, ow, nw_in, ng_in, nw_out, ng_out);
}
}

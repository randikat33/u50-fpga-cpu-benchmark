// conv3_core.hpp - templated HLS core of the v2 3x3 convolution kernels (Vitis HLS 2023.1).
//
// Word-level vectorised line-buffer stencil. The input is the raw interleaved row byte
// stream (see common/conv_params.hpp). Because channels are interleaved, the horizontal
// neighbour of sample i is sample i-C / i+C (C = channels) and the vertical neighbours are
// at the same sample index in the rows above/below. Every clock cycle therefore processes
// one whole 512-bit word = L samples in parallel (L = 64 for 8-bit, 32 for 16-bit data):
//   gray8: 64 px/cycle, rgb8: 21.3 px/cycle, rgb16: 10.7 px/cycle.
// No host-side packing and no gearbox are needed.
//
//   cfg_proc --cfg--> reader(m_axi gmem_in) --words--> stencil --3 words--> writer x3 (m_axi)
//
// Stencil: 2 line buffers store whole words (URAM, depth = words per row). Output word k
// is computed when word k+1 arrives (look-ahead of C samples for the right neighbours);
// the C samples left of word k come from registers (tail of word k-1). One extra "flush"
// iteration per row emits the last word. Border / padding samples are masked to 0.
#pragma once
#include <ap_int.h>
#include <hls_stream.h>
#include "conv_params.hpp"

namespace conv3 {

typedef ap_uint<CONV_WORD_BITS> word_t;

struct cfg_t {
    ap_uint<32> n_in;     // words to read  = rows * stride
    ap_uint<32> n_out;    // words to write = (rows-2) * stride (per output)
    ap_uint<40> n_iter;   // stencil loop iterations = rows * iters
    ap_uint<11> stride;   // words per row
    ap_uint<11> iters;    // iterations per row = max(stride, CONV_MIN_ITERS) + 1
    ap_uint<16> cw1;      // C*(W-1): index of the first right-border sample
};

// ---------------------------------------------------------------- configuration
// C = channels, SH = log2(bytes per sample). All geometry is derived here once, so the
// other processes only consume ready-made trip counts (canonical dataflow).
template <int C, int SH>
void cfg_proc(unsigned width, unsigned rows, unsigned in_words, unsigned out_words,
              hls::stream<cfg_t>& c_rd, hls::stream<cfg_t>& c_st, hls::stream<cfg_t>& c_w0,
              hls::stream<cfg_t>& c_w1, hls::stream<cfg_t>& c_w2) {
    cfg_t c;
    bool ok = width >= 1 && width <= CONV_MAX_WIDTH && rows >= 3 && rows <= CONV_MAX_ROWS;
    ap_uint<14> w = ok ? width : 1;                      // safe value when !ok
    ap_uint<16> samples = w * C;                         // constant multiply -> shift-add
    ap_uint<18> bytes = ap_uint<18>(samples) << SH;
    ap_uint<11> stride = (bytes + 63) >> 6;              // /64 as a shift
    ap_uint<25> r = ok ? rows : 3;
    ap_uint<36> nin = r * stride;                        // one-off multiplies (outside loops)
    ap_uint<36> nout = (r - 2) * stride;
    ok = ok && nin <= in_words && nout <= out_words;
    ap_uint<11> iters = (stride > CONV_MIN_ITERS ? stride : ap_uint<11>(CONV_MIN_ITERS)) + 1;
    c.stride = stride;
    c.iters = iters;
    c.cw1 = samples - C;
    c.n_in = ok ? ap_uint<32>(nin) : ap_uint<32>(0);
    c.n_out = ok ? ap_uint<32>(nout) : ap_uint<32>(0);
    c.n_iter = ok ? ap_uint<40>(r * iters) : ap_uint<40>(0);
    c_rd.write(c); c_st.write(c); c_w0.write(c); c_w1.write(c); c_w2.write(c);
}

// ---------------------------------------------------------------- burst reader
inline void reader(const word_t* mem, hls::stream<cfg_t>& cs, hls::stream<word_t>& out) {
    cfg_t c = cs.read();
    const ap_uint<32> n = c.n_in;
    for (ap_uint<32> i = 0; i < n; ++i) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=0 max=33177600 avg=3317760
        out.write(mem[i]);
    }
}

// ---------------------------------------------------------------- burst writer
inline void writer(word_t* mem, hls::stream<cfg_t>& cs, hls::stream<word_t>& in) {
    cfg_t c = cs.read();
    const ap_uint<32> n = c.n_out;
    for (ap_uint<32> i = 0; i < n; ++i) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=0 max=33177600 avg=3317760
        mem[i] = in.read();
    }
}

// ---------------------------------------------------------------- per-sample math
// B = bits per sample. Returns the three clamped results for one sample.
template <int B>
inline void px3(ap_uint<B> NW, ap_uint<B> N, ap_uint<B> NE, ap_uint<B> Wv, ap_uint<B> Cc, ap_uint<B> E,
                ap_uint<B> SW, ap_uint<B> S, ap_uint<B> SE, ap_uint<B>& o_s, ap_uint<B>& o_e, ap_uint<B>& o_b) {
#pragma HLS INLINE
    const ap_uint<B + 2> s4 = ap_uint<B + 1>(N + S) + ap_uint<B + 1>(Wv + E);        // N+S+W+E
    const ap_uint<B + 2> cr = ap_uint<B + 1>(NW + NE) + ap_uint<B + 1>(SW + SE);     // corners
    const ap_uint<B + 3> c4 = ap_uint<B + 3>(Cc) << 2;
    const ap_uint<B + 3> c8 = ap_uint<B + 3>(Cc) << 3;
    const ap_int<B + 4> vs = ap_int<B + 4>(c4 + Cc) - ap_int<B + 4>(s4);             // [-4M, 5M]
    const ap_int<B + 4> ve = ap_int<B + 4>(c8) - ap_int<B + 4>(s4) - ap_int<B + 4>(cr); // [-8M, 8M]
    const ap_uint<B + 4> vb = ap_uint<B + 4>(c4) + (ap_uint<B + 4>(s4) << 1) + cr;   // [0, 16M]
    const ap_uint<B> maxv = ~ap_uint<B>(0);
    // clamp: sign bit -> 0, any bit above B-1 -> max (shift-free comparisons)
    o_s = vs[B + 3] ? ap_uint<B>(0) : (vs.range(B + 2, B) != 0 ? maxv : ap_uint<B>(vs.range(B - 1, 0)));
    o_e = ve[B + 3] ? ap_uint<B>(0) : (ve.range(B + 2, B) != 0 ? maxv : ap_uint<B>(ve.range(B - 1, 0)));
    o_b = vb.range(B + 3, 4);                                                          // <= M, no clamp needed
}

// ---------------------------------------------------------------- stencil
// B bits/sample, L samples/word, C channels, MAXS line-buffer depth (words).
template <int B, int L, int C, int MAXS>
void stencil(hls::stream<cfg_t>& cs, hls::stream<word_t>& in,
             hls::stream<word_t>& o_sh, hls::stream<word_t>& o_ed, hls::stream<word_t>& o_bl) {
    cfg_t c = cs.read();
    const ap_uint<11> S = c.stride;
    const ap_uint<11> I_last = c.iters - 1;
    const ap_uint<40> total = c.n_iter;
    const ap_int<18> cw1 = c.cw1;

    // Line buffers: whole words of rows y-2 (top) and y-1 (mid). Not static: the contents
    // never need to survive a call (rows 0/1 produce no output) and C-sim of several
    // concurrent CUs stays re-entrant.
    word_t lb_top[MAXS];
    word_t lb_mid[MAXS];
#pragma HLS BIND_STORAGE variable=lb_top type=ram_s2p impl=uram latency=2
#pragma HLS BIND_STORAGE variable=lb_mid type=ram_s2p impl=uram latency=2

    // Current word k-1 and the look-ahead word k, as sample arrays (registers).
    ap_uint<B> cur_t[L], cur_m[L], cur_b[L];
    ap_uint<B> nx_t[L], nx_m[L], nx_b[L];
    ap_uint<B> pt_t[C], pt_m[C], pt_b[C];           // last C samples of word k-2
#pragma HLS ARRAY_PARTITION variable=cur_t complete
#pragma HLS ARRAY_PARTITION variable=cur_m complete
#pragma HLS ARRAY_PARTITION variable=cur_b complete
#pragma HLS ARRAY_PARTITION variable=nx_t complete
#pragma HLS ARRAY_PARTITION variable=nx_m complete
#pragma HLS ARRAY_PARTITION variable=nx_b complete
#pragma HLS ARRAY_PARTITION variable=pt_t complete
#pragma HLS ARRAY_PARTITION variable=pt_m complete
#pragma HLS ARRAY_PARTITION variable=pt_b complete
    for (int j = 0; j < L; ++j) { cur_t[j] = cur_m[j] = cur_b[j] = 0; nx_t[j] = nx_m[j] = nx_b[j] = 0; }
    for (int j = 0; j < C; ++j) { pt_t[j] = pt_m[j] = pt_b[j] = 0; }

    ap_uint<7> cur_lim = 0;      // samples j < cur_lim of word k-1 are left of the right border
    bool cur_first = false;      // word k-1 is word 0 of its row (left border: j < C)
    ap_int<18> rem = cw1;        // cw1 - k*L for the next word to read
    ap_uint<11> t = 0;           // iteration within the row
    ap_uint<2> r = 0;            // saturating row counter (outputs start at input row 2)
    bool wr_pend = false;        // line-buffer write delayed by one iteration
    ap_uint<11> wr_addr = 0;
    word_t wr_top = 0, wr_mid = 0;

    for (ap_uint<40> n = 0; n < total; ++n) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=0 max=26542080 avg=3317760
#pragma HLS DEPENDENCE variable=lb_top type=inter false
#pragma HLS DEPENDENCE variable=lb_mid type=inter false
        const bool rd = t < S;
        const bool emit = (r == 2) && (t != 0) && (t <= S);

        // (1) delayed line-buffer write of the word read in the previous iteration:
        //     never the address read in this iteration -> no same-cycle collision.
        if (wr_pend) {
            lb_top[wr_addr] = wr_top;
            lb_mid[wr_addr] = wr_mid;
        }
        wr_pend = false;

        // (2) read the next word of the row and the two words above it
        ap_uint<7> nx_lim = 0;
        const bool nx_first = (t == 0);
        if (rd) {
            const word_t w_in = in.read();
            const word_t a = lb_top[t];
            const word_t b = lb_mid[t];
            wr_pend = true; wr_addr = t; wr_top = b; wr_mid = w_in;
            for (int j = 0; j < L; ++j) {
                nx_t[j] = a.range(B * (j + 1) - 1, B * j);
                nx_m[j] = b.range(B * (j + 1) - 1, B * j);
                nx_b[j] = w_in.range(B * (j + 1) - 1, B * j);
            }
            nx_lim = rem <= 0 ? ap_uint<7>(0) : (rem >= L ? ap_uint<7>(L) : ap_uint<7>(rem));
            rem -= L;
        }

        // (3) emit word k-1: L samples in parallel
        if (emit) {
            word_t ws = 0, we = 0, wb = 0;
            for (int j = 0; j < L; ++j) {
                // column j-C, j, j+C of the current word (from the tail / look-ahead where needed)
                const int jl = j - C, jr = j + C;
                ap_uint<B> NW = jl < 0 ? pt_t[jl + C] : cur_t[jl < 0 ? 0 : jl];
                ap_uint<B> Wv = jl < 0 ? pt_m[jl + C] : cur_m[jl < 0 ? 0 : jl];
                ap_uint<B> SW = jl < 0 ? pt_b[jl + C] : cur_b[jl < 0 ? 0 : jl];
                ap_uint<B> NE = jr >= L ? nx_t[jr - L] : cur_t[jr >= L ? 0 : jr];
                ap_uint<B> E  = jr >= L ? nx_m[jr - L] : cur_m[jr >= L ? 0 : jr];
                ap_uint<B> SE = jr >= L ? nx_b[jr - L] : cur_b[jr >= L ? 0 : jr];
                ap_uint<B> os, oe, ob;
                px3<B>(NW, cur_t[j], NE, Wv, cur_m[j], E, SW, cur_b[j], SE, os, oe, ob);
                const bool valid = (j < cur_lim) && (!cur_first || j >= C);
                ws.range(B * (j + 1) - 1, B * j) = valid ? os : ap_uint<B>(0);
                we.range(B * (j + 1) - 1, B * j) = valid ? oe : ap_uint<B>(0);
                wb.range(B * (j + 1) - 1, B * j) = valid ? ob : ap_uint<B>(0);
            }
            o_sh.write(ws); o_ed.write(we); o_bl.write(wb);
        }

        // (4) advance the window by one word
        if (rd) {
            for (int j = 0; j < C; ++j) { pt_t[j] = cur_t[L - C + j]; pt_m[j] = cur_m[L - C + j]; pt_b[j] = cur_b[L - C + j]; }
            for (int j = 0; j < L; ++j) { cur_t[j] = nx_t[j]; cur_m[j] = nx_m[j]; cur_b[j] = nx_b[j]; }
            cur_lim = nx_lim;
            cur_first = nx_first;
        }

        // (5) counters (no % or /)
        if (t == I_last) {
            t = 0;
            rem = cw1;
            if (r != 2) ++r;
        } else {
            ++t;
        }
    }
}

}  // namespace conv3

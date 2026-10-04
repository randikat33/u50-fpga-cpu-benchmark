// fir_v2.cpp - int16 FIR filter kernel for the Alveo U50 (Vitis HLS 2023.1).
//
//   src (m_axi gmem_src, 256-bit) --rd--> s_in --> fir_proc (window + 16 x FIR_TAPS MACs, II=1) --> s_out --wr--> dst
//   coef (m_axi gmem_coef) --> loaded once into registers
//
// Out-of-sample streaming workload of the time-energy model (paper Section 6.8). One CU computes
// 16 outputs per cycle (4.8 Gsample/s = 9.6 GB/s in and out at 300 MHz); two CUs exceed the PCIe
// link, as for AES, so the card is PCIe-bound by design. Numerics: common/fir_ref.hpp, bit for bit
// (the products are exact and the int32 sum wraps, so the adder-tree order does not matter).
#include "fir_v2.h"

static void rd(const fir_word_t* src, fir_cnt_t n_words, hls::stream<fir_word_t>& s_in,
               hls::stream<fir_cnt_t>& n_fir, hls::stream<fir_cnt_t>& n_wr) {
    n_fir.write(n_words);
    n_wr.write(n_words);
    const fir_cnt_t total = n_words + FIR_HW;
RD:
    for (fir_cnt_t i = 0; i < total; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=16777218 avg=4194306
#pragma HLS PIPELINE II=1
        s_in.write(src[i]);     // sequential -> long read bursts
    }
}

static void fir_proc(const fir_word_t* coef, hls::stream<fir_word_t>& s_in, hls::stream<fir_cnt_t>& n_in,
                hls::stream<fir_word_t>& s_out) {
    ap_int<16> c[FIR_TAPS];
#pragma HLS ARRAY_PARTITION variable=c complete
    for (int w = 0; w < FIR_CWORDS; ++w) {
        fir_word_t cw = coef[w];
        for (int j = 0; j < FIR_L; ++j) {
            if (w * FIR_L + j < FIR_TAPS) c[w * FIR_L + j] = ap_int<16>(cw(16 * j + 15, 16 * j));
        }
    }
    const fir_cnt_t n = n_in.read();
    const fir_cnt_t total = n + FIR_HW;
    // window of the last (FIR_HW + 1) words, oldest sample first; the newest word is at [FIR_HW*16 ..]
    ap_int<16> win[(FIR_HW + 1) * FIR_L];
#pragma HLS ARRAY_PARTITION variable=win complete
    for (int j = 0; j < (FIR_HW + 1) * FIR_L; ++j) win[j] = 0;
FIR_MAIN:
    for (fir_cnt_t i = 0; i < total; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=16777218 avg=4194306
#pragma HLS PIPELINE II=1
        fir_word_t w = s_in.read();
        for (int j = 0; j < FIR_HW * FIR_L; ++j) win[j] = win[j + FIR_L];
        for (int j = 0; j < FIR_L; ++j) win[FIR_HW * FIR_L + j] = ap_int<16>(w(16 * j + 15, 16 * j));
        if (i >= FIR_HW) {
            fir_word_t o;
            for (int j = 0; j < FIR_L; ++j) {
                ap_int<32> acc = 0;      // wraps modulo 2^32 like the reference
                for (int k = 0; k < FIR_TAPS; ++k) {
                    ap_int<32> p = c[k] * win[FIR_HW * FIR_L + j - k];   // exact (|p| <= 2^30)
                    acc += p;
                }
                ap_int<32> r = acc + 16384;
                ap_int<17> s = r >> 15;
                ap_int<16> y = (s > 32767) ? ap_int<16>(32767) : (s < -32768) ? ap_int<16>(-32768) : ap_int<16>(s);
                o(16 * j + 15, 16 * j) = ap_uint<16>(y);
            }
            s_out.write(o);
        }
    }
}

static void wr(fir_word_t* dst, hls::stream<fir_word_t>& s_out, hls::stream<fir_cnt_t>& n_in) {
    const fir_cnt_t n = n_in.read();
WR:
    for (fir_cnt_t i = 0; i < n; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16777216 avg=4194304
#pragma HLS PIPELINE II=1
        dst[i] = s_out.read();  // sequential -> long write bursts
    }
}

void fir_v2(const fir_word_t* src, fir_word_t* dst, const fir_word_t* coef, fir_cnt_t n_words) {
#pragma HLS INTERFACE mode=m_axi port=src bundle=gmem_src offset=slave depth=1024 latency=0 max_widen_bitwidth=256 max_read_burst_length=256 num_read_outstanding=16 max_write_burst_length=2 num_write_outstanding=1
#pragma HLS INTERFACE mode=m_axi port=dst bundle=gmem_dst offset=slave depth=1024 latency=0 max_widen_bitwidth=256 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1
#pragma HLS INTERFACE mode=m_axi port=coef bundle=gmem_coef offset=slave depth=2 latency=0 max_read_burst_length=16 num_read_outstanding=1 max_write_burst_length=2 num_write_outstanding=1
#pragma HLS INTERFACE mode=s_axilite port=src bundle=control
#pragma HLS INTERFACE mode=s_axilite port=dst bundle=control
#pragma HLS INTERFACE mode=s_axilite port=coef bundle=control
#pragma HLS INTERFACE mode=s_axilite port=n_words bundle=control
#pragma HLS INTERFACE mode=s_axilite port=return bundle=control
#pragma HLS DATAFLOW
    hls::stream<fir_word_t> s_in("s_in"), s_out("s_out");
    hls::stream<fir_cnt_t> n_fir("n_fir"), n_wr("n_wr");
#pragma HLS STREAM variable=s_in depth=FIR_FIFO_DEPTH
#pragma HLS STREAM variable=s_out depth=FIR_FIFO_DEPTH
#pragma HLS STREAM variable=n_fir depth=2
#pragma HLS STREAM variable=n_wr depth=2
    rd(src, n_words, s_in, n_fir, n_wr);
    fir_proc(coef, s_in, n_fir, s_out);
    wr(dst, s_out, n_wr);
}

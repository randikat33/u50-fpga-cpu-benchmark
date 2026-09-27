// conv3_top.inc - body of one kernel top. Included by conv3_<variant>.cpp after defining
//   CONV3_TOP (function name), CONV3_B (bits/sample), CONV3_L (samples/word),
//   CONV3_C (channels), CONV3_SH (log2 bytes/sample), CONV3_MAXS (line-buffer depth).
// Arguments (XRT index): 0 img_in, 1 out_sharpen, 2 out_edge, 3 out_blur,
//   4 width [px], 5 rows [input rows, >=3], 6 in_words [size of img_in], 7 out_words.
// The kernel reads rows*stride words and writes (rows-2)*stride words per output;
// if the sizes are inconsistent it does nothing (all trip counts 0).
void CONV3_TOP(const conv3::word_t* img_in, conv3::word_t* out_sharpen, conv3::word_t* out_edge,
               conv3::word_t* out_blur, unsigned width, unsigned rows, unsigned in_words, unsigned out_words) {
#pragma HLS INTERFACE mode=m_axi port=img_in bundle=gmem_in offset=slave max_widen_bitwidth=512 max_read_burst_length=256 num_read_outstanding=16 max_write_burst_length=2 num_write_outstanding=1 latency=0
#pragma HLS INTERFACE mode=m_axi port=out_sharpen bundle=gmem_sh offset=slave max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE mode=m_axi port=out_edge bundle=gmem_ed offset=slave max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE mode=m_axi port=out_blur bundle=gmem_bl offset=slave max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE mode=s_axilite port=img_in bundle=control
#pragma HLS INTERFACE mode=s_axilite port=out_sharpen bundle=control
#pragma HLS INTERFACE mode=s_axilite port=out_edge bundle=control
#pragma HLS INTERFACE mode=s_axilite port=out_blur bundle=control
#pragma HLS INTERFACE mode=s_axilite port=width bundle=control
#pragma HLS INTERFACE mode=s_axilite port=rows bundle=control
#pragma HLS INTERFACE mode=s_axilite port=in_words bundle=control
#pragma HLS INTERFACE mode=s_axilite port=out_words bundle=control
#pragma HLS INTERFACE mode=s_axilite port=return bundle=control
#pragma HLS DATAFLOW

    hls::stream<conv3::cfg_t> c_rd("c_rd"), c_st("c_st"), c_w0("c_w0"), c_w1("c_w1"), c_w2("c_w2");
#pragma HLS STREAM variable=c_rd depth=2
#pragma HLS STREAM variable=c_st depth=2
#pragma HLS STREAM variable=c_w0 depth=2
#pragma HLS STREAM variable=c_w1 depth=2
#pragma HLS STREAM variable=c_w2 depth=2
    hls::stream<conv3::word_t> s_in("s_in"), s_sh("s_sh"), s_ed("s_ed"), s_bl("s_bl");
#pragma HLS STREAM variable=s_in depth=32
#pragma HLS STREAM variable=s_sh depth=32
#pragma HLS STREAM variable=s_ed depth=32
#pragma HLS STREAM variable=s_bl depth=32
#pragma HLS BIND_STORAGE variable=s_in type=fifo impl=srl
#pragma HLS BIND_STORAGE variable=s_sh type=fifo impl=srl
#pragma HLS BIND_STORAGE variable=s_ed type=fifo impl=srl
#pragma HLS BIND_STORAGE variable=s_bl type=fifo impl=srl

    conv3::cfg_proc<CONV3_C, CONV3_SH>(width, rows, in_words, out_words, c_rd, c_st, c_w0, c_w1, c_w2);
    conv3::reader(img_in, c_rd, s_in);
    conv3::stencil<CONV3_B, CONV3_L, CONV3_C, CONV3_MAXS>(c_st, s_in, s_sh, s_ed, s_bl);
    conv3::writer(out_sharpen, c_w0, s_sh);
    conv3::writer(out_edge, c_w1, s_ed);
    conv3::writer(out_blur, c_w2, s_bl);

#ifndef __SYNTHESIS__
    // C-sim only: every stream must be fully consumed (catches trip-count mismatches).
    if (!s_in.empty() || !s_sh.empty() || !s_ed.empty() || !s_bl.empty()) {
        std::fprintf(stderr, "conv3: unconsumed stream data\n");
        std::abort();
    }
#endif
}

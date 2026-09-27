// aes256ctr.cpp - AES-256-CTR keystream kernel for the Alveo U50 (Vitis HLS 2023.1).
//
// One kernel serves encryption and decryption (CTR: out = in XOR E_K(counter)).
//
//   src (m_axi gmem_src, 512-bit) --rd_proc--> s_data -------------------+
//                                     |                                   v
//                                     +-> n_ks --> ks_proc --> s_ks --> wr_proc --> dst (m_axi gmem_dst)
//   prm (m_axi gmem_prm, 4 words) ----------------^                       ^
//                                     +-> n_wr -----------------------------+
//
// ks_proc is a hand-retimed pipeline: every register is an explicit C++ variable that is
// updated once per loop iteration in reverse stage order, so the loop body is the RTL
// next-state function. This pins the register placement exactly (the C-simulation is
// cycle-true for the datapath) and gives:
//   counter pair (1) + ARK0 (1) + 14 rounds x [SubBytes | ShiftRows+MixColumns+ARK] (28)
//   = AES_KS_DEPTH = 30 register stages, II = 1, 4 cores in parallel = 512 bits per cycle.
// Each stage is at most ~2-3 LUT levels (8-input S-box function = LUT6+MUXF7+MUXF8), so the
// datapath has large margin at 300 MHz; Fmax is set by placement/routing and the AXI adapters.
#include "aes256ctr.h"

typedef unsigned char u8;

// ---------------------------------------------------------------------------------------
// S-box as pure logic. Output bit k = bit x[5:0] of the 64-bit constant selected by x[7:6].
// 32 literal constants (the static array is complete-partitioned, so there is no ROM, no
// memory port and no contention between the 896 concurrent lookups); Vivado reduces each
// output bit to one 8-input LUT function (4 x LUT6 + MUXF7/MUXF8).
// In C-simulation the equivalent table is used for speed; the unit test proves both are
// identical for all 256 inputs.
// ---------------------------------------------------------------------------------------
static inline u8 sbox_logic(u8 x) {
#pragma HLS INLINE
    static const unsigned long long C[8][4] = {   // only used by the bit-sliced form
        {0xb14ede67096c6eedULL, 0x68ab4bfa8acb7a13ULL, 0x10bdb210c006eab5ULL, 0x4f1ead396f247a04ULL},
        {0x7bae007d4c53fc7dULL, 0xe61a4c5e97816f7aULL, 0x6a450b2ef33486b4ULL, 0xc870974094ead8a9ULL},
        {0xa16387fb3b48b4c6ULL, 0x23a869a2a428c424ULL, 0x577d64e03b0c3ffbULL, 0xac39b6c0d6ce2efcULL},
        {0x109020a2193d586aULL, 0x2568ea2effa8527dULL, 0xe9da849cf6ac6c1bULL, 0x4e9ddb76c892fb1bULL},
        {0xc2b0f97752b8b11eULL, 0xf7f17a494ce30f58ULL, 0x2624b286bc48ecb4ULL, 0xf210a3aece472e53ULL},
        {0xf8045f7b6d98dd7fULL, 0x6bc2aa4e0d787aa4ULL, 0x7d8dcc4706319e08ULL, 0x54b248130b4f256fULL},
        {0x980a3cc2c2fdb4ffULL, 0xe4851b3bf3ab2560ULL, 0x3f6bcb91b30db559ULL, 0x21e0b83325591782ULL},
        {0x5caa2ec7bf977090ULL, 0xe7bac28f866aac82ULL, 0x4cb3770196ca0329ULL, 0x52379de7b844e3e1ULL}};
    // The array above is complete-partitioned into 32 constants: every access is a constant
    // select (4:1 mux of literals), never a ROM.
#pragma HLS ARRAY_PARTITION variable=C type=complete dim=0
    unsigned hi = x >> 6, lo = x & 63;
    u8 r = 0;
    for (int k = 0; k < 8; ++k) {
#pragma HLS UNROLL
        r |= static_cast<u8>(((C[k][hi] >> lo) & 1u) << k);
    }
    return r;
}

#ifndef __SYNTHESIS__
static const u8 SBOX_SIM[256] = {
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16};
static inline u8 sbox(u8 x) { return SBOX_SIM[x]; }
unsigned char aes_sbox_hw_test(unsigned char x) { return sbox_logic(x); }
#else
static inline u8 sbox(u8 x) {
#pragma HLS INLINE
    return sbox_logic(x);
}
#endif

static inline u8 xtime(u8 x) {
#pragma HLS INLINE
    return static_cast<u8>((x << 1) ^ ((x & 0x80) ? 0x1b : 0x00));
}

// ---------------------------------------------------------------------------------------
// Stage 1: read. Forwards the word count to the other two processes, then streams data.
// ---------------------------------------------------------------------------------------
static void rd_proc(const aes_word_t* in, aes_cnt_t n_words, hls::stream<aes_word_t>& s_data,
                    hls::stream<aes_cnt_t>& n_ks, hls::stream<aes_cnt_t>& n_wr) {
    n_ks.write(n_words);
    n_wr.write(n_words);
    for (aes_cnt_t i = 0; i < n_words; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16777216 avg=4194304
#pragma HLS PIPELINE II=1
        // sequential access -> 256-word read bursts
        s_data.write(in[i]);
    }
}

// ---------------------------------------------------------------------------------------
// Stage 2: keystream generator (hand-retimed, see header comment).
// ---------------------------------------------------------------------------------------
static void ks_proc(const aes_word_t* prm, ap_uint<64> blk_off, hls::stream<aes_cnt_t>& n_in,
                    hls::stream<aes_word_t>& s_ks) {
    // ---- one-time setup (runs before the pipeline; not timing critical) ----
    aes_word_t pw[AES_PARAM_WORDS];
    // 4 registers, sliced with constant ranges below
#pragma HLS ARRAY_PARTITION variable=pw type=complete
    for (int i = 0; i < AES_PARAM_WORDS; ++i) {
#pragma HLS PIPELINE II=1
        // one 4-word read burst
        pw[i] = prm[i];
    }
    u8 rk[15][16];
    // round keys are plain registers (fan-out 4 per bit)
#pragma HLS ARRAY_PARTITION variable=rk type=complete dim=0
    for (int r = 0; r < 15; ++r) {
        // unrolled (both levels)
#pragma HLS UNROLL
        for (int i = 0; i < 16; ++i) {
            // unrolled: j is a constant per copy, so the slices are plain wires
#pragma HLS UNROLL
            int j = 16 * r + i;
            rk[r][i] = pw[j >> 6].range(8 * (j & 63) + 7, 8 * (j & 63)).to_uint();
        }
    }
    ap_uint<128> iv = 0;
    for (int i = 0; i < 16; ++i) {
        // unrolled: constant slices
#pragma HLS UNROLL
        int j = 240 + i;
        iv = (iv << 8) | ap_uint<128>(pw[j >> 6].range(8 * (j & 63) + 7, 8 * (j & 63)));
    }
    ap_uint<128> c0 = iv + ap_uint<128>(blk_off);   // counter of the first block (mod 2^128)

    // ---- pipeline registers ----
    ap_uint<64> H[AES_LANES], L[AES_LANES], Ld[AES_LANES];
    ap_uint<1> cy[AES_LANES];
    // counter registers of the 4 lanes (lane b counts c0+b, c0+b+4, ...)
#pragma HLS ARRAY_PARTITION variable=H type=complete
#pragma HLS ARRAY_PARTITION variable=L type=complete
#pragma HLS ARRAY_PARTITION variable=Ld type=complete
#pragma HLS ARRAY_PARTITION variable=cy type=complete
    for (int b = 0; b < AES_LANES; ++b) {
        // 4 independent initial values
#pragma HLS UNROLL
        ap_uint<128> cb = c0 + ap_uint<128>(b);
        H[b] = cb.range(127, 64);
        L[b] = cb.range(63, 0);
        Ld[b] = 0;
        cy[b] = 0;
    }
    u8 S[15][AES_LANES][16];   // S[r]: state after AddRoundKey of round r (S[0] = ctr ^ rk0)
    u8 A[15][AES_LANES][16];   // A[r]: registered SubBytes output of round r (A[0] unused)
    // every state byte is its own register
#pragma HLS ARRAY_PARTITION variable=S type=complete dim=0
#pragma HLS ARRAY_PARTITION variable=A type=complete dim=0
    for (int r = 0; r < 15; ++r)
        for (int b = 0; b < AES_LANES; ++b)
            for (int i = 0; i < 16; ++i) {
                // clear all state registers in one step (values are don't-care; keeps C defined)
#pragma HLS UNROLL
                S[r][b][i] = 0; A[r][b][i] = 0;
            }
    // valid bits: v[0] = (H,Ld) pair, v[1] = S[0], v[2r] = A[r], v[2r+1] = S[r]; v[29] = S[14]
    ap_uint<AES_KS_DEPTH> v = 0;

    const aes_cnt_t n = n_in.read();
    const ap_uint<33> trips = ap_uint<33>(n) + AES_KS_DEPTH;

    for (ap_uint<33> t = 0; t < trips; ++t) {
#pragma HLS LOOP_TRIPCOUNT min=31 max=16777246 avg=4194334
#pragma HLS PIPELINE II=1
        // (a) output register -> stream (only valid words)
        if (v[AES_KS_DEPTH - 1]) {
            aes_word_t ks = 0;
            for (int b = 0; b < AES_LANES; ++b)
                for (int i = 0; i < 16; ++i) {
                    int j = 16 * b + i;
                    ks.range(8 * j + 7, 8 * j) = S[14][b][i];
                }
            s_ks.write(ks);
        }
        // (b) rounds 14..1, reverse order so every stage sees last cycle's register values
        for (int r = 14; r >= 1; --r) {
            for (int b = 0; b < AES_LANES; ++b) {
                // ShiftRows is wiring
                u8 u[16] = {A[r][b][0], A[r][b][5],  A[r][b][10], A[r][b][15],
                            A[r][b][4], A[r][b][9],  A[r][b][14], A[r][b][3],
                            A[r][b][8], A[r][b][13], A[r][b][2],  A[r][b][7],
                            A[r][b][12], A[r][b][1], A[r][b][6],  A[r][b][11]};
                // temporary wires, never a memory
#pragma HLS ARRAY_PARTITION variable=u type=complete
                if (r != 14) {   // constant per unrolled copy: no MixColumns in the last round
                    for (int c = 0; c < 4; ++c) {
                        u8 a0 = u[4 * c], a1 = u[4 * c + 1], a2 = u[4 * c + 2], a3 = u[4 * c + 3];
                        u8 x = a0 ^ a1 ^ a2 ^ a3;
                        u[4 * c]     = a0 ^ x ^ xtime(a0 ^ a1);
                        u[4 * c + 1] = a1 ^ x ^ xtime(a1 ^ a2);
                        u[4 * c + 2] = a2 ^ x ^ xtime(a2 ^ a3);
                        u[4 * c + 3] = a3 ^ x ^ xtime(a3 ^ a0);
                    }
                }
                for (int i = 0; i < 16; ++i) S[r][b][i] = u[i] ^ rk[r][i];      // register stage B
                for (int i = 0; i < 16; ++i) A[r][b][i] = sbox(S[r - 1][b][i]); // register stage A
            }
        }
        // (c) AddRoundKey 0 on the counter pair (H, Ld) -> S[0]
        for (int b = 0; b < AES_LANES; ++b) {
            for (int i = 0; i < 8; ++i) {
                S[0][b][i]     = u8(H[b].range(63 - 8 * i, 56 - 8 * i).to_uint()) ^ rk[0][i];
                S[0][b][8 + i] = u8(Ld[b].range(63 - 8 * i, 56 - 8 * i).to_uint()) ^ rk[0][8 + i];
            }
        }
        // (d) pipelined 128-bit counters (+4 per word). The carry out of the low half is
        //     registered and added to the high half one cycle later; the low half is
        //     delayed by one register (Ld) so (H, Ld) is always a consistent value.
        for (int b = 0; b < AES_LANES; ++b) {
            Ld[b] = L[b];
            H[b] = H[b] + cy[b];
            cy[b] = (L[b].range(63, 2) == 0x3FFFFFFFFFFFFFFFULL) ? 1 : 0;
            L[b] = L[b] + 4;
        }
        // (e) valid shift register
        v = (v << 1) | ap_uint<AES_KS_DEPTH>(t < n ? 1 : 0);
    }
}

// ---------------------------------------------------------------------------------------
// Stage 3: XOR data with keystream and write.
// ---------------------------------------------------------------------------------------
static void wr_proc(aes_word_t* out, hls::stream<aes_word_t>& s_data, hls::stream<aes_word_t>& s_ks,
                    hls::stream<aes_cnt_t>& n_in) {
    const aes_cnt_t n = n_in.read();
    for (aes_cnt_t i = 0; i < n; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16777216 avg=4194304
#pragma HLS PIPELINE II=1
        // sequential access -> 256-word write bursts
        out[i] = s_data.read() ^ s_ks.read();
    }
}

// ---------------------------------------------------------------------------------------
// Top
// ---------------------------------------------------------------------------------------
void aes256ctr(const aes_word_t* src, aes_word_t* dst, const aes_word_t* prm,
               ap_uint<64> blk_off, aes_cnt_t n_words) {
    // input data: read-only, long bursts, own HBM bank
#pragma HLS INTERFACE mode=m_axi port=src bundle=gmem_src offset=slave depth=1024 latency=0 max_widen_bitwidth=512 max_read_burst_length=256 num_read_outstanding=16 max_write_burst_length=2 num_write_outstanding=1
    // output data: write-only, long bursts, own HBM bank
#pragma HLS INTERFACE mode=m_axi port=dst bundle=gmem_dst offset=slave depth=1024 latency=0 max_widen_bitwidth=512 max_write_burst_length=256 num_write_outstanding=16 max_read_burst_length=2 num_read_outstanding=1
    // parameter buffer (round keys + IV): one 4-word read per call, minimal adapter
#pragma HLS INTERFACE mode=m_axi port=prm bundle=gmem_prm offset=slave depth=4 latency=0 max_widen_bitwidth=512 max_read_burst_length=16 num_read_outstanding=1 max_write_burst_length=2 num_write_outstanding=1
    // control register map
#pragma HLS INTERFACE mode=s_axilite port=src bundle=control
#pragma HLS INTERFACE mode=s_axilite port=dst bundle=control
#pragma HLS INTERFACE mode=s_axilite port=prm bundle=control
#pragma HLS INTERFACE mode=s_axilite port=blk_off bundle=control
#pragma HLS INTERFACE mode=s_axilite port=n_words bundle=control
#pragma HLS INTERFACE mode=s_axilite port=return bundle=control

    // canonical dataflow: three processes, point-to-point streams, no feedback
#pragma HLS DATAFLOW
    hls::stream<aes_word_t> s_data("s_data");
    hls::stream<aes_word_t> s_ks("s_ks");
    hls::stream<aes_cnt_t> n_ks("n_ks");
    hls::stream<aes_cnt_t> n_wr("n_wr");
    // data waits ~AES_KS_DEPTH words for its keystream; LUTRAM keeps BRAM free
#pragma HLS STREAM variable=s_data depth=AES_DATA_FIFO_DEPTH
#pragma HLS BIND_STORAGE variable=s_data type=fifo impl=lutram
    // keystream -> writer; absorbs AXI write back-pressure
#pragma HLS STREAM variable=s_ks depth=AES_KS_FIFO_DEPTH
#pragma HLS BIND_STORAGE variable=s_ks type=fifo impl=lutram
    // one-shot word-count channels
#pragma HLS STREAM variable=n_ks depth=2
#pragma HLS STREAM variable=n_wr depth=2

    rd_proc(src, n_words, s_data, n_ks, n_wr);
    ks_proc(prm, blk_off, n_ks, s_ks);
    wr_proc(dst, s_data, s_ks, n_wr);
}

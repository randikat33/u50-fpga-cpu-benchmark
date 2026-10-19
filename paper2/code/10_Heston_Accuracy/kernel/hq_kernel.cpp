// hq_kernel.cpp - paper-2 Heston kernels (Vitis HLS 2023.1, Alveo U50): single-asset Heston call,
// three strikes (each from its call or put payoff, per the put mask; the host applies put-call parity), Euler full truncation (HQ_SCHEME=0, top hq_ft) or Andersen QE (HQ_SCHEME=1, hq_qe).
//
//   m_axi fpar --> load (32 floats)
//   DATAFLOW  split --job--> lane l (II=1, IL interleaved pair slots: one (pair, step) per clock cycle;
//                 |              |fin           both antithetic sides, two xoshiro draws, two ICDF reads)
//                 |            settle l  (payoff of the finished pair at three strikes, one strike per
//                 |              |res            cycle, exact 128-bit sums of m and of m^2 per pair)      ... for l = 0 .. L-1
//                 +----------> collect (sums the L lane results)
//   write out (14 x uint64)
//
// Numerics are those of common/hq_model.hpp, bit for bit (test/tb_kernel.cpp): the same statements in
// the same order; integer pieces (xoshiro, ICDF, exp(X/2) from paper 1's mc_heston_v2; the fixed-point
// log) use ap_int; the state X (and FT's U) is an exact int64 sum.
// Each lane has its own settle unit, so the output rate is L pairs per 3 cycles: lanes run at full speed
// for M >= 3 steps (every setting of the protocol except one cheap P1 setting with M = 2).
#include "hq_kernel.h"
#include <ap_fixed.h>
#include <ap_int.h>
#include <hls_stream.h>
#include <cmath>
#include "mc_tables.h"
#include "hq_log_tables.h"

#define L HQ_LANES
#define IL HQ_IL
#if HQ_LANES < 1 || HQ_LANES > HQ_MAX_LANES
#error "HQ_LANES must be 1..64"
#endif

typedef ap_uint<32> u32;
typedef long long i64;

// float parameter indices (hq::FparIdx)
enum { P_X0 = 0, P_D2 = 1, P_QS = 2, P_K = 3, P_U0 = 6, P_KDT = 7, P_KTH2 = 8, P_C1H = 9, P_C2Q = 10,
       P_L2 = 11, P_V0 = 12, P_TH = 13, P_EK = 14, P_QC1 = 15, P_QC2 = 16, P_K0 = 17, P_K1 = 18,
       P_K2 = 19, P_K3 = 20, P_K4 = 21, P_ZS = 22, P_ZV = 23, P_IPC = 24, P_PUTS = 25 };

// Start-up values reach each lane and settle unit as a short sequence of 32-bit words, and the lane
// results return as 64-bit words: wide one-shot FIFOs (785 bits for the lane job) failed timing at
// 300 MHz in the first build because one control signal had to reach every bit (phase 3, step 2).
#define JOB_WORDS 26      // 16 constants, 4 keys, base, n (2 words), M, U0 (2 words)
#define SET_WORDS 7       // X0, D2, QS, K0, K1, K2, put mask
#define RES_WORDS 14      // per strike s1 lo, hi, sp lo, hi; pairs; overflows
struct FinPair { i64 XA, XB; };
struct Res { ap_uint<128> s1[3], sp[3]; ap_uint<64> pairs, ovf; };

static inline u32 fbits(float f) {
#pragma HLS INLINE
    union { float f; uint32_t u; } cv;
    cv.f = f;
    return cv.u;
}
static inline float bitsf(u32 w) {
#pragma HLS INLINE
    union { float f; uint32_t u; } cv;
    cv.u = w.to_uint();
    return cv.f;
}

static const float XQ = 1099511627776.0f;            // 2^40
static const float XQI = 9.094947017729282e-13f;     // 2^-40
static const float UQ = 288230376151711744.0f;       // 2^58
static const float UQI = 3.469446951953614e-18f;     // 2^-58

// ------------------------------------------------------------------ helpers from paper 1 (mc_heston_v2.cpp)
static inline u32 rotl32(u32 x, int k) {
#pragma HLS INLINE
    return (x << k) | (x >> (32 - k));
}
static inline u32 fmix32(u32 h) {
#pragma HLS INLINE
    h ^= h >> 16;
    h = h * u32(0x85ebca6bu);
    h ^= h >> 13;
    h = h * u32(0xc2b2ae35u);
    h ^= h >> 16;
    return h;
}
static inline u32 xo_next_hw(u32& s0, u32& s1, u32& s2, u32& s3) {
#pragma HLS INLINE
    u32 m5 = (s1 << 2) + s1;
    u32 r7 = rotl32(m5, 7);
    u32 res = (r7 << 3) + r7;
    u32 t = s1 << 9;
    s2 ^= s0; s3 ^= s1; s1 ^= s2; s0 ^= s3;
    s2 ^= t;
    s3 = rotl32(s3, 11);
    return res;
}
static inline ap_int<24> icdf_hw(u32 u, const ap_uint<54> rom[2048]) {
#pragma HLS INLINE
    ap_uint<1> sgn = u[31];
    ap_uint<31> lo = u(30, 0);
    ap_uint<31> mi = sgn ? ap_uint<31>(~lo) : lo;
    ap_uint<31> mcopy = mi;
    ap_uint<5> lz = mcopy.countLeadingZeros();
    ap_uint<31> n = mi << lz;
    ap_uint<11> idx = (lz, ap_uint<6>(n(29, 24)));
    ap_int<16> x = ap_int<16>(ap_uint<15>(n(23, 9)));
    ap_uint<54> w = rom[idx];
    ap_int<26> c0 = ap_uint<25>(w(53, 29));
    ap_int<17> c1 = ap_int<17>(w(28, 12));
    ap_int<12> c2 = ap_int<12>(w(11, 0));
    ap_int<28> m1 = c2 * x;
    ap_int<18> b = c1 + ap_int<13>(m1 >> 15);
    ap_int<32> m2 = b * x;
    ap_int<27> zi = c0 + ap_int<17>(m2 >> 15);
    ap_int<25> zr = (zi + 2) >> 2;
    return sgn ? ap_int<24>(zr) : ap_int<24>(-zr);
}
static inline float fexp_half_hw(float X, const ap_uint<32> T[256], const float P2[256]) {
#pragma HLS INLINE
    float xc0 = (X <= 128.0f) ? X : 128.0f;
    float xc = (X >= -128.0f) ? xc0 : -128.0f;
    ap_fixed<32, 9> xf = xc;
    ap_int<32> xq = xf.range(31, 0);
    ap_int<63> y = xq * ap_int<32>(1549082005);
    ap_int<9> n = y >> 54;
    ap_uint<32> fr = y(53, 22);
    ap_uint<8> ii = fr(31, 24);
    ap_uint<24> fl = fr(23, 0);
    ap_uint<56> dp = fl * ap_uint<32>(2977044472u);
    ap_uint<24> d = dp >> 32;
    ap_uint<48> d2 = d * d;
    ap_uint<16> dd = d2 >> 33;
    ap_uint<25> g = d + dd;
    ap_uint<32> Ti = T[ii];
    ap_uint<57> Tg = Ti * g;
    ap_uint<33> Mm = Ti + (Tg >> 32);
    if (Mm > ap_uint<33>(4294967167ull)) Mm = 4294967167ull;
    ap_uint<24> m24 = (Mm + 128) >> 8;
    ap_int<32> m24s = m24;
    float mf = m24s.to_int();
    ap_uint<8> k = n + 128;
    float p2 = P2[k];
    float e = mf * p2;
    return e;
}

// ------------------------------------------------------------------ new pieces (hq_model.hpp)
// float -> fixed point: exact scale by a power of two, then truncation (hq::to_q)
static inline i64 to_q_hw(float v, float q) {
#pragma HLS INLINE
    float s = v * q;
    return (i64)s;
}
// hq::hq_ln: ln(n * 2^sc), n >= 1
static inline float hq_ln_hw(ap_uint<64> n, int sc, const ap_uint<32> T[1024], const ap_uint<32> R[1024]) {
#pragma HLS INLINE
    ap_uint<64> ncopy = n;
    ap_uint<7> lz = ncopy.countLeadingZeros();
    ap_uint<64> top = n << lz;
    ap_int<16> e = ap_int<16>(63) - ap_int<16>(lz) + ap_int<16>(sc);
    ap_uint<32> fr = top(62, 31);
    ap_uint<10> i = fr(31, 22);
    ap_uint<22> rem = fr(21, 0);
    ap_uint<64> prod = ap_uint<64>(rem) * ap_uint<64>(R[i]) + ap_uint<64>(1ull << 30);
    ap_uint<64> d = prod >> 31;
    ap_uint<64> dsq = d * d + ap_uint<64>(1ull << 32);
    ap_uint<64> d2 = dsq >> 33;
    ap_int<64> Lq = ap_int<64>(T[i]) + ap_int<64>(d) - ap_int<64>(d2);
    ap_int<64> tot = ap_int<64>(e) * ap_int<64>(HQ_LN2_Q40) + Lq * 256;
    i64 tl = tot.to_int64();
    float tf = (float)tl;
    float r = tf * XQI;                       // 2^-40, exact
    return r;
}
static inline float hq_lnf_hw(float x, const ap_uint<32> T[1024], const ap_uint<32> R[1024]) {
#pragma HLS INLINE
    union { float f; uint32_t u; } cv;
    cv.f = x;
    ap_uint<32> b = cv.u;
    ap_int<16> ex = ap_int<16>(ap_uint<8>(b(30, 23))) - 127;
    ap_uint<64> m = ap_uint<64>(ap_uint<24>((ap_uint<1>(1), ap_uint<23>(b(22, 0)))));
    return hq_ln_hw(m, int(ex) - 23, T, R);
}

// constants c[] of a lane, per scheme (first 16 job words)
#if HQ_SCHEME == 0
enum { C_KDT = 0, C_KTH2, C_C1H, C_C2Q, C_L2 };
#else
enum { C_TH = 0, C_EK, C_QC1, C_QC2, C_K0, C_K1, C_K2, C_K3, C_K4, C_ZS, C_ZV, C_IPC, C_V0 };
#endif

#if HQ_SCHEME == 0
// hq::ft_side
static inline void ft_side_hw(const float c[16], i64& Xi, i64& Ui, float zs, float w) {
#pragma HLS INLINE
    i64 upi = Ui > 0 ? Ui : 0;
    float upf = (float)upi;
    float up = upf * UQI;
    float P = std::sqrt(up);
    float g = up * c[C_KDT];
    float h = c[C_KTH2] - g;
    float e = P * w;
    float du = h + e;
    float f = P * zs;
    float y = f - up;
    Xi = Xi + to_q_hw(y, XQ);
    Ui = Ui + to_q_hw(du, UQ);
}
#else
// hq::qe_side; uv = variance uniform of this side, zvn = icdf(uv) as float, zx = 2 Z of this side
static inline void qe_side_hw(const float c[16], i64& Xi, float& V, u32 uv, float zvn, float zx,
                              const ap_uint<32> T[1024], const ap_uint<32> R[1024]) {
#pragma HLS INLINE
    float m1 = V - c[C_TH];
    float m2 = m1 * c[C_EK];
    float m = c[C_TH] + m2;
    float s2a = V * c[C_QC1];
    float s2 = s2a + c[C_QC2];
    float mm = m * m;
    float t = mm / s2;
    float ip = t + t;
    float ipm1 = ip - 1.0f;
    float pr = ip * ipm1;
    float prc = pr > 0.0f ? pr : 0.0f;
    float sq = std::sqrt(prc);
    float b2 = ipm1 + sq;
    float den = 1.0f + b2;
    float a = m / den;
    float b2c = b2 > 0.0f ? b2 : 0.0f;
    float b = std::sqrt(b2c);
    float zv = zvn * c[C_ZV];
    float tq = b + zv;
    float t2 = tq * tq;
    float vq = a * t2;
    float ip2 = ip + ip;
    float dd = 2.0f + ip;
    float opp = ip2 / dd;
    u32 cu = ~uv;
    ap_uint<32> cua = cu;
    float cuf = (float)(i64)cua.to_uint64();
    float omu = cuf * 2.3283064365386963e-10f;            // 2^-32
    float lo = hq_lnf_hw(opp, T, R);
    ap_uint<64> arg = (ap_uint<64>(cua) << 1) + 1;
    float lu = hq_ln_hw(arg, -33, T, R);
    float df = lo - lu;
    float ratio = m / opp;
    float vex = ratio * df;
    float ve = (omu < opp) ? vex : 0.0f;
    float vn = ip >= c[C_IPC] ? vq : ve;
    float c1 = c[C_K1] * V;
    float c2 = c[C_K2] * vn;
    float s = c[C_K0] + c1;
    float s3 = s + c2;
    float d3 = c[C_K3] * V;
    float d4 = c[C_K4] * vn;
    float dv = d3 + d4;
    float sd = std::sqrt(dv);
    float y = sd * zx;
    float inc = s3 + y;
    Xi = Xi + to_q_hw(inc, XQ);
    V = vn;
}
#endif

// ------------------------------------------------------------------ split
static void split(const float fp[32], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint64_t key_lo,
                  uint64_t key_hi, hls::stream<u32> job[L], hls::stream<u32> sj[L]) {
    float c[16];
#pragma HLS ARRAY_PARTITION variable=c complete
    for (int i = 0; i < 16; ++i) c[i] = 0.0f;
    i64 U0 = 0;
#if HQ_SCHEME == 0
    c[C_KDT] = fp[P_KDT]; c[C_KTH2] = fp[P_KTH2]; c[C_C1H] = fp[P_C1H]; c[C_C2Q] = fp[P_C2Q];
    c[C_L2] = fp[P_L2];
    U0 = to_q_hw(fp[P_U0], UQ);
#else
    c[C_TH] = fp[P_TH]; c[C_EK] = fp[P_EK]; c[C_QC1] = fp[P_QC1]; c[C_QC2] = fp[P_QC2];
    c[C_K0] = fp[P_K0]; c[C_K1] = fp[P_K1]; c[C_K2] = fp[P_K2]; c[C_K3] = fp[P_K3];
    c[C_K4] = fp[P_K4]; c[C_ZS] = fp[P_ZS]; c[C_ZV] = fp[P_ZV]; c[C_IPC] = fp[P_IPC];
    c[C_V0] = fp[P_V0];
#endif
    ap_uint<64> u0w = (ap_uint<64>)U0;
    ap_uint<33> n = n_pairs;
    ap_uint<33> q = n / L;
    ap_uint<33> rem = n - q * L;
    u32 base = pair_base;
    u32 setw[SET_WORDS];
#pragma HLS ARRAY_PARTITION variable=setw complete
    setw[0] = fbits(fp[P_X0]); setw[1] = fbits(fp[P_D2]); setw[2] = fbits(fp[P_QS]);
    setw[3] = fbits(fp[P_K + 0]); setw[4] = fbits(fp[P_K + 1]); setw[5] = fbits(fp[P_K + 2]);
    setw[6] = (u32)fp[P_PUTS] & 7u;   // hq::put_mask
SPLIT:
    for (int l = 0; l < L; ++l) {
        ap_uint<33> nl = q + (ap_uint<33>(l) < rem ? 1 : 0);
        u32 jw[JOB_WORDS];
#pragma HLS ARRAY_PARTITION variable=jw complete
        for (int i = 0; i < 16; ++i) jw[i] = fbits(c[i]);
        jw[16] = key_lo & 0xFFFFFFFFu; jw[17] = key_lo >> 32; jw[18] = key_hi & 0xFFFFFFFFu; jw[19] = key_hi >> 32;
        jw[20] = base;
        jw[21] = nl(31, 0);
        jw[22] = nl(32, 32);
        jw[23] = steps;
        jw[24] = u0w(31, 0);
        jw[25] = u0w(63, 32);
        for (int i = 0; i < JOB_WORDS; ++i) {
#pragma HLS PIPELINE II=1
            job[l].write(jw[i]);
        }
        for (int i = 0; i < SET_WORDS; ++i) {
#pragma HLS PIPELINE II=1
            sj[l].write(setw[i]);
        }
        base += nl(31, 0);
    }
}

// ------------------------------------------------------------------ lane (loop interleaving)
static void lane(hls::stream<u32>& jin, hls::stream<FinPair>& fout, hls::stream<ap_uint<33> >& nout) {
    static const ap_uint<54> rom[2048] = MC_ICDF_ROM_INIT;
#pragma HLS BIND_STORAGE variable=rom type=rom_2p impl=bram
#if HQ_SCHEME == 1
    // two log calls per side and cycle: one dual-port copy of each table per side
    static const ap_uint<32> TA[1024] = HQ_LN_T_INIT;
    static const ap_uint<32> RA[1024] = HQ_LN_R_INIT;
    static const ap_uint<32> TB[1024] = HQ_LN_T_INIT;
    static const ap_uint<32> RB[1024] = HQ_LN_R_INIT;
#pragma HLS BIND_STORAGE variable=TA type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=RA type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=TB type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=RB type=rom_2p impl=bram
#endif
    float c[16];
#pragma HLS ARRAY_PARTITION variable=c complete
    u32 jw[JOB_WORDS];
#pragma HLS ARRAY_PARTITION variable=jw complete
JOB_READ:
    for (int i = 0; i < JOB_WORDS; ++i) {
#pragma HLS PIPELINE II=1
        jw[i] = jin.read();
    }
    for (int i = 0; i < 16; ++i) c[i] = bitsf(jw[i]);
    struct { u32 k0, k1, k2, k3, base; ap_uint<33> n; ap_uint<16> M; i64 U0; } J;
    J.k0 = jw[16]; J.k1 = jw[17]; J.k2 = jw[18]; J.k3 = jw[19];
    J.base = jw[20];
    J.n = (ap_uint<33>(jw[22](0, 0)) << 32) | ap_uint<33>(jw[21]);
    J.M = jw[23](15, 0);
    ap_uint<64> u0w = (ap_uint<64>(jw[25]) << 32) | ap_uint<64>(jw[24]);
    J.U0 = (i64)u0w.to_uint64();
    nout.write(J.n);

    i64 ringXA[IL], ringXB[IL];
#if HQ_SCHEME == 0
    i64 ringUA[IL], ringUB[IL];
#pragma HLS BIND_STORAGE variable=ringUA type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringUB type=ram_s2p impl=bram
#else
    float ringVA[IL], ringVB[IL];
#pragma HLS BIND_STORAGE variable=ringVA type=ram_s2p impl=lutram
#pragma HLS BIND_STORAGE variable=ringVB type=ram_s2p impl=lutram
#endif
    u32 ringR[IL][4];
    ap_uint<17> ringC[IL];   // bit16 valid, bits15:0 steps done
#pragma HLS ARRAY_RESHAPE variable=ringR complete dim=2
#pragma HLS BIND_STORAGE variable=ringXA type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringXB type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringR type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringC type=ram_s2p impl=lutram

RING_INIT:
    for (int s = 0; s < IL; ++s) {
#pragma HLS PIPELINE II=1
        ringC[s] = 0;
        ringR[s][0] = 0; ringR[s][1] = 0; ringR[s][2] = 0; ringR[s][3] = 0;
        ringXA[s] = 0; ringXB[s] = 0;
#if HQ_SCHEME == 0
        ringUA[s] = 0; ringUB[s] = 0;
#else
        ringVA[s] = 0.0f; ringVB[s] = 0.0f;
#endif
    }

    // exact trip count: the last pair (index n-1) starts in wave W at slot s and finishes M-1
    // visits later: T = (W*M + M - 1)*IL + s + 1
    ap_uint<64> Tn = 0;
    if (J.n != 0) {
        ap_uint<33> nm1 = J.n - 1;
        ap_uint<26> W = nm1 >> HQ_IL_LOG2;
        ap_uint<HQ_IL_LOG2> s = nm1(HQ_IL_LOG2 - 1, 0);
        ap_uint<48> wm = W * J.M;
        ap_uint<48> v = wm + J.M - 1;
        Tn = (ap_uint<64>(v) << HQ_IL_LOG2) + s + 1;
    }
    ap_uint<33> nxt = 0;

RING:
    for (ap_uint<64> t = 0; t < Tn; ++t) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=268435456 avg=16777216
#pragma HLS DEPENDENCE variable=ringXA inter false
#pragma HLS DEPENDENCE variable=ringXB inter false
#if HQ_SCHEME == 0
#pragma HLS DEPENDENCE variable=ringUA inter false
#pragma HLS DEPENDENCE variable=ringUB inter false
#else
#pragma HLS DEPENDENCE variable=ringVA inter false
#pragma HLS DEPENDENCE variable=ringVB inter false
#endif
#pragma HLS DEPENDENCE variable=ringR inter false
#pragma HLS DEPENDENCE variable=ringC inter false
        ap_uint<HQ_IL_LOG2> slot = t(HQ_IL_LOG2 - 1, 0);
        ap_uint<17> C = ringC[slot];
        bool valid = C[16];
        bool take = !valid && (nxt < J.n);
        u32 a = J.base + nxt(31, 0);
        if (take) nxt++;
        bool active = valid || take;

        u32 f0 = fmix32(a ^ J.k0), f1 = fmix32(a ^ J.k1), f2 = fmix32(a ^ J.k2), f3 = fmix32(a ^ J.k3);
        if ((f0 | f1 | f2 | f3) == 0) f0 = 0x9E3779B9u;
        u32 s0 = take ? f0 : ringR[slot][0];
        u32 s1 = take ? f1 : ringR[slot][1];
        u32 s2 = take ? f2 : ringR[slot][2];
        u32 s3 = take ? f3 : ringR[slot][3];
        ap_uint<16> cnt = take ? ap_uint<16>(0) : ap_uint<16>(C(15, 0));
        i64 xa = take ? i64(0) : ringXA[slot];
        i64 xb = take ? i64(0) : ringXB[slot];

        u32 u1 = xo_next_hw(s0, s1, s2, s3);
        u32 u2 = xo_next_hw(s0, s1, s2, s3);
#if HQ_SCHEME == 0
        i64 ua = take ? J.U0 : ringUA[slot];
        i64 ub = take ? J.U0 : ringUB[slot];
        ap_int<24> z1 = icdf_hw(u1, rom);
        ap_int<24> z2 = icdf_hw(u2, rom);
        ap_int<32> z1k = z1, z2k = z2;
        float fz1 = z1k.to_int();
        float fz2 = z2k.to_int();
        float zs = c[C_L2] * fz1;
        float a1 = c[C_C1H] * zs;
        float a2 = c[C_C2Q] * fz2;
        float w = a1 + a2;
        float nzs = -zs, nw = -w;
        ft_side_hw(c, xa, ua, zs, w);
        ft_side_hw(c, xb, ub, nzs, nw);
#else
        float va = take ? c[C_V0] : ringVA[slot];
        float vb = take ? c[C_V0] : ringVB[slot];
        ap_int<24> zq = icdf_hw(u1, rom);          // icdf(~u1) == -icdf(u1) exactly
        ap_int<24> zp = icdf_hw(u2, rom);
        ap_int<32> zqk = zq, zpk = zp;
        float fzq = zqk.to_int();
        float fzqn = -fzq;
        float fzp = zpk.to_int();
        float zx = c[C_ZS] * fzp;
        float nzx = -zx;
        qe_side_hw(c, xa, va, u1, fzq, zx, TA, RA);
        qe_side_hw(c, xb, vb, ~u1, fzqn, nzx, TB, RB);
#endif
        ap_uint<16> cnt1 = cnt + 1;
        bool done = active && (cnt1 == J.M);
        if (done) {
            FinPair F;
            F.XA = xa; F.XB = xb;
            fout.write(F);
        }
        ringC[slot] = (active && !done) ? ap_uint<17>((ap_uint<1>(1), cnt1)) : ap_uint<17>(0);
        ringR[slot][0] = s0; ringR[slot][1] = s1; ringR[slot][2] = s2; ringR[slot][3] = s3;
        ringXA[slot] = xa; ringXB[slot] = xb;
#if HQ_SCHEME == 0
        ringUA[slot] = ua; ringUB[slot] = ub;
#else
        ringVA[slot] = va; ringVB[slot] = vb;
#endif
    }
}

// ------------------------------------------------------------------ per-lane payoffs and exact sums
static inline ap_uint<40> pm_hw(float S, float K, float QS, ap_uint<1> neg, ap_uint<1>& ovf) {
#pragma HLS INLINE
    float d = S - K;
    if (neg) d = -d;                    // put payoff K - S (exact negation); the host adds S0 - K e^-rT
    float po = d > 0.0f ? d : 0.0f;
    float pm = po * QS;
    ap_uint<40> r;
    if (!(pm < 1099511627776.0f)) {
        r = ap_uint<40>(~ap_uint<40>(0));
        ovf = 1;
    } else {
        ap_ufixed<40, 40> mf = pm;      // exact: truncation of a non-negative float < 2^40
        r = mf.range(39, 0);
        ovf = 0;
    }
    return r;
}
static void settle(hls::stream<ap_uint<33> >& nin, hls::stream<u32>& sj, hls::stream<FinPair>& in,
                   hls::stream<ap_uint<64> >& rout) {
    static const ap_uint<32> T[256] = MC_EXP_T_INIT;
    static const float P2[256] = MC_EXP_P2_INIT;
#pragma HLS BIND_STORAGE variable=T type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=P2 type=rom_2p impl=bram
    ap_uint<33> n = nin.read();
    struct { float X0, D2, QS, K0, K1, K2; } J;
    J.X0 = bitsf(sj.read()); J.D2 = bitsf(sj.read()); J.QS = bitsf(sj.read());
    J.K0 = bitsf(sj.read()); J.K1 = bitsf(sj.read()); J.K2 = bitsf(sj.read());
    const ap_uint<3> PM = sj.read();
    ap_uint<128> a10 = 0, a11 = 0, a12 = 0, ap0 = 0, ap1 = 0, ap2 = 0;
    ap_uint<64> ovf = 0;
    float SA = 0.0f, SB = 0.0f;
    ap_uint<2> k = 0;
    ap_uint<35> total = ap_uint<35>(n) * 3;
SETTLE:
    for (ap_uint<35> it = 0; it < total; ++it) {
#pragma HLS PIPELINE II=1
        if (k == 0) {
            FinPair p = in.read();
            float xra = (float)p.XA;
            float xrb = (float)p.XB;
            float ra = xra * XQI;
            float rb = xrb * XQI;
            float xa = J.X0 + ra;
            float xb = J.X0 + rb;
            float ta = xa + J.D2;
            float tb = xb + J.D2;
            SA = fexp_half_hw(ta, T, P2);
            SB = fexp_half_hw(tb, T, P2);
        }
        float K = (k == 0) ? J.K0 : (k == 1) ? J.K1 : J.K2;
        ap_uint<1> oa, ob;
        const ap_uint<1> neg = PM[k];
        ap_uint<40> mA = pm_hw(SA, K, J.QS, neg, oa);
        ap_uint<40> mB = pm_hw(SB, K, J.QS, neg, ob);
        ap_uint<41> ps = ap_uint<41>(mA) + mB;
        ap_uint<82> psq = ps * ps;
        if (k == 0) { a10 += ps; ap0 += psq; }
        if (k == 1) { a11 += ps; ap1 += psq; }
        if (k == 2) { a12 += ps; ap2 += psq; }
        ovf += ap_uint<2>(oa) + ap_uint<2>(ob);   // hq::payoff counts every (side, strike) overflow
        k = (k == 2) ? ap_uint<2>(0) : ap_uint<2>(k + 1);
    }
    ap_uint<64> w[RES_WORDS];
#pragma HLS ARRAY_PARTITION variable=w complete
    w[0] = a10(63, 0); w[1] = a10(127, 64); w[2] = ap0(63, 0); w[3] = ap0(127, 64);
    w[4] = a11(63, 0); w[5] = a11(127, 64); w[6] = ap1(63, 0); w[7] = ap1(127, 64);
    w[8] = a12(63, 0); w[9] = a12(127, 64); w[10] = ap2(63, 0); w[11] = ap2(127, 64);
    w[12] = n;
    w[13] = ovf;
RES_WRITE:
    for (int i = 0; i < RES_WORDS; ++i) {
#pragma HLS PIPELINE II=1
        rout.write(w[i]);
    }
}

static void collect(hls::stream<ap_uint<64> > res[L], Res& tot) {
    Res t;
    for (int k = 0; k < 3; ++k) { t.s1[k] = 0; t.sp[k] = 0; }
    t.pairs = 0;
    t.ovf = 0;
COLLECT:
    for (int l = 0; l < L; ++l) {
        ap_uint<64> w[RES_WORDS];
#pragma HLS ARRAY_PARTITION variable=w complete
        for (int i = 0; i < RES_WORDS; ++i) {
#pragma HLS PIPELINE II=1
            w[i] = res[l].read();
        }
        for (int k = 0; k < 3; ++k) {
            ap_uint<128> s1 = (ap_uint<128>(w[4 * k + 1]) << 64) | w[4 * k + 0];
            ap_uint<128> sp = (ap_uint<128>(w[4 * k + 3]) << 64) | w[4 * k + 2];
            t.s1[k] += s1;
            t.sp[k] += sp;
        }
        t.pairs += w[12];
        t.ovf += w[13];
    }
    tot = t;
}

static void engine(const float fp[32], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint64_t key_lo,
                   uint64_t key_hi, Res& tot) {
#pragma HLS DATAFLOW
    hls::stream<u32> job[L];
    hls::stream<u32> sj[L];
    hls::stream<FinPair> fin[L];
    hls::stream<ap_uint<33> > nset[L];
    hls::stream<ap_uint<64> > res[L];
#pragma HLS STREAM variable=job depth=32
#pragma HLS STREAM variable=sj depth=8
#pragma HLS STREAM variable=fin depth=16
#pragma HLS STREAM variable=nset depth=2
#pragma HLS STREAM variable=res depth=16
    split(fp, pair_base, n_pairs, steps, key_lo, key_hi, job, sj);
#if HQ_LANES > 0
    lane(job[0], fin[0], nset[0]);
    settle(nset[0], sj[0], fin[0], res[0]);
#endif
#if HQ_LANES > 1
    lane(job[1], fin[1], nset[1]);
    settle(nset[1], sj[1], fin[1], res[1]);
#endif
#if HQ_LANES > 2
    lane(job[2], fin[2], nset[2]);
    settle(nset[2], sj[2], fin[2], res[2]);
#endif
#if HQ_LANES > 3
    lane(job[3], fin[3], nset[3]);
    settle(nset[3], sj[3], fin[3], res[3]);
#endif
#if HQ_LANES > 4
    lane(job[4], fin[4], nset[4]);
    settle(nset[4], sj[4], fin[4], res[4]);
#endif
#if HQ_LANES > 5
    lane(job[5], fin[5], nset[5]);
    settle(nset[5], sj[5], fin[5], res[5]);
#endif
#if HQ_LANES > 6
    lane(job[6], fin[6], nset[6]);
    settle(nset[6], sj[6], fin[6], res[6]);
#endif
#if HQ_LANES > 7
    lane(job[7], fin[7], nset[7]);
    settle(nset[7], sj[7], fin[7], res[7]);
#endif
#if HQ_LANES > 8
    lane(job[8], fin[8], nset[8]);
    settle(nset[8], sj[8], fin[8], res[8]);
#endif
#if HQ_LANES > 9
    lane(job[9], fin[9], nset[9]);
    settle(nset[9], sj[9], fin[9], res[9]);
#endif
#if HQ_LANES > 10
    lane(job[10], fin[10], nset[10]);
    settle(nset[10], sj[10], fin[10], res[10]);
#endif
#if HQ_LANES > 11
    lane(job[11], fin[11], nset[11]);
    settle(nset[11], sj[11], fin[11], res[11]);
#endif
#if HQ_LANES > 12
    lane(job[12], fin[12], nset[12]);
    settle(nset[12], sj[12], fin[12], res[12]);
#endif
#if HQ_LANES > 13
    lane(job[13], fin[13], nset[13]);
    settle(nset[13], sj[13], fin[13], res[13]);
#endif
#if HQ_LANES > 14
    lane(job[14], fin[14], nset[14]);
    settle(nset[14], sj[14], fin[14], res[14]);
#endif
#if HQ_LANES > 15
    lane(job[15], fin[15], nset[15]);
    settle(nset[15], sj[15], fin[15], res[15]);
#endif
#if HQ_LANES > 16
    lane(job[16], fin[16], nset[16]);
    settle(nset[16], sj[16], fin[16], res[16]);
#endif
#if HQ_LANES > 17
    lane(job[17], fin[17], nset[17]);
    settle(nset[17], sj[17], fin[17], res[17]);
#endif
#if HQ_LANES > 18
    lane(job[18], fin[18], nset[18]);
    settle(nset[18], sj[18], fin[18], res[18]);
#endif
#if HQ_LANES > 19
    lane(job[19], fin[19], nset[19]);
    settle(nset[19], sj[19], fin[19], res[19]);
#endif
#if HQ_LANES > 20
    lane(job[20], fin[20], nset[20]);
    settle(nset[20], sj[20], fin[20], res[20]);
#endif
#if HQ_LANES > 21
    lane(job[21], fin[21], nset[21]);
    settle(nset[21], sj[21], fin[21], res[21]);
#endif
#if HQ_LANES > 22
    lane(job[22], fin[22], nset[22]);
    settle(nset[22], sj[22], fin[22], res[22]);
#endif
#if HQ_LANES > 23
    lane(job[23], fin[23], nset[23]);
    settle(nset[23], sj[23], fin[23], res[23]);
#endif
#if HQ_LANES > 24
    lane(job[24], fin[24], nset[24]);
    settle(nset[24], sj[24], fin[24], res[24]);
#endif
#if HQ_LANES > 25
    lane(job[25], fin[25], nset[25]);
    settle(nset[25], sj[25], fin[25], res[25]);
#endif
#if HQ_LANES > 26
    lane(job[26], fin[26], nset[26]);
    settle(nset[26], sj[26], fin[26], res[26]);
#endif
#if HQ_LANES > 27
    lane(job[27], fin[27], nset[27]);
    settle(nset[27], sj[27], fin[27], res[27]);
#endif
#if HQ_LANES > 28
    lane(job[28], fin[28], nset[28]);
    settle(nset[28], sj[28], fin[28], res[28]);
#endif
#if HQ_LANES > 29
    lane(job[29], fin[29], nset[29]);
    settle(nset[29], sj[29], fin[29], res[29]);
#endif
#if HQ_LANES > 30
    lane(job[30], fin[30], nset[30]);
    settle(nset[30], sj[30], fin[30], res[30]);
#endif
#if HQ_LANES > 31
    lane(job[31], fin[31], nset[31]);
    settle(nset[31], sj[31], fin[31], res[31]);
#endif
#if HQ_LANES > 32
    lane(job[32], fin[32], nset[32]);
    settle(nset[32], sj[32], fin[32], res[32]);
#endif
#if HQ_LANES > 33
    lane(job[33], fin[33], nset[33]);
    settle(nset[33], sj[33], fin[33], res[33]);
#endif
#if HQ_LANES > 34
    lane(job[34], fin[34], nset[34]);
    settle(nset[34], sj[34], fin[34], res[34]);
#endif
#if HQ_LANES > 35
    lane(job[35], fin[35], nset[35]);
    settle(nset[35], sj[35], fin[35], res[35]);
#endif
#if HQ_LANES > 36
    lane(job[36], fin[36], nset[36]);
    settle(nset[36], sj[36], fin[36], res[36]);
#endif
#if HQ_LANES > 37
    lane(job[37], fin[37], nset[37]);
    settle(nset[37], sj[37], fin[37], res[37]);
#endif
#if HQ_LANES > 38
    lane(job[38], fin[38], nset[38]);
    settle(nset[38], sj[38], fin[38], res[38]);
#endif
#if HQ_LANES > 39
    lane(job[39], fin[39], nset[39]);
    settle(nset[39], sj[39], fin[39], res[39]);
#endif
#if HQ_LANES > 40
    lane(job[40], fin[40], nset[40]);
    settle(nset[40], sj[40], fin[40], res[40]);
#endif
#if HQ_LANES > 41
    lane(job[41], fin[41], nset[41]);
    settle(nset[41], sj[41], fin[41], res[41]);
#endif
#if HQ_LANES > 42
    lane(job[42], fin[42], nset[42]);
    settle(nset[42], sj[42], fin[42], res[42]);
#endif
#if HQ_LANES > 43
    lane(job[43], fin[43], nset[43]);
    settle(nset[43], sj[43], fin[43], res[43]);
#endif
#if HQ_LANES > 44
    lane(job[44], fin[44], nset[44]);
    settle(nset[44], sj[44], fin[44], res[44]);
#endif
#if HQ_LANES > 45
    lane(job[45], fin[45], nset[45]);
    settle(nset[45], sj[45], fin[45], res[45]);
#endif
#if HQ_LANES > 46
    lane(job[46], fin[46], nset[46]);
    settle(nset[46], sj[46], fin[46], res[46]);
#endif
#if HQ_LANES > 47
    lane(job[47], fin[47], nset[47]);
    settle(nset[47], sj[47], fin[47], res[47]);
#endif
#if HQ_LANES > 48
    lane(job[48], fin[48], nset[48]);
    settle(nset[48], sj[48], fin[48], res[48]);
#endif
#if HQ_LANES > 49
    lane(job[49], fin[49], nset[49]);
    settle(nset[49], sj[49], fin[49], res[49]);
#endif
#if HQ_LANES > 50
    lane(job[50], fin[50], nset[50]);
    settle(nset[50], sj[50], fin[50], res[50]);
#endif
#if HQ_LANES > 51
    lane(job[51], fin[51], nset[51]);
    settle(nset[51], sj[51], fin[51], res[51]);
#endif
#if HQ_LANES > 52
    lane(job[52], fin[52], nset[52]);
    settle(nset[52], sj[52], fin[52], res[52]);
#endif
#if HQ_LANES > 53
    lane(job[53], fin[53], nset[53]);
    settle(nset[53], sj[53], fin[53], res[53]);
#endif
#if HQ_LANES > 54
    lane(job[54], fin[54], nset[54]);
    settle(nset[54], sj[54], fin[54], res[54]);
#endif
#if HQ_LANES > 55
    lane(job[55], fin[55], nset[55]);
    settle(nset[55], sj[55], fin[55], res[55]);
#endif
#if HQ_LANES > 56
    lane(job[56], fin[56], nset[56]);
    settle(nset[56], sj[56], fin[56], res[56]);
#endif
#if HQ_LANES > 57
    lane(job[57], fin[57], nset[57]);
    settle(nset[57], sj[57], fin[57], res[57]);
#endif
#if HQ_LANES > 58
    lane(job[58], fin[58], nset[58]);
    settle(nset[58], sj[58], fin[58], res[58]);
#endif
#if HQ_LANES > 59
    lane(job[59], fin[59], nset[59]);
    settle(nset[59], sj[59], fin[59], res[59]);
#endif
#if HQ_LANES > 60
    lane(job[60], fin[60], nset[60]);
    settle(nset[60], sj[60], fin[60], res[60]);
#endif
#if HQ_LANES > 61
    lane(job[61], fin[61], nset[61]);
    settle(nset[61], sj[61], fin[61], res[61]);
#endif
#if HQ_LANES > 62
    lane(job[62], fin[62], nset[62]);
    settle(nset[62], sj[62], fin[62], res[62]);
#endif
#if HQ_LANES > 63
    lane(job[63], fin[63], nset[63]);
    settle(nset[63], sj[63], fin[63], res[63]);
#endif
    collect(res, tot);
}

void HQ_TOP(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs, uint32_t steps,
            uint64_t key_lo, uint64_t key_hi) {
#pragma HLS INTERFACE m_axi port=fpar bundle=gmem0 offset=slave depth=32 max_read_burst_length=32 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=out bundle=gmem1 offset=slave depth=14 max_write_burst_length=16 num_write_outstanding=1 latency=0
#pragma HLS INTERFACE s_axilite port=fpar bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=pair_base bundle=control
#pragma HLS INTERFACE s_axilite port=n_pairs bundle=control
#pragma HLS INTERFACE s_axilite port=steps bundle=control
#pragma HLS INTERFACE s_axilite port=key_lo bundle=control
#pragma HLS INTERFACE s_axilite port=key_hi bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    float fp[32];
#pragma HLS ARRAY_PARTITION variable=fp complete
LOAD:
    for (int i = 0; i < 32; ++i) {
#pragma HLS PIPELINE II=1
        fp[i] = fpar[i];
    }
    uint32_t st = steps;
    if (st == 0) st = 1;
    if (st > 65535) st = 65535;
    Res r;
    engine(fp, pair_base, n_pairs, st, key_lo, key_hi, r);
    uint64_t w[14];
    for (int k = 0; k < 3; ++k) {
        w[4 * k + 0] = r.s1[k](63, 0); w[4 * k + 1] = r.s1[k](127, 64);
        w[4 * k + 2] = r.sp[k](63, 0); w[4 * k + 3] = r.sp[k](127, 64);
    }
    w[12] = r.pairs;
    w[13] = uint64_t(r.ovf(31, 0)) | (uint64_t(HQ_LANES) << 32) | (uint64_t(HQ_IL) << 40) | (uint64_t(HQ_SCHEME) << 56);
WRITE:
    for (int i = 0; i < 14; ++i) {
#pragma HLS PIPELINE II=1
        out[i] = w[i];
    }
}

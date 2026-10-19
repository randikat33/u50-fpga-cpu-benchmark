// ha_kernel.cpp - Case A of paper 2 (protocol v0.5): arithmetic-average Asian call under Black-Scholes,
// three strikes, plain and geometric control-variate estimators from the same paths (Vitis HLS 2023.1, U50).
// Derived from kernel/hq_kernel.cpp (split, interleaved lanes, per-lane settle, exact sums) and paper 1's
// 08_MC_Asian (one draw per step, exp in the lane): the FT lane with constant variance is an exact GBM step.
//
//   m_axi fpar --> load (16 floats)
//   DATAFLOW  split --job--> lane l (II=1, IL interleaved pair slots: one (pair, date) per clock cycle:
//                 |              |fin           1 xoshiro draw, 1 ICDF, per side: exact int64 log-price,
//                 |              |              exp(X/2), exact 2^-24 sum of the prices, sum of the log-prices)
//                 |            settle l  (one strike per cycle: arithmetic and geometric payoffs of both
//                 |              |res            sides, exact sums of ys, ys^2, gs, gs^2, ys gs)
//                 +----------> collect
//   write out (32 x uint64)
//
// Numerics are those of common/ha_model.hpp, bit for bit (test/tb_ha_kernel.cpp).
// A lane emits one pair per M cycles; settle takes 3 cycles per pair, so lanes run at full speed for M >= 3.
#include "ha_kernel.h"
#include <ap_fixed.h>
#include <ap_int.h>
#include <hls_stream.h>
#include <cmath>
#include "mc_tables.h"

#define L HA_LANES
#define IL HA_IL
#if HA_LANES < 1 || HA_LANES > HA_MAX_LANES
#error "HA_LANES must be 1..64"
#endif

typedef ap_uint<32> u32;
typedef long long i64;



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
static const float AQ = 16777216.0f;                 // 2^24 (ha::AQ)
static const float S_CLAMP = 4194304.0f;             // 2^22 (ha::S_CLAMP)

// float parameter indices (ha::AIdx)
enum { A_X0 = 0, A_DR = 1, A_VOL = 2, A_K = 3, A_QS = 6, A_CAM = 7, A_CGM = 8 };

#define JOB_WORDS 10      // X0, DR, VOL, 4 keys, base, n (2 words) ... M follows as word 10
#define JOB_N 11
#define SET_WORDS 7       // X0, QS, CAM, CGM, K0, K1, K2
#define RES_WORDS 32
struct FinPair { i64 AA, AB, GA, GB; };
struct Res { ap_uint<128> v[15]; ap_uint<64> pairs, ovf; };

// ------------------------------------------------------------------ split
static void split(const float fp[16], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint64_t key_lo,
                  uint64_t key_hi, hls::stream<u32> job[L], hls::stream<u32> sj[L]) {
    ap_uint<33> n = n_pairs;
    ap_uint<33> q = n / L;
    ap_uint<33> rem = n - q * L;
    u32 base = pair_base;
    u32 setw[SET_WORDS];
#pragma HLS ARRAY_PARTITION variable=setw complete
    setw[0] = fbits(fp[A_X0]); setw[1] = fbits(fp[A_QS]); setw[2] = fbits(fp[A_CAM]); setw[3] = fbits(fp[A_CGM]);
    setw[4] = fbits(fp[A_K + 0]); setw[5] = fbits(fp[A_K + 1]); setw[6] = fbits(fp[A_K + 2]);
SPLIT:
    for (int l = 0; l < L; ++l) {
        ap_uint<33> nl = q + (ap_uint<33>(l) < rem ? 1 : 0);
        u32 jw[JOB_N];
#pragma HLS ARRAY_PARTITION variable=jw complete
        jw[0] = fbits(fp[A_X0]); jw[1] = fbits(fp[A_DR]); jw[2] = fbits(fp[A_VOL]);
        jw[3] = key_lo & 0xFFFFFFFFu; jw[4] = key_lo >> 32; jw[5] = key_hi & 0xFFFFFFFFu; jw[6] = key_hi >> 32;
        jw[7] = base;
        jw[8] = nl(31, 0);
        jw[9] = nl(32, 32);
        jw[10] = steps;
        for (int i = 0; i < JOB_N; ++i) {
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
// ha::side_step: Xi += to_q(inc); S = fexp_half(X0 + Xi 2^-40), clamped; Aq += to_q(S, 2^24); Gq += Xi
static inline void side_step_hw(float X0, float inc, i64& Xi, i64& Aq, i64& Gq, ap_uint<1>& clamp,
                                const ap_uint<32> T[256], const float P2[256]) {
#pragma HLS INLINE
    Xi = Xi + to_q_hw(inc, XQ);
    float xrf = (float)Xi;
    float xr = xrf * XQI;
    float x = X0 + xr;
    float S = fexp_half_hw(x, T, P2);
    bool c = !(S < S_CLAMP);
    float Sc = c ? S_CLAMP : S;
    clamp = c;
    Aq = Aq + to_q_hw(Sc, AQ);
    Gq = Gq + Xi;
}

static void lane(hls::stream<u32>& jin, hls::stream<FinPair>& fout, hls::stream<ap_uint<33> >& nout,
                 hls::stream<ap_uint<32> >& cout) {
    static const ap_uint<54> rom[2048] = MC_ICDF_ROM_INIT;
#pragma HLS BIND_STORAGE variable=rom type=rom_1p impl=bram
    // one exp call site per side and cycle: one dual-port copy of the tables, shared by the two sides
    static const ap_uint<32> T[256] = MC_EXP_T_INIT;
    static const float P2[256] = MC_EXP_P2_INIT;
#pragma HLS BIND_STORAGE variable=T type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=P2 type=rom_2p impl=bram
    u32 jw[JOB_N];
#pragma HLS ARRAY_PARTITION variable=jw complete
JOB_READ:
    for (int i = 0; i < JOB_N; ++i) {
#pragma HLS PIPELINE II=1
        jw[i] = jin.read();
    }
    const float X0 = bitsf(jw[0]), DR = bitsf(jw[1]), VOL = bitsf(jw[2]);
    struct { u32 k0, k1, k2, k3, base; ap_uint<33> n; ap_uint<16> M; } J;
    J.k0 = jw[3]; J.k1 = jw[4]; J.k2 = jw[5]; J.k3 = jw[6];
    J.base = jw[7];
    J.n = (ap_uint<33>(jw[9](0, 0)) << 32) | ap_uint<33>(jw[8]);
    J.M = jw[10](15, 0);
    nout.write(J.n);

    i64 ringXA[IL], ringXB[IL], ringAA[IL], ringAB[IL], ringGA[IL], ringGB[IL];
    u32 ringR[IL][4];
    ap_uint<17> ringC[IL];   // bit16 valid, bits15:0 steps done
#pragma HLS ARRAY_RESHAPE variable=ringR complete dim=2
#pragma HLS BIND_STORAGE variable=ringXA type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringXB type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringAA type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringAB type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringGA type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringGB type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringR type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringC type=ram_s2p impl=lutram

RING_INIT:
    for (int s = 0; s < IL; ++s) {
#pragma HLS PIPELINE II=1
        ringC[s] = 0;
        ringR[s][0] = 0; ringR[s][1] = 0; ringR[s][2] = 0; ringR[s][3] = 0;
        ringXA[s] = 0; ringXB[s] = 0; ringAA[s] = 0; ringAB[s] = 0; ringGA[s] = 0; ringGB[s] = 0;
    }

    // exact trip count: T = (W*M + M - 1)*IL + s + 1 for the last pair (wave W, slot s)
    ap_uint<64> Tn = 0;
    if (J.n != 0) {
        ap_uint<33> nm1 = J.n - 1;
        ap_uint<26> W = nm1 >> HA_IL_LOG2;
        ap_uint<HA_IL_LOG2> s = nm1(HA_IL_LOG2 - 1, 0);
        ap_uint<48> wm = W * J.M;
        ap_uint<48> v = wm + J.M - 1;
        Tn = (ap_uint<64>(v) << HA_IL_LOG2) + s + 1;
    }
    ap_uint<33> nxt = 0;
    ap_uint<32> clamps = 0;

RING:
    for (ap_uint<64> t = 0; t < Tn; ++t) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=268435456 avg=16777216
#pragma HLS DEPENDENCE variable=ringXA inter false
#pragma HLS DEPENDENCE variable=ringXB inter false
#pragma HLS DEPENDENCE variable=ringAA inter false
#pragma HLS DEPENDENCE variable=ringAB inter false
#pragma HLS DEPENDENCE variable=ringGA inter false
#pragma HLS DEPENDENCE variable=ringGB inter false
#pragma HLS DEPENDENCE variable=ringR inter false
#pragma HLS DEPENDENCE variable=ringC inter false
        ap_uint<HA_IL_LOG2> slot = t(HA_IL_LOG2 - 1, 0);
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
        i64 aa = take ? i64(0) : ringAA[slot];
        i64 ab = take ? i64(0) : ringAB[slot];
        i64 ga = take ? i64(0) : ringGA[slot];
        i64 gb = take ? i64(0) : ringGB[slot];

        u32 o = xo_next_hw(s0, s1, s2, s3);
        ap_int<24> z = icdf_hw(o, rom);
        ap_int<32> zk = z;
        float fz = zk.to_int();
        float e = VOL * fz;
        float ia = DR + e;
        float ib = DR - e;
        ap_uint<1> ca, cb;
        side_step_hw(X0, ia, xa, aa, ga, ca, T, P2);
        side_step_hw(X0, ib, xb, ab, gb, cb, T, P2);
        if (active) clamps += ap_uint<2>(ca) + ap_uint<2>(cb);

        ap_uint<16> cnt1 = cnt + 1;
        bool done = active && (cnt1 == J.M);
        if (done) {
            FinPair F;
            F.AA = aa; F.AB = ab; F.GA = ga; F.GB = gb;
            fout.write(F);
        }
        ringC[slot] = (active && !done) ? ap_uint<17>((ap_uint<1>(1), cnt1)) : ap_uint<17>(0);
        ringR[slot][0] = s0; ringR[slot][1] = s1; ringR[slot][2] = s2; ringR[slot][3] = s3;
        ringXA[slot] = xa; ringXB[slot] = xb;
        ringAA[slot] = aa; ringAB[slot] = ab;
        ringGA[slot] = ga; ringGB[slot] = gb;
    }
    cout.write(clamps);
}

// ------------------------------------------------------------------ per-lane payoffs and exact sums
static inline ap_uint<40> pm_hw(float d, float QS, ap_uint<1>& ovf) {
#pragma HLS INLINE
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
                   hls::stream<ap_uint<32> >& cin, hls::stream<ap_uint<64> >& rout) {
    static const ap_uint<32> T[256] = MC_EXP_T_INIT;
    static const float P2[256] = MC_EXP_P2_INIT;
#pragma HLS BIND_STORAGE variable=T type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=P2 type=rom_2p impl=bram
    ap_uint<33> n = nin.read();
    float X0 = bitsf(sj.read()), QS = bitsf(sj.read()), CAM = bitsf(sj.read()), CGM = bitsf(sj.read());
    float Kk[3];
#pragma HLS ARRAY_PARTITION variable=Kk complete
    Kk[0] = bitsf(sj.read()); Kk[1] = bitsf(sj.read()); Kk[2] = bitsf(sj.read());
    ap_uint<128> acc[3][5];
#pragma HLS ARRAY_PARTITION variable=acc complete dim=0
    for (int k = 0; k < 3; ++k) for (int i = 0; i < 5; ++i) acc[k][i] = 0;
    ap_uint<64> ovf = 0;
    float AvA = 0.0f, AvB = 0.0f, GA = 0.0f, GB = 0.0f;
    ap_uint<2> k = 0;
    ap_uint<35> total = ap_uint<35>(n) * 3;
SETTLE:
    for (ap_uint<35> it = 0; it < total; ++it) {
#pragma HLS PIPELINE II=1
        if (k == 0) {
            FinPair p = in.read();
            float afa = (float)p.AA;
            float afb = (float)p.AB;
            AvA = afa * CAM;
            AvB = afb * CAM;
            float gfa = (float)p.GA;
            float gfb = (float)p.GB;
            float gma = gfa * CGM;
            float gmb = gfb * CGM;
            float xga = X0 + gma;
            float xgb = X0 + gmb;
            GA = fexp_half_hw(xga, T, P2);
            GB = fexp_half_hw(xgb, T, P2);
        }
        float K = Kk[k];
        float dya = AvA - K, dyb = AvB - K, dga = GA - K, dgb = GB - K;
        ap_uint<1> o1, o2, o3, o4;
        ap_uint<40> yA = pm_hw(dya, QS, o1);
        ap_uint<40> yB = pm_hw(dyb, QS, o3);
        ap_uint<40> gA = pm_hw(dga, QS, o2);
        ap_uint<40> gB = pm_hw(dgb, QS, o4);
        ap_uint<41> ys = ap_uint<41>(yA) + yB;
        ap_uint<41> gs = ap_uint<41>(gA) + gB;
        ap_uint<82> yy = ys * ys;
        ap_uint<82> gg = gs * gs;
        ap_uint<82> yg = ys * gs;
        for (int kk = 0; kk < 3; ++kk) {
            if (k == kk) {
                acc[kk][0] += ys; acc[kk][1] += yy; acc[kk][2] += gs; acc[kk][3] += gg; acc[kk][4] += yg;
            }
        }
        ovf += ap_uint<3>(o1) + ap_uint<3>(o2) + ap_uint<3>(o3) + ap_uint<3>(o4);
        k = (k == 2) ? ap_uint<2>(0) : ap_uint<2>(k + 1);
    }
    ap_uint<32> cl = cin.read();
    ovf += cl;
    ap_uint<64> w[RES_WORDS];
#pragma HLS ARRAY_PARTITION variable=w complete
    for (int kk = 0; kk < 3; ++kk) {
        for (int i = 0; i < 5; ++i) {
            w[10 * kk + 2 * i] = acc[kk][i](63, 0);
            w[10 * kk + 2 * i + 1] = acc[kk][i](127, 64);
        }
    }
    w[30] = n;
    w[31] = ovf;
RES_WRITE:
    for (int i = 0; i < RES_WORDS; ++i) {
#pragma HLS PIPELINE II=1
        rout.write(w[i]);
    }
}

static void collect(hls::stream<ap_uint<64> > res[L], Res& tot) {
    Res t;
    for (int i = 0; i < 15; ++i) t.v[i] = 0;
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
        for (int i = 0; i < 15; ++i) t.v[i] += (ap_uint<128>(w[2 * i + 1]) << 64) | w[2 * i];
        t.pairs += w[30];
        t.ovf += w[31];
    }
    tot = t;
}

#define HA_LANE_INST(i) \
    lane(job[i], fin[i], nset[i], cl[i]); \
    settle(nset[i], sj[i], fin[i], cl[i], res[i]);

static void engine(const float fp[16], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint64_t key_lo,
                   uint64_t key_hi, Res& tot) {
#pragma HLS DATAFLOW
    hls::stream<u32> job[L];
    hls::stream<u32> sj[L];
    hls::stream<FinPair> fin[L];
    hls::stream<ap_uint<33> > nset[L];
    hls::stream<ap_uint<32> > cl[L];
    hls::stream<ap_uint<64> > res[L];
#pragma HLS STREAM variable=job depth=16
#pragma HLS STREAM variable=sj depth=8
#pragma HLS STREAM variable=fin depth=16
#pragma HLS STREAM variable=nset depth=2
#pragma HLS STREAM variable=cl depth=2
#pragma HLS STREAM variable=res depth=32
    split(fp, pair_base, n_pairs, steps, key_lo, key_hi, job, sj);
#if HA_LANES > 0
    HA_LANE_INST(0)
#endif
#if HA_LANES > 1
    HA_LANE_INST(1)
#endif
#if HA_LANES > 2
    HA_LANE_INST(2)
#endif
#if HA_LANES > 3
    HA_LANE_INST(3)
#endif
#if HA_LANES > 4
    HA_LANE_INST(4)
#endif
#if HA_LANES > 5
    HA_LANE_INST(5)
#endif
#if HA_LANES > 6
    HA_LANE_INST(6)
#endif
#if HA_LANES > 7
    HA_LANE_INST(7)
#endif
#if HA_LANES > 8
    HA_LANE_INST(8)
#endif
#if HA_LANES > 9
    HA_LANE_INST(9)
#endif
#if HA_LANES > 10
    HA_LANE_INST(10)
#endif
#if HA_LANES > 11
    HA_LANE_INST(11)
#endif
#if HA_LANES > 12
    HA_LANE_INST(12)
#endif
#if HA_LANES > 13
    HA_LANE_INST(13)
#endif
#if HA_LANES > 14
    HA_LANE_INST(14)
#endif
#if HA_LANES > 15
    HA_LANE_INST(15)
#endif
#if HA_LANES > 16
    HA_LANE_INST(16)
#endif
#if HA_LANES > 17
    HA_LANE_INST(17)
#endif
#if HA_LANES > 18
    HA_LANE_INST(18)
#endif
#if HA_LANES > 19
    HA_LANE_INST(19)
#endif
#if HA_LANES > 20
    HA_LANE_INST(20)
#endif
#if HA_LANES > 21
    HA_LANE_INST(21)
#endif
#if HA_LANES > 22
    HA_LANE_INST(22)
#endif
#if HA_LANES > 23
    HA_LANE_INST(23)
#endif
#if HA_LANES > 24
    HA_LANE_INST(24)
#endif
#if HA_LANES > 25
    HA_LANE_INST(25)
#endif
#if HA_LANES > 26
    HA_LANE_INST(26)
#endif
#if HA_LANES > 27
    HA_LANE_INST(27)
#endif
#if HA_LANES > 28
    HA_LANE_INST(28)
#endif
#if HA_LANES > 29
    HA_LANE_INST(29)
#endif
#if HA_LANES > 30
    HA_LANE_INST(30)
#endif
#if HA_LANES > 31
    HA_LANE_INST(31)
#endif
#if HA_LANES > 32
    HA_LANE_INST(32)
#endif
#if HA_LANES > 33
    HA_LANE_INST(33)
#endif
#if HA_LANES > 34
    HA_LANE_INST(34)
#endif
#if HA_LANES > 35
    HA_LANE_INST(35)
#endif
#if HA_LANES > 36
    HA_LANE_INST(36)
#endif
#if HA_LANES > 37
    HA_LANE_INST(37)
#endif
#if HA_LANES > 38
    HA_LANE_INST(38)
#endif
#if HA_LANES > 39
    HA_LANE_INST(39)
#endif
#if HA_LANES > 40
    HA_LANE_INST(40)
#endif
#if HA_LANES > 41
    HA_LANE_INST(41)
#endif
#if HA_LANES > 42
    HA_LANE_INST(42)
#endif
#if HA_LANES > 43
    HA_LANE_INST(43)
#endif
#if HA_LANES > 44
    HA_LANE_INST(44)
#endif
#if HA_LANES > 45
    HA_LANE_INST(45)
#endif
#if HA_LANES > 46
    HA_LANE_INST(46)
#endif
#if HA_LANES > 47
    HA_LANE_INST(47)
#endif
#if HA_LANES > 48
    HA_LANE_INST(48)
#endif
#if HA_LANES > 49
    HA_LANE_INST(49)
#endif
#if HA_LANES > 50
    HA_LANE_INST(50)
#endif
#if HA_LANES > 51
    HA_LANE_INST(51)
#endif
#if HA_LANES > 52
    HA_LANE_INST(52)
#endif
#if HA_LANES > 53
    HA_LANE_INST(53)
#endif
#if HA_LANES > 54
    HA_LANE_INST(54)
#endif
#if HA_LANES > 55
    HA_LANE_INST(55)
#endif
#if HA_LANES > 56
    HA_LANE_INST(56)
#endif
#if HA_LANES > 57
    HA_LANE_INST(57)
#endif
#if HA_LANES > 58
    HA_LANE_INST(58)
#endif
#if HA_LANES > 59
    HA_LANE_INST(59)
#endif
#if HA_LANES > 60
    HA_LANE_INST(60)
#endif
#if HA_LANES > 61
    HA_LANE_INST(61)
#endif
#if HA_LANES > 62
    HA_LANE_INST(62)
#endif
#if HA_LANES > 63
    HA_LANE_INST(63)
#endif
    collect(res, tot);
}

void HA_TOP(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs, uint32_t steps,
            uint64_t key_lo, uint64_t key_hi) {
#pragma HLS INTERFACE m_axi port=fpar bundle=gmem0 offset=slave depth=16 max_read_burst_length=16 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=out bundle=gmem1 offset=slave depth=32 max_write_burst_length=32 num_write_outstanding=1 latency=0
#pragma HLS INTERFACE s_axilite port=fpar bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=pair_base bundle=control
#pragma HLS INTERFACE s_axilite port=n_pairs bundle=control
#pragma HLS INTERFACE s_axilite port=steps bundle=control
#pragma HLS INTERFACE s_axilite port=key_lo bundle=control
#pragma HLS INTERFACE s_axilite port=key_hi bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    float fp[16];
#pragma HLS ARRAY_PARTITION variable=fp complete
LOAD:
    for (int i = 0; i < 16; ++i) {
#pragma HLS PIPELINE II=1
        fp[i] = fpar[i];
    }
    uint32_t st = steps;
    if (st == 0) st = 1;
    if (st > 65535) st = 65535;
    Res r;
    engine(fp, pair_base, n_pairs, st, key_lo, key_hi, r);
    uint64_t w[32];
    for (int i = 0; i < 15; ++i) { w[2 * i] = r.v[i](63, 0); w[2 * i + 1] = r.v[i](127, 64); }
    w[30] = r.pairs;
    w[31] = uint64_t(r.ovf(31, 0)) | (uint64_t(HA_LANES) << 32) | (uint64_t(HA_IL) << 40) | (uint64_t(0x20) << 56);
WRITE:
    for (int i = 0; i < 32; ++i) {
#pragma HLS PIPELINE II=1
        out[i] = w[i];
    }
}

// mc_heston_v2.cpp - v2 Monte Carlo Heston basket-option kernel (Vitis HLS 2023.1, Alveo U50)
//
//   m_axi fpar --> load (96 floats)
//                    |
//   DATAFLOW  split --job--> lane 0 ..  lane L-1        (II=1 each, IL=128 interleaved pair slots)
//                 |            |fin[l] (depth IL)        one (pair, step) per clock cycle per lane
//                 |          merge  (rotating priority, emits side A then side B: 1 path/cycle)
//                 |            |
//                 +--prj--> price  (8 x exp(X/2), basket, payoff -> exact integer m, II=1)
//                 |            |
//                 +--tot--> accum  (128-bit exact sums S1, S2, SP; II=1) --> res
//   write out (8 x uint64)
//
// Numerics are those of common/mc_model.hpp, bit for bit (test/tb_kernel.cpp).
// Every pragma carries a one-line reason.
#include "mc_heston_v2.h"
#include <ap_fixed.h>
#include <ap_int.h>
#include <hls_math.h>
#include <hls_stream.h>
#include "mc_tables.h"

#define L MC_LANES
#define IL MC_IL
#define NA 8
#define NZ 16
#if MC_LANES < 1 || MC_LANES > MC_MAX_LANES
#error "MC_LANES must be 1..16"
#endif

typedef ap_uint<32> u32;

// ------------------------------------------------------------------ stream payloads
struct LaneJob {
    float X0[NA], U0[NA], OMK[NA], KTH2[NA], C1H[NA], C2Q[NA];
    float L2[36];
    u32 k0, k1, k2, k3;
    u32 base;             // first pair index of this lane
    ap_uint<33> n;        // pairs in this lane
    ap_uint<16> M;        // steps
};
struct FinPair { float XA[NA], XB[NA]; };
struct FinSide { float X[NA]; };
struct PrJob { float D2[NA]; float K8, QS; ap_uint<1> put; };
struct PM { ap_uint<40> m; ap_uint<1> ovf; };
struct Res { ap_uint<128> s1, s2, sp; ap_uint<64> pairs, ovf; };

// ------------------------------------------------------------------ datapath helpers
static inline u32 rotl32(u32 x, int k) {
#pragma HLS INLINE
    return (x << k) | (x >> (32 - k));
}
static inline u32 fmix32(u32 h) {
#pragma HLS INLINE
    h ^= h >> 16;
    h = h * u32(0x85ebca6bu);   // constant multiply -> shift-add / DSP chosen by HLS
    h ^= h >> 13;
    h = h * u32(0xc2b2ae35u);
    h ^= h >> 16;
    return h;
}

// xoshiro128** step (mc::xo_next); *5 and *9 are shift-adds
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

// segmented quadratic ICDF (mc::icdf); each ROM instance is read by exactly two call sites
static inline ap_int<24> icdf_hw(u32 u, const ap_uint<54> rom[2048]) {
#pragma HLS INLINE
    ap_uint<1> sgn = u[31];
    ap_uint<31> lo = u(30, 0);
    ap_uint<31> mi = sgn ? ap_uint<31>(~lo) : lo;
    ap_uint<31> mcopy = mi;
    ap_uint<5> lz = mcopy.countLeadingZeros();  // 31 for mi == 0
    ap_uint<31> n = mi << lz;
    ap_uint<11> idx = (lz, ap_uint<6>(n(29, 24)));
    ap_int<16> x = ap_int<16>(ap_uint<15>(n(23, 9)));
    ap_uint<54> w = rom[idx];
    ap_int<26> c0 = ap_uint<25>(w(53, 29));
    ap_int<17> c1 = ap_int<17>(w(28, 12));
    ap_int<12> c2 = ap_int<12>(w(11, 0));
    ap_int<28> m1 = c2 * x;                 // |.| < 2^27  (1 DSP48E2)
    ap_int<18> b = c1 + ap_int<13>(m1 >> 15);
    ap_int<32> m2 = b * x;                  // |.| < 2^31 (checked by the table generator)
    ap_int<27> zi = c0 + ap_int<17>(m2 >> 15);
    ap_int<25> zr = (zi + 2) >> 2;
    return sgn ? ap_int<24>(zr) : ap_int<24>(-zr);
}

// exp(X/2) (mc::fexp_half)
static inline float fexp_half_hw(float X, const ap_uint<32> T[256], const float P2[256]) {
#pragma HLS INLINE
    float xc0 = (X <= 128.0f) ? X : 128.0f;
    float xc = (X >= -128.0f) ? xc0 : -128.0f;
    ap_fixed<32, 9> xf = xc;                              // floor(X * 2^23) (AP_TRN)
    ap_int<32> xq = xf.range(31, 0);
    ap_int<63> y = xq * ap_int<32>(1549082005);           // (X/2)/ln2, Q54
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
    float mf = m24s.to_int();                             // exact (< 2^24)
    ap_uint<8> k = n + 128;
    float p2 = P2[k];
    float e = mf * p2;
    return e;
}

// fixed-order pairwise tree sum (mc::tree_sum); n is a constant at every call site
static inline float tree_sum_hw(const float t[NA], int n) {
#pragma HLS INLINE
    float a[8], b[4];
    int na = (n + 1) / 2;
    for (int k = 0; k < na; ++k) {
        if (2 * k + 1 < n) a[k] = t[2 * k] + t[2 * k + 1];
        else a[k] = t[2 * k];
    }
    int nb = (na + 1) / 2;
    for (int k = 0; k < nb; ++k) {
        if (2 * k + 1 < na) b[k] = a[2 * k] + a[2 * k + 1];
        else b[k] = a[2 * k];
    }
    float r;
    if (nb == 1) r = b[0];
    else {
        float c0 = b[0] + b[1];
        if (nb == 2) r = c0;
        else {
            float c1 = (nb == 4) ? (b[2] + b[3]) : b[2];
            r = c0 + c1;
        }
    }
    return r;
}

// one side of one asset (mc::side_step)
static inline void side_hw(float X, float U, float zs, float ww, float omk, float kth2, float& Xo, float& Uo) {
#pragma HLS INLINE
    float P = hls::sqrt(U);
    float g = U * omk;
    float h = g + kth2;
    float e = P * ww;
    float un = h + e;
    float f = P * zs;
    float y = f - U;
    Xo = X + y;
    Uo = un > 0.0f ? un : 0.0f;
}

// ------------------------------------------------------------------ split
static void split(const float fp[96], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint32_t flags,
                  uint64_t key_lo, uint64_t key_hi, hls::stream<LaneJob> job[L], hls::stream<PrJob>& prj,
                  hls::stream<ap_uint<33> >& tot_m, hls::stream<ap_uint<33> >& tot_p, hls::stream<ap_uint<33> >& tot_a) {
    LaneJob J;
    for (int i = 0; i < NA; ++i) {
        J.X0[i] = fp[0 + i]; J.U0[i] = fp[8 + i]; J.OMK[i] = fp[16 + i];
        J.KTH2[i] = fp[24 + i]; J.C1H[i] = fp[32 + i]; J.C2Q[i] = fp[40 + i];
    }
    for (int i = 0; i < 36; ++i) J.L2[i] = fp[56 + i];
    J.k0 = key_lo & 0xFFFFFFFFu; J.k1 = key_lo >> 32; J.k2 = key_hi & 0xFFFFFFFFu; J.k3 = key_hi >> 32;
    J.M = steps;
    ap_uint<33> n = n_pairs;
    ap_uint<33> q = n / L;                 // once per call (not in an II=1 loop)
    ap_uint<33> rem = n - q * L;
    u32 base = pair_base;
    for (int l = 0; l < L; ++l) {
        J.base = base;
        J.n = q + (ap_uint<33>(l) < rem ? 1 : 0);
        job[l].write(J);
        base += J.n(31, 0);
    }
    PrJob P;
    for (int i = 0; i < NA; ++i) P.D2[i] = fp[48 + i];
    P.K8 = fp[92];
    P.QS = fp[93];
    P.put = flags & 1;
    prj.write(P);
    tot_m.write(n);
    tot_p.write(n);
    tot_a.write(n);
}

// ------------------------------------------------------------------ lane (loop interleaving)
static void lane(hls::stream<LaneJob>& jin, hls::stream<FinPair>& fout) {
    static const ap_uint<54> rom0[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<54> rom1[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<54> rom2[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<54> rom3[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<54> rom4[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<54> rom5[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<54> rom6[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<54> rom7[2048] = MC_ICDF_ROM_INIT;
#pragma HLS BIND_STORAGE variable=rom0 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom1 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom2 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom3 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom4 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom5 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom6 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom7 type=rom_2p impl=bram

    LaneJob J = jin.read();
    float X0[NA], U0[NA], OMK[NA], KTH2[NA], C1H[NA], C2Q[NA], L2[36];
#pragma HLS ARRAY_PARTITION variable=X0 complete
#pragma HLS ARRAY_PARTITION variable=U0 complete
#pragma HLS ARRAY_PARTITION variable=OMK complete
#pragma HLS ARRAY_PARTITION variable=KTH2 complete
#pragma HLS ARRAY_PARTITION variable=C1H complete
#pragma HLS ARRAY_PARTITION variable=C2Q complete
#pragma HLS ARRAY_PARTITION variable=L2 complete
    for (int i = 0; i < NA; ++i) {
        X0[i] = J.X0[i]; U0[i] = J.U0[i]; OMK[i] = J.OMK[i];
        KTH2[i] = J.KTH2[i]; C1H[i] = J.C1H[i]; C2Q[i] = J.C2Q[i];
    }
    for (int i = 0; i < 36; ++i) L2[i] = J.L2[i];

    // slot RAMs: one word per slot (reshaped), read at the first stage, written at the last
    float ringXA[IL][NA], ringXB[IL][NA], ringUA[IL][NA], ringUB[IL][NA];
    u32 ringR[IL][4];
    ap_uint<17> ringC[IL];   // bit16 valid, bits15:0 steps done
#pragma HLS ARRAY_RESHAPE variable=ringXA complete dim=2
#pragma HLS ARRAY_RESHAPE variable=ringXB complete dim=2
#pragma HLS ARRAY_RESHAPE variable=ringUA complete dim=2
#pragma HLS ARRAY_RESHAPE variable=ringUB complete dim=2
#pragma HLS ARRAY_RESHAPE variable=ringR complete dim=2
#pragma HLS BIND_STORAGE variable=ringXA type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringXB type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringUA type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringUB type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringR type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringC type=ram_s2p impl=lutram

RING_INIT:
    for (int s = 0; s < IL; ++s) {
#pragma HLS PIPELINE II=1
        ringC[s] = 0;
        ringR[s][0] = 0; ringR[s][1] = 0; ringR[s][2] = 0; ringR[s][3] = 0;
        for (int i = 0; i < NA; ++i) {
            ringXA[s][i] = 0.0f; ringXB[s][i] = 0.0f; ringUA[s][i] = 0.0f; ringUB[s][i] = 0.0f;
        }
    }

    // exact trip count: the last pair (index n-1) starts in wave W at slot s and finishes M-1
    // visits later: T = (W*M + M - 1)*IL + s + 1
    ap_uint<64> T = 0;
    if (J.n != 0) {
        ap_uint<33> nm1 = J.n - 1;
        ap_uint<26> W = nm1 >> MC_IL_LOG2;
        ap_uint<MC_IL_LOG2> s = nm1(MC_IL_LOG2 - 1, 0);
        ap_uint<48> wm = W * J.M;
        ap_uint<48> v = wm + J.M - 1;
        T = (ap_uint<64>(v) << MC_IL_LOG2) + s + 1;
    }
    ap_uint<33> nxt = 0;

RING:
    for (ap_uint<64> t = 0; t < T; ++t) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=268435456 avg=16777216
#pragma HLS DEPENDENCE variable=ringXA inter false
#pragma HLS DEPENDENCE variable=ringXB inter false
#pragma HLS DEPENDENCE variable=ringUA inter false
#pragma HLS DEPENDENCE variable=ringUB inter false
#pragma HLS DEPENDENCE variable=ringR inter false
#pragma HLS DEPENDENCE variable=ringC inter false
        ap_uint<MC_IL_LOG2> slot = t(MC_IL_LOG2 - 1, 0);
        ap_uint<17> C = ringC[slot];
        bool valid = C[16];
        bool take = !valid && (nxt < J.n);
        u32 a = J.base + nxt(31, 0);
        if (take) nxt++;
        bool active = valid || take;

        // feed-forward seeding (computed every cycle, selected when a slot is empty)
        u32 f0 = fmix32(a ^ J.k0), f1 = fmix32(a ^ J.k1), f2 = fmix32(a ^ J.k2), f3 = fmix32(a ^ J.k3);
        if ((f0 | f1 | f2 | f3) == 0) f0 = 0x9E3779B9u;
        u32 s0 = take ? f0 : ringR[slot][0];
        u32 s1 = take ? f1 : ringR[slot][1];
        u32 s2 = take ? f2 : ringR[slot][2];
        u32 s3 = take ? f3 : ringR[slot][3];
        ap_uint<16> cnt = take ? ap_uint<16>(0) : ap_uint<16>(C(15, 0));
        float xa[NA], xb[NA], ua[NA], ub[NA];
#pragma HLS ARRAY_PARTITION variable=xa complete
#pragma HLS ARRAY_PARTITION variable=xb complete
#pragma HLS ARRAY_PARTITION variable=ua complete
#pragma HLS ARRAY_PARTITION variable=ub complete
        for (int i = 0; i < NA; ++i) {
            xa[i] = take ? X0[i] : ringXA[slot][i];
            xb[i] = take ? X0[i] : ringXB[slot][i];
            ua[i] = take ? U0[i] : ringUA[slot][i];
            ub[i] = take ? U0[i] : ringUB[slot][i];
        }

        // 16 draws (unrolled xoshiro128**) -> 16 ICDFs -> float (exact)
        u32 o[NZ];
#pragma HLS ARRAY_PARTITION variable=o complete
        for (int k = 0; k < NZ; ++k) o[k] = xo_next_hw(s0, s1, s2, s3);
        float fz[NZ];
#pragma HLS ARRAY_PARTITION variable=fz complete
        ap_int<24> z[NZ];
        z[0] = icdf_hw(o[0], rom0);   z[1] = icdf_hw(o[1], rom0);
        z[2] = icdf_hw(o[2], rom1);   z[3] = icdf_hw(o[3], rom1);
        z[4] = icdf_hw(o[4], rom2);   z[5] = icdf_hw(o[5], rom2);
        z[6] = icdf_hw(o[6], rom3);   z[7] = icdf_hw(o[7], rom3);
        z[8] = icdf_hw(o[8], rom4);   z[9] = icdf_hw(o[9], rom4);
        z[10] = icdf_hw(o[10], rom5); z[11] = icdf_hw(o[11], rom5);
        z[12] = icdf_hw(o[12], rom6); z[13] = icdf_hw(o[13], rom6);
        z[14] = icdf_hw(o[14], rom7); z[15] = icdf_hw(o[15], rom7);
        for (int k = 0; k < NZ; ++k) {
            ap_int<32> zk = z[k];
            fz[k] = zk.to_int();
        }

        // Cholesky rows (fixed tree order) and variance shocks, shared by both sides
        float xan[NA], xbn[NA], uan[NA], ubn[NA];
        for (int i = 0; i < NA; ++i) {
            float tt[NA];
            for (int j = 0; j < NA; ++j) tt[j] = 0.0f;
            for (int j = 0; j <= i; ++j) tt[j] = L2[i * (i + 1) / 2 + j] * fz[j];
            float zS2 = tree_sum_hw(tt, i + 1);
            float a1 = C1H[i] * zS2;
            float a2 = C2Q[i] * fz[NA + i];
            float w = a1 + a2;
            float nz = -zS2;
            float nw = -w;
            side_hw(xa[i], ua[i], zS2, w, OMK[i], KTH2[i], xan[i], uan[i]);
            side_hw(xb[i], ub[i], nz, nw, OMK[i], KTH2[i], xbn[i], ubn[i]);
        }

        ap_uint<16> cnt1 = cnt + 1;
        bool done = active && (cnt1 == J.M);
        if (done) {
            FinPair F;
            for (int i = 0; i < NA; ++i) { F.XA[i] = xan[i]; F.XB[i] = xbn[i]; }
            fout.write(F);
        }
        ringC[slot] = (active && !done) ? ap_uint<17>((ap_uint<1>(1), cnt1)) : ap_uint<17>(0);
        ringR[slot][0] = s0; ringR[slot][1] = s1; ringR[slot][2] = s2; ringR[slot][3] = s3;
        for (int i = 0; i < NA; ++i) {
            ringXA[slot][i] = xan[i]; ringXB[slot][i] = xbn[i];
            ringUA[slot][i] = uan[i]; ringUB[slot][i] = ubn[i];
        }
    }
}

// ------------------------------------------------------------------ merge (1 path per cycle)
static void merge(hls::stream<ap_uint<33> >& tot, hls::stream<FinPair> fin[L], hls::stream<FinSide>& out) {
    ap_uint<34> total = ap_uint<34>(tot.read()) << 1;
    ap_uint<34> written = 0;
    bool have_b = false;
    FinSide b;
    ap_uint<5> rr = 0;
MERGE:
    while (written < total) {
#pragma HLS PIPELINE II=1
        if (have_b) {
            out.write(b);
            have_b = false;
            written++;
        } else {
            bool ne[L];
            for (int l = 0; l < L; ++l) ne[l] = !fin[l].empty();
            int sel = -1;
            for (int k = 0; k < L; ++k) {
                int l = rr + k;
                if (l >= L) l -= L;
                if (sel < 0 && ne[l]) sel = l;
            }
            if (sel >= 0) {
                FinPair p;
                for (int l = 0; l < L; ++l) {
                    if (l == sel) p = fin[l].read();
                }
                FinSide a;
                for (int i = 0; i < NA; ++i) { a.X[i] = p.XA[i]; b.X[i] = p.XB[i]; }
                out.write(a);
                have_b = true;
                written++;
                rr = (sel + 1 >= L) ? 0 : sel + 1;
            }
        }
    }
}

// ------------------------------------------------------------------ price (1 path per cycle)
static void price(hls::stream<ap_uint<33> >& tot, hls::stream<PrJob>& prj, hls::stream<FinSide>& in,
                  hls::stream<PM>& out) {
    static const ap_uint<32> T0[256] = MC_EXP_T_INIT;
    static const ap_uint<32> T1[256] = MC_EXP_T_INIT;
    static const ap_uint<32> T2[256] = MC_EXP_T_INIT;
    static const ap_uint<32> T3[256] = MC_EXP_T_INIT;
    static const float P0[256] = MC_EXP_P2_INIT;
    static const float P1[256] = MC_EXP_P2_INIT;
    static const float P2[256] = MC_EXP_P2_INIT;
    static const float P3[256] = MC_EXP_P2_INIT;
    ap_uint<34> total = ap_uint<34>(tot.read()) << 1;
    PrJob J = prj.read();
    float D2[NA];
#pragma HLS ARRAY_PARTITION variable=D2 complete
    for (int i = 0; i < NA; ++i) D2[i] = J.D2[i];
PRICE:
    for (ap_uint<34> k = 0; k < total; ++k) {
#pragma HLS PIPELINE II=1
        FinSide s = in.read();
        float xt[NA], S[NA];
#pragma HLS ARRAY_PARTITION variable=xt complete
#pragma HLS ARRAY_PARTITION variable=S complete
        for (int i = 0; i < NA; ++i) xt[i] = s.X[i] + D2[i];
        S[0] = fexp_half_hw(xt[0], T0, P0); S[1] = fexp_half_hw(xt[1], T0, P0);
        S[2] = fexp_half_hw(xt[2], T1, P1); S[3] = fexp_half_hw(xt[3], T1, P1);
        S[4] = fexp_half_hw(xt[4], T2, P2); S[5] = fexp_half_hw(xt[5], T2, P2);
        S[6] = fexp_half_hw(xt[6], T3, P3); S[7] = fexp_half_hw(xt[7], T3, P3);
        float B8 = tree_sum_hw(S, NA);
        float d = J.put ? (J.K8 - B8) : (B8 - J.K8);
        float po = d > 0.0f ? d : 0.0f;
        float pm = po * J.QS;
        PM r;
        if (!(pm < 1099511627776.0f)) {
            r.m = ~ap_uint<40>(0);
            r.ovf = 1;
        } else {
            ap_ufixed<40, 40> mf = pm;    // exact: pm is an integer < 2^40
            r.m = mf.range(39, 0);
            r.ovf = 0;
        }
        out.write(r);
    }
}

// ------------------------------------------------------------------ accumulate (exact)
static void accum(hls::stream<ap_uint<33> >& tot, hls::stream<PM>& in, Res& res) {
    ap_uint<34> total = ap_uint<34>(tot.read()) << 1;
    ap_uint<128> s1 = 0, s2 = 0, sp = 0;
    ap_uint<64> ovf = 0;
    ap_uint<40> mA = 0;
ACCUM:
    for (ap_uint<34> k = 0; k < total; ++k) {
#pragma HLS PIPELINE II=1
        PM r = in.read();
        ap_uint<80> sq = r.m * r.m;
        s1 += r.m;
        s2 += sq;
        if (r.ovf) ovf++;
        if (k[0] == 0) {
            mA = r.m;
        } else {
            ap_uint<41> ps = ap_uint<41>(mA) + r.m;
            ap_uint<82> psq = ps * ps;
            sp += psq;
        }
    }
    res.s1 = s1; res.s2 = s2; res.sp = sp;
    res.pairs = total >> 1;
    res.ovf = ovf;
}

static void engine(const float fp[96], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint32_t flags,
                   uint64_t key_lo, uint64_t key_hi, Res& res) {
#pragma HLS DATAFLOW
    hls::stream<LaneJob> job[L];
    hls::stream<FinPair> fin[L];
    hls::stream<PrJob> prj;
    hls::stream<ap_uint<33> > tot_m, tot_p, tot_a;
    hls::stream<FinSide> sides;
    hls::stream<PM> pms;
#pragma HLS STREAM variable=job depth=2
#pragma HLS STREAM variable=fin depth=MC_IL
#pragma HLS STREAM variable=prj depth=2
#pragma HLS STREAM variable=tot_m depth=2
#pragma HLS STREAM variable=tot_p depth=2
#pragma HLS STREAM variable=tot_a depth=2
#pragma HLS STREAM variable=sides depth=32
#pragma HLS STREAM variable=pms depth=32
    split(fp, pair_base, n_pairs, steps, flags, key_lo, key_hi, job, prj, tot_m, tot_p, tot_a);
    lane(job[0], fin[0]);
#if MC_LANES > 1
    lane(job[1], fin[1]);
#endif
#if MC_LANES > 2
    lane(job[2], fin[2]);
#endif
#if MC_LANES > 3
    lane(job[3], fin[3]);
#endif
#if MC_LANES > 4
    lane(job[4], fin[4]);
#endif
#if MC_LANES > 5
    lane(job[5], fin[5]);
#endif
#if MC_LANES > 6
    lane(job[6], fin[6]);
#endif
#if MC_LANES > 7
    lane(job[7], fin[7]);
#endif
#if MC_LANES > 8
    lane(job[8], fin[8]);
#endif
#if MC_LANES > 9
    lane(job[9], fin[9]);
#endif
#if MC_LANES > 10
    lane(job[10], fin[10]);
#endif
#if MC_LANES > 11
    lane(job[11], fin[11]);
#endif
#if MC_LANES > 12
    lane(job[12], fin[12]);
#endif
#if MC_LANES > 13
    lane(job[13], fin[13]);
#endif
#if MC_LANES > 14
    lane(job[14], fin[14]);
#endif
#if MC_LANES > 15
    lane(job[15], fin[15]);
#endif
    merge(tot_m, fin, sides);
    price(tot_p, prj, sides, pms);
    accum(tot_a, pms, res);
}

void mc_heston_v2(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs,
                  uint32_t steps, uint32_t flags, uint64_t key_lo, uint64_t key_hi) {
#pragma HLS INTERFACE m_axi port=fpar bundle=gmem0 offset=slave depth=96 max_read_burst_length=256 num_read_outstanding=16 latency=0
#pragma HLS INTERFACE m_axi port=out bundle=gmem1 offset=slave depth=8 max_write_burst_length=256 num_write_outstanding=16 latency=0
#pragma HLS INTERFACE s_axilite port=fpar bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=pair_base bundle=control
#pragma HLS INTERFACE s_axilite port=n_pairs bundle=control
#pragma HLS INTERFACE s_axilite port=steps bundle=control
#pragma HLS INTERFACE s_axilite port=flags bundle=control
#pragma HLS INTERFACE s_axilite port=key_lo bundle=control
#pragma HLS INTERFACE s_axilite port=key_hi bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    float fp[96];
#pragma HLS ARRAY_PARTITION variable=fp complete
LOAD:
    for (int i = 0; i < 96; ++i) {
#pragma HLS PIPELINE II=1
        fp[i] = fpar[i];
    }
    Res res;
    uint32_t st = steps;
    if (st == 0) st = 1;             // guard (host validates 1..65535)
    if (st > 65535) st = 65535;
    engine(fp, pair_base, n_pairs, st, flags, key_lo, key_hi, res);
    uint64_t w[8];
    w[0] = res.s1(63, 0);  w[1] = res.s1(127, 64);
    w[2] = res.s2(63, 0);  w[3] = res.s2(127, 64);
    w[4] = res.sp(63, 0);  w[5] = res.sp(127, 64);
    w[6] = res.pairs;
    w[7] = uint64_t(res.ovf(31, 0)) | (uint64_t(MC_LANES) << 32) | (uint64_t(MC_IL) << 40);
WRITE:
    for (int i = 0; i < 8; ++i) {
#pragma HLS PIPELINE II=1
        out[i] = w[i];
    }
}

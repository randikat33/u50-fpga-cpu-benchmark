// mc_asian.cpp - Monte Carlo arithmetic-average Asian option (GBM) kernel (Vitis HLS 2023.1, Alveo U50)
//
// Out-of-sample workload of the time-energy model: predicted before it ran on the card.
//
//   m_axi fpar --> load (16 floats)
//   DATAFLOW  split --job--> lane 0 .. lane L-1      (II=1 each, IL=128 interleaved pair slots:
//                 |            |fin[l]                 one (pair, step) per clock cycle per lane =
//                 |          merge (rotating priority, one pair per cycle)    1 xoshiro draw, 1 ICDF,
//                 +--tot--> settle (payoffs mA, mB -> exact 128-bit sums, II=1)   2 exp(X/2), 2 sums)
//   write out (8 x uint64)
//
// The datapath helpers (xoshiro128**, ICDF, exp) are copied verbatim from 03_MC_Heston/kernel/mc_heston_v2.cpp;
// numerics are those of common/asian_model.hpp, bit for bit (test/tb_kernel.cpp).
// A lane finishes one pair every M cycles, merge/settle take one pair per cycle: full speed needs
// M >= L (the benchmark uses M = 64 and 256); for smaller M the lanes wait on their output FIFOs.
#include "mc_asian.h"
#include <ap_fixed.h>
#include <ap_int.h>
#include <hls_stream.h>
#include "mc_tables.h"

#define L AS_LANES
#define IL AS_IL
#if AS_LANES < 1 || AS_LANES > AS_MAX_LANES
#error "AS_LANES must be 1..64"
#endif

typedef ap_uint<32> u32;

struct LaneJob {
    float X0, DR, VOL;
    u32 k0, k1, k2, k3;
    u32 base;             // first pair index of this lane
    ap_uint<33> n;        // pairs in this lane
    ap_uint<16> M;        // steps
};
struct FinPair { float SA, SB; };
struct SetJob { float KM, QS; ap_uint<1> put; };
struct Res { ap_uint<128> s1, s2, sp; ap_uint<64> pairs, ovf; };

// ------------------------------------------------------------------ datapath helpers (from mc_heston_v2.cpp)
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


// ------------------------------------------------------------------ split
static void split(const float fp[16], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint32_t flags,
                  uint64_t key_lo, uint64_t key_hi, hls::stream<LaneJob> job[L], hls::stream<SetJob>& sj,
                  hls::stream<ap_uint<33> >& tot_m, hls::stream<ap_uint<33> >& tot_s) {
    LaneJob J;
    J.X0 = fp[0]; J.DR = fp[1]; J.VOL = fp[2];
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
    SetJob S;
    S.KM = fp[3];
    S.QS = fp[4];
    S.put = flags & 1;
    sj.write(S);
    tot_m.write(n);
    tot_s.write(n);
}

// ------------------------------------------------------------------ lane (loop interleaving)
static void lane(hls::stream<LaneJob>& jin, hls::stream<FinPair>& fout) {
    static const ap_uint<54> rom[2048] = MC_ICDF_ROM_INIT;
    static const ap_uint<32> T[256] = MC_EXP_T_INIT;
    static const float P2[256] = MC_EXP_P2_INIT;
#pragma HLS BIND_STORAGE variable=rom type=rom_1p impl=bram
    // two exp call sites per cycle (sides A and B): dual-port tables
#pragma HLS BIND_STORAGE variable=T type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=P2 type=rom_2p impl=bram

    LaneJob J = jin.read();
    const float X0 = J.X0, DR = J.DR, VOL = J.VOL;

    // slot RAMs, read at the first stage and written at the last (IL > pipeline depth)
    float ringXA[IL], ringXB[IL], ringSA[IL], ringSB[IL];
    u32 ringR[IL][4];
    ap_uint<17> ringC[IL];   // bit16 valid, bits15:0 steps done
#pragma HLS ARRAY_RESHAPE variable=ringR complete dim=2
#pragma HLS BIND_STORAGE variable=ringXA type=ram_s2p impl=lutram
#pragma HLS BIND_STORAGE variable=ringXB type=ram_s2p impl=lutram
#pragma HLS BIND_STORAGE variable=ringSA type=ram_s2p impl=lutram
#pragma HLS BIND_STORAGE variable=ringSB type=ram_s2p impl=lutram
#pragma HLS BIND_STORAGE variable=ringR type=ram_s2p impl=bram
#pragma HLS BIND_STORAGE variable=ringC type=ram_s2p impl=lutram

RING_INIT:
    for (int s = 0; s < IL; ++s) {
#pragma HLS PIPELINE II=1
        ringC[s] = 0;
        ringR[s][0] = 0; ringR[s][1] = 0; ringR[s][2] = 0; ringR[s][3] = 0;
        ringXA[s] = 0.0f; ringXB[s] = 0.0f; ringSA[s] = 0.0f; ringSB[s] = 0.0f;
    }

    // exact trip count: the last pair (index n-1) starts in wave W at slot s and finishes M-1
    // visits later: T = (W*M + M - 1)*IL + s + 1
    ap_uint<64> Tn = 0;
    if (J.n != 0) {
        ap_uint<33> nm1 = J.n - 1;
        ap_uint<26> W = nm1 >> AS_IL_LOG2;
        ap_uint<AS_IL_LOG2> s = nm1(AS_IL_LOG2 - 1, 0);
        ap_uint<48> wm = W * J.M;
        ap_uint<48> v = wm + J.M - 1;
        Tn = (ap_uint<64>(v) << AS_IL_LOG2) + s + 1;
    }
    ap_uint<33> nxt = 0;

RING:
    for (ap_uint<64> t = 0; t < Tn; ++t) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=268435456 avg=16777216
#pragma HLS DEPENDENCE variable=ringXA inter false
#pragma HLS DEPENDENCE variable=ringXB inter false
#pragma HLS DEPENDENCE variable=ringSA inter false
#pragma HLS DEPENDENCE variable=ringSB inter false
#pragma HLS DEPENDENCE variable=ringR inter false
#pragma HLS DEPENDENCE variable=ringC inter false
        ap_uint<AS_IL_LOG2> slot = t(AS_IL_LOG2 - 1, 0);
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
        float xa = take ? X0 : ringXA[slot];
        float xb = take ? X0 : ringXB[slot];
        float sa = take ? 0.0f : ringSA[slot];
        float sb = take ? 0.0f : ringSB[slot];

        // one draw -> ICDF -> exact float; GBM step on both sides; exp; running sums (asn::pair_step)
        u32 o = xo_next_hw(s0, s1, s2, s3);
        ap_int<24> z = icdf_hw(o, rom);
        ap_int<32> zk = z;
        float fz = zk.to_int();
        float e = VOL * fz;
        float ta = xa + DR;
        float tb = xb + DR;
        float xan = ta + e;
        float xbn = tb - e;
        float ea = fexp_half_hw(xan, T, P2);
        float eb = fexp_half_hw(xbn, T, P2);
        float san = sa + ea;
        float sbn = sb + eb;

        ap_uint<16> cnt1 = cnt + 1;
        bool done = active && (cnt1 == J.M);
        if (done) {
            FinPair F;
            F.SA = san; F.SB = sbn;
            fout.write(F);
        }
        ringC[slot] = (active && !done) ? ap_uint<17>((ap_uint<1>(1), cnt1)) : ap_uint<17>(0);
        ringR[slot][0] = s0; ringR[slot][1] = s1; ringR[slot][2] = s2; ringR[slot][3] = s3;
        ringXA[slot] = xan; ringXB[slot] = xbn;
        ringSA[slot] = san; ringSB[slot] = sbn;
    }
}

// ------------------------------------------------------------------ merge (1 pair per cycle)
static void merge(hls::stream<ap_uint<33> >& tot, hls::stream<FinPair> fin[L], hls::stream<FinPair>& out) {
    ap_uint<33> total = tot.read();
    ap_uint<33> written = 0;
    ap_uint<7> rr = 0;
MERGE:
    while (written < total) {
#pragma HLS PIPELINE II=1
        bool ne[L];
#pragma HLS ARRAY_PARTITION variable=ne complete
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
            out.write(p);
            written++;
            rr = (sel + 1 >= L) ? 0 : sel + 1;
        }
    }
}

// ------------------------------------------------------------------ payoffs + exact sums (1 pair per cycle)
static inline ap_uint<41> pm_hw(float S, float KM, float QS, bool put, ap_uint<1>& ovf) {
#pragma HLS INLINE
    float d = put ? (KM - S) : (S - KM);
    float po = d > 0.0f ? d : 0.0f;
    float pm = po * QS;
    ap_uint<41> r;
    if (!(pm < 1099511627776.0f)) {
        r = ap_uint<40>(~ap_uint<40>(0));
        ovf = 1;
    } else {
        ap_ufixed<40, 40> mf = pm;    // exact: pm is an integer < 2^40 (truncation of a non-negative float)
        r = mf.range(39, 0);
        ovf = 0;
    }
    return r;
}
static void settle(hls::stream<ap_uint<33> >& tot, hls::stream<SetJob>& sj, hls::stream<FinPair>& in,
                   Res& res) {
    ap_uint<33> total = tot.read();
    SetJob J = sj.read();
    ap_uint<128> s1 = 0, s2 = 0, sp = 0;
    ap_uint<64> ovf = 0;
SETTLE:
    for (ap_uint<33> k = 0; k < total; ++k) {
#pragma HLS PIPELINE II=1
        FinPair p = in.read();
        ap_uint<1> oa, ob;
        ap_uint<40> mA = pm_hw(p.SA, J.KM, J.QS, J.put, oa);
        ap_uint<40> mB = pm_hw(p.SB, J.KM, J.QS, J.put, ob);
        ap_uint<80> qa = mA * mA;
        ap_uint<80> qb = mB * mB;
        ap_uint<41> ps = ap_uint<41>(mA) + mB;
        ap_uint<82> psq = ps * ps;
        s1 += ps;
        s2 += ap_uint<81>(qa) + qb;
        sp += psq;
        ovf += (oa | ob);
    }
    res.s1 = s1; res.s2 = s2; res.sp = sp;
    res.pairs = total;
    res.ovf = ovf;
}

static void engine(const float fp[16], uint64_t pair_base, uint64_t n_pairs, uint32_t steps, uint32_t flags,
                   uint64_t key_lo, uint64_t key_hi, Res& res) {
#pragma HLS DATAFLOW
    hls::stream<LaneJob> job[L];
    hls::stream<FinPair> fin[L];
    hls::stream<SetJob> sj;
    hls::stream<ap_uint<33> > tot_m, tot_s;
    hls::stream<FinPair> pairs;
#pragma HLS STREAM variable=job depth=2
#pragma HLS STREAM variable=fin depth=4
#pragma HLS STREAM variable=sj depth=2
#pragma HLS STREAM variable=tot_m depth=2
#pragma HLS STREAM variable=tot_s depth=2
#pragma HLS STREAM variable=pairs depth=16
    split(fp, pair_base, n_pairs, steps, flags, key_lo, key_hi, job, sj, tot_m, tot_s);
    lane(job[0], fin[0]);
#if AS_LANES > 1
    lane(job[1], fin[1]);
#endif
#if AS_LANES > 2
    lane(job[2], fin[2]);
#endif
#if AS_LANES > 3
    lane(job[3], fin[3]);
#endif
#if AS_LANES > 4
    lane(job[4], fin[4]);
#endif
#if AS_LANES > 5
    lane(job[5], fin[5]);
#endif
#if AS_LANES > 6
    lane(job[6], fin[6]);
#endif
#if AS_LANES > 7
    lane(job[7], fin[7]);
#endif
#if AS_LANES > 8
    lane(job[8], fin[8]);
#endif
#if AS_LANES > 9
    lane(job[9], fin[9]);
#endif
#if AS_LANES > 10
    lane(job[10], fin[10]);
#endif
#if AS_LANES > 11
    lane(job[11], fin[11]);
#endif
#if AS_LANES > 12
    lane(job[12], fin[12]);
#endif
#if AS_LANES > 13
    lane(job[13], fin[13]);
#endif
#if AS_LANES > 14
    lane(job[14], fin[14]);
#endif
#if AS_LANES > 15
    lane(job[15], fin[15]);
#endif
#if AS_LANES > 16
    lane(job[16], fin[16]);
#endif
#if AS_LANES > 17
    lane(job[17], fin[17]);
#endif
#if AS_LANES > 18
    lane(job[18], fin[18]);
#endif
#if AS_LANES > 19
    lane(job[19], fin[19]);
#endif
#if AS_LANES > 20
    lane(job[20], fin[20]);
#endif
#if AS_LANES > 21
    lane(job[21], fin[21]);
#endif
#if AS_LANES > 22
    lane(job[22], fin[22]);
#endif
#if AS_LANES > 23
    lane(job[23], fin[23]);
#endif
#if AS_LANES > 24
    lane(job[24], fin[24]);
#endif
#if AS_LANES > 25
    lane(job[25], fin[25]);
#endif
#if AS_LANES > 26
    lane(job[26], fin[26]);
#endif
#if AS_LANES > 27
    lane(job[27], fin[27]);
#endif
#if AS_LANES > 28
    lane(job[28], fin[28]);
#endif
#if AS_LANES > 29
    lane(job[29], fin[29]);
#endif
#if AS_LANES > 30
    lane(job[30], fin[30]);
#endif
#if AS_LANES > 31
    lane(job[31], fin[31]);
#endif
#if AS_LANES > 32
    lane(job[32], fin[32]);
#endif
#if AS_LANES > 33
    lane(job[33], fin[33]);
#endif
#if AS_LANES > 34
    lane(job[34], fin[34]);
#endif
#if AS_LANES > 35
    lane(job[35], fin[35]);
#endif
#if AS_LANES > 36
    lane(job[36], fin[36]);
#endif
#if AS_LANES > 37
    lane(job[37], fin[37]);
#endif
#if AS_LANES > 38
    lane(job[38], fin[38]);
#endif
#if AS_LANES > 39
    lane(job[39], fin[39]);
#endif
#if AS_LANES > 40
    lane(job[40], fin[40]);
#endif
#if AS_LANES > 41
    lane(job[41], fin[41]);
#endif
#if AS_LANES > 42
    lane(job[42], fin[42]);
#endif
#if AS_LANES > 43
    lane(job[43], fin[43]);
#endif
#if AS_LANES > 44
    lane(job[44], fin[44]);
#endif
#if AS_LANES > 45
    lane(job[45], fin[45]);
#endif
#if AS_LANES > 46
    lane(job[46], fin[46]);
#endif
#if AS_LANES > 47
    lane(job[47], fin[47]);
#endif
#if AS_LANES > 48
    lane(job[48], fin[48]);
#endif
#if AS_LANES > 49
    lane(job[49], fin[49]);
#endif
#if AS_LANES > 50
    lane(job[50], fin[50]);
#endif
#if AS_LANES > 51
    lane(job[51], fin[51]);
#endif
#if AS_LANES > 52
    lane(job[52], fin[52]);
#endif
#if AS_LANES > 53
    lane(job[53], fin[53]);
#endif
#if AS_LANES > 54
    lane(job[54], fin[54]);
#endif
#if AS_LANES > 55
    lane(job[55], fin[55]);
#endif
#if AS_LANES > 56
    lane(job[56], fin[56]);
#endif
#if AS_LANES > 57
    lane(job[57], fin[57]);
#endif
#if AS_LANES > 58
    lane(job[58], fin[58]);
#endif
#if AS_LANES > 59
    lane(job[59], fin[59]);
#endif
#if AS_LANES > 60
    lane(job[60], fin[60]);
#endif
#if AS_LANES > 61
    lane(job[61], fin[61]);
#endif
#if AS_LANES > 62
    lane(job[62], fin[62]);
#endif
#if AS_LANES > 63
    lane(job[63], fin[63]);
#endif
    merge(tot_m, fin, pairs);
    settle(tot_s, sj, pairs, res);
}

void mc_asian(const float* fpar, uint64_t* out, uint64_t pair_base, uint64_t n_pairs,
              uint32_t steps, uint32_t flags, uint64_t key_lo, uint64_t key_hi) {
#pragma HLS INTERFACE m_axi port=fpar bundle=gmem0 offset=slave depth=16 max_read_burst_length=16 num_read_outstanding=1 latency=0
#pragma HLS INTERFACE m_axi port=out bundle=gmem1 offset=slave depth=8 max_write_burst_length=16 num_write_outstanding=1 latency=0
#pragma HLS INTERFACE s_axilite port=fpar bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=pair_base bundle=control
#pragma HLS INTERFACE s_axilite port=n_pairs bundle=control
#pragma HLS INTERFACE s_axilite port=steps bundle=control
#pragma HLS INTERFACE s_axilite port=flags bundle=control
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
    if (st == 0) st = 1;             // guard (host validates 1..65535)
    if (st > 65535) st = 65535;
    Res res;
    engine(fp, pair_base, n_pairs, st, flags, key_lo, key_hi, res);
    uint64_t w[8];
    w[0] = res.s1(63, 0);  w[1] = res.s1(127, 64);
    w[2] = res.s2(63, 0);  w[3] = res.s2(127, 64);
    w[4] = res.sp(63, 0);  w[5] = res.sp(127, 64);
    w[6] = res.pairs;
    w[7] = uint64_t(res.ovf(31, 0)) | (uint64_t(AS_LANES) << 32) | (uint64_t(AS_IL) << 40);
WRITE:
    for (int i = 0; i < 8; ++i) {
#pragma HLS PIPELINE II=1
        out[i] = w[i];
    }
}

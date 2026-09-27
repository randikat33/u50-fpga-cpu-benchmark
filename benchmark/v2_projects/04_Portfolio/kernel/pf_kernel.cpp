// pf_kernel.cpp - v2 two-stage Heston Monte Carlo portfolio kernel (Vitis HLS 2023.1, Alveo U50)
//
// One call prices a list of trades with the same (n_paths, n_steps):
//
//   m_axi trades (2 views) --> read_trades --\
//   m_axi ids -------------> read_ids -------+--> setup (per-trade constants, II=1)
//   market (loaded before the dataflow) -----/        |
//                                                   split (trade -> <=L lane jobs, round robin)
//                                   +-------------+---+---+-------------+
//                                 lane 0        lane 1   ...        lane L-1     (II=1, IL slots each)
//                                   +-------------+---+---+-------------+
//                                                 collect (exact integer moment sums, trade order)
//                                                   write_out --> m_axi out (4 x uint64 per trade)
//
// A lane runs one job (trade, path range) at a time. The path state lives in an IL-deep slot RAM;
// every clock cycle advances one slot by one time step (loop interleaving, II=1). Rotation 0 of a job
// seeds and primes the slots, rotation n of each block prices the finished paths and reseeds the
// slots for the next block, so a job costs IL + nblk*n*IL cycles.
// Numerics are bit-identical to common/pf_model.hpp (checked by test/tb_kernel.cpp).
#include "pf_kernel.h"
#include <ap_fixed.h>
#include <ap_int.h>
#include <hls_math.h>
#include <hls_stream.h>
#include "pf_tables.h"

#define L PF_LANES
#define IL PF_IL
#if PF_LANES < 1 || PF_LANES > 32
#error "PF_LANES must be 1..32"
#endif

typedef ap_uint<32> u32;

// ---------------------------------------------------------------- stream payloads
struct TradeIn {
    u32 uid;
    float K, T, v0, kappa, theta, sigma, rho;
    ap_uint<1> put, asian;
};
struct Tmpl {  // per-trade constants (see pf::TradeConst)
    float S0, K, v0, theta, kdt, sqdt, mu, hdt, sr, ssq, Rq, inv_n, W0, W1, W2, W3;
    u32 k0, k1, k2, k3, id;
    ap_uint<11> n_steps;
    ap_uint<1> put, asian, ft, last;
};
struct LaneJob {
    Tmpl t;
    u32 p_begin, p_end;
};
struct LaneRes {
    ap_uint<64> sum;
    ap_uint<96> sum2;
};
struct Meta {
    u32 id;
    ap_uint<8> nsub;
    ap_uint<1> last;
};
struct OutRec {
    ap_uint<64> sum;
    ap_uint<96> sum2;
    u32 id;
};
struct Slot {  // per-path state stored in the interleave RAM (288 bits)
    u32 s0, s1, s2, s3;
    float zS, zVs, v, lr, sE;
};

// ---------------------------------------------------------------- small datapath helpers
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

static inline ap_uint<64> mix64(ap_uint<64> z) {
#pragma HLS INLINE
    z = (z ^ (z >> 30)) * ap_uint<64>(0xbf58476d1ce4e5b9ull);
    z = (z ^ (z >> 27)) * ap_uint<64>(0x94d049bb133111ebull);
    return z ^ (z >> 31);
}

// One ICDF evaluation. `rom` is a lane-local ROM; each ROM is read by exactly two call sites.
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
    ap_int<28> m1 = c2 * x;                 // |.| < 2^27
    ap_int<18> b = c1 + ap_int<13>(m1 >> 15);
    ap_int<32> m2 = b * x;                  // |.| < 2^31 (checked by the table generator)
    ap_int<27> zi = c0 + ap_int<17>(m2 >> 15);
    ap_int<25> zr = (zi + 2) >> 2;
    return sgn ? ap_int<24>(zr) : ap_int<24>(-zr);
}

static const float PO_CAP = PF_PO_CAP;

// exp(x) in hardware form (pf::fexp)
static inline float exp_hw(float x, const ap_uint<32> T[256], const float P2[256]) {
#pragma HLS INLINE
    float xc0 = (x <= 64.0f) ? x : 64.0f;
    float xc = (x >= -64.0f) ? xc0 : -64.0f;
    ap_fixed<32, 8> xf = xc;                              // floor(xc * 2^24) (AP_TRN)
    ap_int<32> xq = xf.range(31, 0);
    ap_int<63> y = xq * ap_int<32>(1549082005);           // x / ln2, Q54
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
    ap_uint<33> M = Ti + (Tg >> 32);
    if (M > ap_uint<33>(4294967167ull)) M = 4294967167ull;
    ap_uint<24> m24 = (M + 128) >> 8;
    ap_int<32> m24s = m24;
    float mf = m24s.to_int();
    ap_uint<8> k = n + 128;
    float p2 = P2[k];
    float e = mf * p2;
    return e;
}

// ---------------------------------------------------------------- lane
static void lane_run(const LaneJob& J, LaneRes& R) {
    static const ap_uint<54> rom0[2048] = PF_ICDF_ROM_INIT;
    static const ap_uint<54> rom1[2048] = PF_ICDF_ROM_INIT;
    static const ap_uint<54> rom2[2048] = PF_ICDF_ROM_INIT;
    static const ap_uint<32> expT[256] = PF_EXP_T_INIT;
    static const float expP2[256] = PF_EXP_P2_INIT;
    // three distinct dual-port ROMs: 6 ICDF reads per cycle
#pragma HLS BIND_STORAGE variable=rom0 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom1 type=rom_2p impl=bram
#pragma HLS BIND_STORAGE variable=rom2 type=rom_2p impl=bram
    // small tables: LUT ROMs
#pragma HLS BIND_STORAGE variable=expT type=rom_1p impl=lutram
#pragma HLS BIND_STORAGE variable=expP2 type=rom_1p impl=lutram

    const Tmpl& C = J.t;
    ap_uint<33> span = ap_uint<33>(J.p_end) - J.p_begin;               // >= 1
    ap_uint<33> nb33 = (span + (IL - 1)) >> PF_LOG_IL;
    ap_uint<27> nblk = nb33;
    ap_uint<27> nblk_m1 = nblk - 1;
    ap_uint<PF_LOG_IL> remb = span(PF_LOG_IL - 1, 0);
    ap_uint<PF_LOG_IL + 1> nlast = (remb == 0) ? ap_uint<PF_LOG_IL + 1>(IL) : ap_uint<PF_LOG_IL + 1>(remb);
    ap_uint<11> n = C.n_steps;
    ap_uint<38> rots = nblk * n;
    ap_uint<48> total = (ap_uint<48>(rots) << PF_LOG_IL) + IL;

    Slot S[IL];
    // one wide RAM (288 bits x IL); read at the first stage, written ~90 stages later
#pragma HLS AGGREGATE variable=S
#pragma HLS BIND_STORAGE variable=S type=ram_s2p impl=bram

    ap_uint<PF_LOG_IL> i = 0;
    ap_uint<11> r = 0;
    ap_uint<27> blk = 0;
    u32 base = J.p_begin;
    u32 nbase = J.p_begin + IL;
    bool lastblk = (nblk_m1 == 0);
    ap_uint<64> sum = 0;
    ap_uint<96> sum2 = 0;

pf_lane_loop:
    for (ap_uint<48> it = 0; it < total; ++it) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=128 max=4194432 avg=8320
        // Interleaving: slot i is written again only IL iterations later. dependent=true + distance
        // lets HLS verify that the read->write latency fits (it raises II instead of silently failing).
#pragma HLS DEPENDENCE variable=S type=inter direction=RAW dependent=true distance=PF_IL_PRAGMA
        bool prime = (r == 0);
        bool lastr = (r == n);
        bool reseed = prime || lastr;
        bool valid = lastr && (!lastblk || (ap_uint<PF_LOG_IL + 1>(i) < nlast));
        u32 sp = (prime ? base : nbase) | u32(i);

        Slot st = {0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
        if (!prime) st = S[i];

        // ---- RNG: seed or continue, then 6 xoshiro128** outputs
        u32 s0, s1, s2, s3;
        if (reseed) {
            s0 = fmix32(sp ^ C.k0);
            s1 = fmix32(sp ^ C.k1);
            s2 = fmix32(sp ^ C.k2);
            s3 = fmix32(sp ^ C.k3);
            if ((s0 | s1 | s2 | s3) == 0) s0 = 0x9E3779B9u;
        } else {
            s0 = st.s0; s1 = st.s1; s2 = st.s2; s3 = st.s3;
        }
        u32 o[6];
        for (int k = 0; k < 6; ++k) {
            u32 m5 = (s1 << 2) + s1;
            u32 rr = rotl32(m5, 7);
            o[k] = (rr << 3) + rr;
            u32 t = s1 << 9;
            s2 ^= s0; s3 ^= s1; s1 ^= s2; s0 ^= s3;
            s2 ^= t;
            s3 = rotl32(s3, 11);
        }

        // ---- inverse normal x6 and factor mixing (normals for the NEXT step)
        ap_int<24> z0 = icdf_hw(o[0], rom0);
        ap_int<24> z1 = icdf_hw(o[1], rom0);
        ap_int<24> z2 = icdf_hw(o[2], rom1);
        ap_int<24> z3 = icdf_hw(o[3], rom1);
        ap_int<24> z4 = icdf_hw(o[4], rom2);
        ap_int<24> z5 = icdf_hw(o[5], rom2);
        float f0 = z0.to_int(), f1 = z1.to_int(), f2 = z2.to_int();
        float f3 = z3.to_int(), f4 = z4.to_int(), f5 = z5.to_int();
        float m0 = C.W0 * f0;
        float m1 = C.W1 * f1;
        float m2 = C.W2 * f2;
        float m3 = C.W3 * f3;
        float mr = C.Rq * f4;
        float s01 = m0 + m1;
        float s23 = m2 + m3;
        float s03 = s01 + s23;
        float zSn = s03 + mr;
        float za = C.sr * zSn;
        float zb = C.ssq * f5;
        float zVsn = za + zb;

        // ---- Heston step with the stored normals (pf::heston_step)
        float sq = hls::sqrt(st.v);
        float sv = sq * C.sqdt;
        float tv = C.theta - st.v;
        float d1 = C.kdt * tv;
        float va = st.v + d1;
        float d2 = sv * st.zVs;
        float vn = va + d2;
        float hv = C.hdt * st.v;
        float l1 = C.mu - hv;
        float la = st.lr + l1;
        float l2 = sv * st.zS;
        float lrn = la + l2;
        float vneg = -vn;
        float vref = (vn < 0.0f) ? vneg : vn;
        float vft = (vn > 0.0f) ? vn : 0.0f;
        float vnn = C.ft ? vft : vref;

        // ---- running average and payoff
        float E = exp_hw(lrn, expT, expP2);
        float sEn = st.sE + E;
        float A = sEn * C.inv_n;
        float bsel = C.asian ? A : E;
        float Sp = bsel * C.S0;
        float da = C.put ? C.K : Sp;
        float db = C.put ? Sp : C.K;
        float dd = da - db;
        float po = (dd > 0.0f) ? dd : 0.0f;
        float pc = (po < PO_CAP) ? po : PO_CAP;
        ap_ufixed<31, 14> qf = pc;                 // floor(pc * 2^17)
        ap_uint<31> q17 = qf.range(30, 0);
        ap_uint<32> q17p = ap_uint<32>(q17) + 1;
        ap_uint<30> q = q17p >> 1;
        ap_uint<60> qq = q * q;

        // ---- state write-back
        Slot ns;
        ns.s0 = s0; ns.s1 = s1; ns.s2 = s2; ns.s3 = s3;
        ns.zS = zSn;
        ns.zVs = zVsn;
        ns.v = reseed ? C.v0 : vnn;
        ns.lr = reseed ? 0.0f : lrn;
        ns.sE = reseed ? 0.0f : sEn;
        S[i] = ns;

        if (valid) {
            sum += q;
            sum2 += qq;
        }

        // ---- slot / rotation / block counters
        if (i == IL - 1) {
            if (lastr) {
                r = 1;
                blk = blk + 1;
                lastblk = ((blk) == nblk_m1);
                base = nbase;
                nbase = nbase + IL;
            } else {
                r = r + 1;
            }
        }
        i = i + 1;
    }
    R.sum = sum;
    R.sum2 = sum2;
}

static void lane_proc(hls::stream<LaneJob>& jin, hls::stream<LaneRes>& rout) {
pf_lane_jobs:
    for (;;) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=1048576 avg=31250
        LaneJob J = jin.read();
        if (J.t.last) break;
        LaneRes R;
        lane_run(J, R);
        rout.write(R);
    }
}

// ---------------------------------------------------------------- read / setup / split
static void read_trades_proc(const float* tf, const uint32_t* ti, u32 n, hls::stream<TradeIn>& tout,
                             hls::stream<u32>& cnt_setup, hls::stream<u32>& cnt_wr) {
    cnt_setup.write(n);
    cnt_wr.write(n);
    ap_uint<31> total = ap_uint<31>(n) * PF_TRADE_WORDS;
    ap_uint<4> k = 0;
    TradeIn cur;
    cur.uid = 0; cur.K = 0; cur.T = 0; cur.v0 = 0; cur.kappa = 0; cur.theta = 0; cur.sigma = 0; cur.rho = 0;
    cur.put = 0; cur.asian = 0;
pf_read_trades:
    for (ap_uint<31> w = 0; w < total; ++w) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=11 max=738197504 avg=2750000
        float f = tf[w];     // sequential burst on gmem0
        u32 u = ti[w];       // sequential burst on gmem1 (same bank, integer view)
        switch (k) {
            case 0: cur.uid = u; break;
            case 1: cur.K = f; break;
            case 2: cur.T = f; break;
            case 3: cur.put = (u(7, 0) != 0); cur.asian = (u(15, 8) == 1); break;
            case 6: cur.v0 = f; break;
            case 7: cur.kappa = f; break;
            case 8: cur.theta = f; break;
            case 9: cur.sigma = f; break;
            case 10: cur.rho = f; break;
            default: break;
        }
        if (k == PF_TRADE_WORDS - 1) {
            tout.write(cur);
            k = 0;
        } else {
            k = k + 1;
        }
    }
}

static void read_ids_proc(const uint32_t* ids, u32 n_ids, hls::stream<u32>& iout) {
pf_read_ids:
    for (u32 j = 0; j < n_ids; ++j) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=0 max=67108864 avg=2500
        iout.write(ids[j]);   // sequential burst on gmem2
    }
}

static void setup_proc(hls::stream<TradeIn>& tin, hls::stream<u32>& iin, hls::stream<u32>& cnt,
                       const float spot[PF_MAX_UNDER], const float rate[PF_MAX_UNDER],
                       const float divq[PF_MAX_UNDER], const float fw0[PF_MAX_UNDER],
                       const float fw1[PF_MAX_UNDER], const float fw2[PF_MAX_UNDER],
                       const float fw3[PF_MAX_UNDER], float dspot, float drate, float volscale,
                       u32 n_under, u32 use_ids, u32 id_base, ap_uint<11> n_steps, ap_uint<1> ft,
                       ap_uint<64> seed, u32 stage, hls::stream<Tmpl>& tout) {
    u32 n = cnt.read();
    // per-call constants (pf::call_const)
    ap_int<32> ns32 = n_steps;
    float nf = ns32.to_int();
    float inv_n = 1.0f / nf;
    float one_p = 1.0f + dspot;
    ap_uint<64> seed_hi = seed ^ (ap_uint<64>(stage) << 32);
pf_setup:
    for (u32 t = 0; t < n; ++t) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=0 max=67108864 avg=250000
        TradeIn tr = tin.read();
        u32 id = use_ids ? iin.read() : u32(id_base + t);
        ap_uint<10> uid = (tr.uid < n_under) ? ap_uint<10>(tr.uid(9, 0)) : ap_uint<10>(0);
        Tmpl c;
        float dt = tr.T / nf;
        c.sqdt = hls::sqrt(dt);
        c.hdt = 0.5f * dt;
        float rr = rate[uid] + drate;
        float rmq = rr - divq[uid];
        c.mu = rmq * dt;
        c.kdt = tr.kappa * dt;
        c.theta = tr.theta;
        float sig = tr.sigma * volscale;
        float rho2 = tr.rho * tr.rho;
        float omr0 = 1.0f - rho2;
        float omr = (omr0 < 0.0f) ? 0.0f : omr0;
        float srho = hls::sqrt(omr);
        c.sr = sig * tr.rho;
        float ss = sig * srho;
        c.ssq = ss * 0x1p-20f;
        float w0 = fw0[uid], w1 = fw1[uid], w2 = fw2[uid], w3 = fw3[uid];
        float q0 = w0 * w0;
        float q1 = w1 * w1;
        float q2 = w2 * w2;
        float q3 = w3 * w3;
        float a0 = 0.0f + q0;
        float a1 = a0 + q1;
        float a2 = a1 + q2;
        float a3 = a2 + q3;
        c.W0 = w0 * 0x1p-20f;
        c.W1 = w1 * 0x1p-20f;
        c.W2 = w2 * 0x1p-20f;
        c.W3 = w3 * 0x1p-20f;
        float res0 = 1.0f - a3;
        float res = (res0 < 0.0f) ? 0.0f : res0;
        float Rt = hls::sqrt(res);
        c.Rq = Rt * 0x1p-20f;
        c.S0 = spot[uid] * one_p;
        c.K = tr.K;
        c.v0 = (tr.v0 > 0.0f) ? tr.v0 : 1e-4f;
        c.inv_n = inv_n;
        c.put = tr.put;
        c.asian = tr.asian;
        c.ft = ft;
        c.n_steps = n_steps;
        c.id = id;
        ap_uint<64> b = seed_hi ^ ap_uint<64>(id);
        ap_uint<64> ka = mix64(b + ap_uint<64>(PF_GOLDEN64));
        ap_uint<64> kb = mix64(b + ap_uint<64>(2 * PF_GOLDEN64));
        c.k0 = ka(31, 0); c.k1 = ka(63, 32);
        c.k2 = kb(31, 0); c.k3 = kb(63, 32);
        c.last = 0;
        tout.write(c);
    }
    Tmpl e;
    e.S0 = e.K = e.v0 = e.theta = e.kdt = e.sqdt = e.mu = e.hdt = e.sr = e.ssq = e.Rq = e.inv_n = 0.0f;
    e.W0 = e.W1 = e.W2 = e.W3 = 0.0f;
    e.k0 = e.k1 = e.k2 = e.k3 = e.id = 0;
    e.n_steps = 0; e.put = 0; e.asian = 0; e.ft = 0;
    e.last = 1;
    tout.write(e);
}

static void split_proc(hls::stream<Tmpl>& tin, u32 n_paths, u32 chunk, ap_uint<8> nsub,
                       hls::stream<LaneJob> jobs[L], hls::stream<Meta>& meta) {
    ap_uint<8> rr = 0;
    ap_uint<8> rem = 0;
    u32 p = 0;
    Tmpl T;
    bool done = false;
pf_split:
    while (!done) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=67108864 avg=250000
        bool have = true;
        if (rem == 0) {
            T = tin.read();
            if (T.last) {
                done = true;
                have = false;
            } else {
                Meta m;
                m.id = T.id; m.nsub = nsub; m.last = 0;
                meta.write(m);
                rem = nsub;
                p = 0;
            }
        }
        if (have) {
            LaneJob J;
            J.t = T;
            J.p_begin = p;
            ap_uint<33> pe = ap_uint<33>(p) + chunk;
            J.p_end = (pe > n_paths) ? n_paths : u32(pe);
            for (int l = 0; l < L; ++l) {
                if (rr == l) jobs[l].write(J);
            }
            rr = (rr == L - 1) ? ap_uint<8>(0) : ap_uint<8>(rr + 1);
            p = J.p_end;
            rem = rem - 1;
        }
    }
    LaneJob E;
    E.t = T;
    E.t.last = 1;
    E.p_begin = 0;
    E.p_end = 0;
    for (int l = 0; l < L; ++l) jobs[l].write(E);
    Meta m;
    m.id = 0; m.nsub = 0; m.last = 1;
    meta.write(m);
}

static void collect_proc(hls::stream<Meta>& meta, hls::stream<LaneRes> res[L], hls::stream<OutRec>& out) {
    ap_uint<8> rr = 0;
pf_collect:
    for (;;) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=67108864 avg=250000
        Meta m = meta.read();
        if (m.last) break;
        ap_uint<64> sum = 0;
        ap_uint<96> sum2 = 0;
    pf_collect_sub:
        for (ap_uint<8> j = 0; j < m.nsub; ++j) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=1 max=64 avg=1
            LaneRes x;
            x.sum = 0; x.sum2 = 0;
            for (int l = 0; l < L; ++l) {
                if (rr == l) x = res[l].read();
            }
            sum += x.sum;
            sum2 += x.sum2;
            rr = (rr == L - 1) ? ap_uint<8>(0) : ap_uint<8>(rr + 1);
        }
        OutRec o;
        o.sum = sum; o.sum2 = sum2; o.id = m.id;
        out.write(o);
    }
}

static void write_proc(hls::stream<u32>& cnt, hls::stream<OutRec>& in, uint64_t* out) {
    u32 n = cnt.read();
    ap_uint<29> total = ap_uint<29>(n) * PF_MOM_WORDS;
    OutRec r;
    r.sum = 0; r.sum2 = 0; r.id = 0;
    ap_uint<2> k = 0;
pf_write:
    for (ap_uint<29> w = 0; w < total; ++w) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=0 max=268435456 avg=1000000
        if (k == 0) r = in.read();
        ap_uint<64> word;
        switch (k) {
            case 0: word = r.sum; break;
            case 1: word = r.sum2(63, 0); break;
            case 2: word = ap_uint<64>(r.sum2(95, 64)); break;
            default: word = (ap_uint<64>(1) << 63) | ap_uint<64>(r.id); break;
        }
        out[w] = word;   // sequential burst on gmem4
        k = k + 1;
    }
}

static void pf_dataflow(const float* tf, const uint32_t* ti, const uint32_t* ids, uint64_t* out,
                        const float spot[PF_MAX_UNDER], const float rate[PF_MAX_UNDER],
                        const float divq[PF_MAX_UNDER], const float fw0[PF_MAX_UNDER],
                        const float fw1[PF_MAX_UNDER], const float fw2[PF_MAX_UNDER],
                        const float fw3[PF_MAX_UNDER], float dspot, float drate, float volscale,
                        u32 n_trades, u32 n_ids, u32 id_base, u32 use_ids, u32 n_under,
                        u32 n_paths, ap_uint<11> n_steps, u32 chunk, ap_uint<8> nsub,
                        ap_uint<64> seed, u32 stage, ap_uint<1> ft) {
#pragma HLS DATAFLOW
    hls::stream<TradeIn> s_tr("s_tr");
    hls::stream<u32> s_id("s_id");
    hls::stream<u32> s_cnt_setup("s_cnt_setup");
    hls::stream<u32> s_cnt_wr("s_cnt_wr");
    hls::stream<Tmpl> s_tmpl("s_tmpl");
    hls::stream<LaneJob> s_job[L];
    hls::stream<LaneRes> s_res[L];
    hls::stream<Meta> s_meta("s_meta");
    hls::stream<OutRec> s_out("s_out");
    // FIFO depths: small for the feed-forward path (lanes are the bottleneck), 2 per lane result
    // (<= 1 pending result per lane per trade, see README deadlock argument).
#pragma HLS STREAM variable=s_tr depth=32
#pragma HLS STREAM variable=s_id depth=64
#pragma HLS STREAM variable=s_cnt_setup depth=2
#pragma HLS STREAM variable=s_cnt_wr depth=2
#pragma HLS STREAM variable=s_tmpl depth=16
#pragma HLS STREAM variable=s_job depth=4
#pragma HLS STREAM variable=s_res depth=4
#pragma HLS STREAM variable=s_meta depth=128
#pragma HLS STREAM variable=s_out depth=32

    read_trades_proc(tf, ti, n_trades, s_tr, s_cnt_setup, s_cnt_wr);
    read_ids_proc(ids, n_ids, s_id);
    setup_proc(s_tr, s_id, s_cnt_setup, spot, rate, divq, fw0, fw1, fw2, fw3, dspot, drate, volscale,
               n_under, use_ids, id_base, n_steps, ft, seed, stage, s_tmpl);
    split_proc(s_tmpl, n_paths, chunk, nsub, s_job, s_meta);
    // L lane instances (unconditional calls in the dataflow region, one RTL instance each)
#if PF_LANES > 0
    lane_proc(s_job[0], s_res[0]);
#endif
#if PF_LANES > 1
    lane_proc(s_job[1], s_res[1]);
#endif
#if PF_LANES > 2
    lane_proc(s_job[2], s_res[2]);
#endif
#if PF_LANES > 3
    lane_proc(s_job[3], s_res[3]);
#endif
#if PF_LANES > 4
    lane_proc(s_job[4], s_res[4]);
#endif
#if PF_LANES > 5
    lane_proc(s_job[5], s_res[5]);
#endif
#if PF_LANES > 6
    lane_proc(s_job[6], s_res[6]);
#endif
#if PF_LANES > 7
    lane_proc(s_job[7], s_res[7]);
#endif
#if PF_LANES > 8
    lane_proc(s_job[8], s_res[8]);
#endif
#if PF_LANES > 9
    lane_proc(s_job[9], s_res[9]);
#endif
#if PF_LANES > 10
    lane_proc(s_job[10], s_res[10]);
#endif
#if PF_LANES > 11
    lane_proc(s_job[11], s_res[11]);
#endif
#if PF_LANES > 12
    lane_proc(s_job[12], s_res[12]);
#endif
#if PF_LANES > 13
    lane_proc(s_job[13], s_res[13]);
#endif
#if PF_LANES > 14
    lane_proc(s_job[14], s_res[14]);
#endif
#if PF_LANES > 15
    lane_proc(s_job[15], s_res[15]);
#endif
#if PF_LANES > 16
    lane_proc(s_job[16], s_res[16]);
#endif
#if PF_LANES > 17
    lane_proc(s_job[17], s_res[17]);
#endif
#if PF_LANES > 18
    lane_proc(s_job[18], s_res[18]);
#endif
#if PF_LANES > 19
    lane_proc(s_job[19], s_res[19]);
#endif
#if PF_LANES > 20
    lane_proc(s_job[20], s_res[20]);
#endif
#if PF_LANES > 21
    lane_proc(s_job[21], s_res[21]);
#endif
#if PF_LANES > 22
    lane_proc(s_job[22], s_res[22]);
#endif
#if PF_LANES > 23
    lane_proc(s_job[23], s_res[23]);
#endif
#if PF_LANES > 24
    lane_proc(s_job[24], s_res[24]);
#endif
#if PF_LANES > 25
    lane_proc(s_job[25], s_res[25]);
#endif
#if PF_LANES > 26
    lane_proc(s_job[26], s_res[26]);
#endif
#if PF_LANES > 27
    lane_proc(s_job[27], s_res[27]);
#endif
#if PF_LANES > 28
    lane_proc(s_job[28], s_res[28]);
#endif
#if PF_LANES > 29
    lane_proc(s_job[29], s_res[29]);
#endif
#if PF_LANES > 30
    lane_proc(s_job[30], s_res[30]);
#endif
#if PF_LANES > 31
    lane_proc(s_job[31], s_res[31]);
#endif
    collect_proc(s_meta, s_res, s_out);
    write_proc(s_cnt_wr, s_out, out);
}

// ---------------------------------------------------------------- top
void pf_kernel(const float* trades_f, const uint32_t* trades_i, const uint32_t* ids,
               const float* market, uint64_t* out,
               uint32_t n_trades, uint32_t n_ids, uint32_t id_base, uint32_t use_ids,
               uint32_t n_under, uint32_t n_paths, uint32_t n_steps, uint32_t chunk_paths,
               uint64_t seed, uint32_t stage, uint32_t flags) {
    // one bundle per logical buffer view; trades_f/trades_i are two views of one BO (same HBM bank)
#pragma HLS INTERFACE m_axi port=trades_f bundle=gmem0 offset=slave depth=1408 max_read_burst_length=256 num_read_outstanding=16 num_write_outstanding=16 latency=0
#pragma HLS INTERFACE m_axi port=trades_i bundle=gmem1 offset=slave depth=1408 max_read_burst_length=256 num_read_outstanding=16 num_write_outstanding=16 latency=0
#pragma HLS INTERFACE m_axi port=ids bundle=gmem2 offset=slave depth=128 max_read_burst_length=256 num_read_outstanding=16 num_write_outstanding=16 latency=0
#pragma HLS INTERFACE m_axi port=market bundle=gmem3 offset=slave depth=7172 max_read_burst_length=256 num_read_outstanding=16 num_write_outstanding=16 latency=0
#pragma HLS INTERFACE m_axi port=out bundle=gmem4 offset=slave depth=512 max_write_burst_length=256 num_read_outstanding=16 num_write_outstanding=16 latency=0
// Vitis kernel mode: the m_axi base-address registers must be in the same s_axilite bundle
#pragma HLS INTERFACE s_axilite port=trades_f bundle=control
#pragma HLS INTERFACE s_axilite port=trades_i bundle=control
#pragma HLS INTERFACE s_axilite port=ids bundle=control
#pragma HLS INTERFACE s_axilite port=market bundle=control
#pragma HLS INTERFACE s_axilite port=out bundle=control
#pragma HLS INTERFACE s_axilite port=n_trades bundle=control
#pragma HLS INTERFACE s_axilite port=n_ids bundle=control
#pragma HLS INTERFACE s_axilite port=id_base bundle=control
#pragma HLS INTERFACE s_axilite port=use_ids bundle=control
#pragma HLS INTERFACE s_axilite port=n_under bundle=control
#pragma HLS INTERFACE s_axilite port=n_paths bundle=control
#pragma HLS INTERFACE s_axilite port=n_steps bundle=control
#pragma HLS INTERFACE s_axilite port=chunk_paths bundle=control
#pragma HLS INTERFACE s_axilite port=seed bundle=control
#pragma HLS INTERFACE s_axilite port=stage bundle=control
#pragma HLS INTERFACE s_axilite port=flags bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control

    float spot[PF_MAX_UNDER], rate[PF_MAX_UNDER], divq[PF_MAX_UNDER];
    float fw0[PF_MAX_UNDER], fw1[PF_MAX_UNDER], fw2[PF_MAX_UNDER], fw3[PF_MAX_UNDER];
    float prm[PF_PARAM_WORDS];
    // BRAM market tables, factor weights split so setup reads all four in one cycle
#pragma HLS BIND_STORAGE variable=spot type=ram_2p impl=bram
#pragma HLS BIND_STORAGE variable=rate type=ram_2p impl=bram
#pragma HLS BIND_STORAGE variable=divq type=ram_2p impl=bram
#pragma HLS ARRAY_PARTITION variable=prm type=complete

    // ---- load the market blob (7168 + 4 floats, one sequential burst)
    ap_uint<3> region = 0;   // 0 spot, 1 rate, 2 divq, 3 fw, 4 params
    ap_uint<10> u = 0;
    ap_uint<2> fk = 0;
pf_load_market:
    for (ap_uint<13> w = 0; w < PF_MARKET_WORDS + PF_PARAM_WORDS; ++w) {
#pragma HLS PIPELINE II=1
        float f = market[w];
        switch (region) {
            case 0: spot[u] = f; break;
            case 1: rate[u] = f; break;
            case 2: divq[u] = f; break;
            case 3:
                if (fk == 0) fw0[u] = f;
                else if (fk == 1) fw1[u] = f;
                else if (fk == 2) fw2[u] = f;
                else fw3[u] = f;
                break;
            default: prm[u(1, 0)] = f; break;
        }
        if (region == 3) {
            if (fk == 3) {
                fk = 0;
                if (u == PF_MAX_UNDER - 1) { u = 0; region = 4; }
                else u = u + 1;
            } else {
                fk = fk + 1;
            }
        } else if (region == 4) {
            u = u + 1;
        } else {
            if (u == PF_MAX_UNDER - 1) { u = 0; region = region + 1; }
            else u = u + 1;
        }
    }

    // ---- sanitise scalar arguments (keeps every stream balanced for any input)
    u32 nu = (n_under == 0 || n_under > PF_MAX_UNDER) ? u32(PF_MAX_UNDER) : u32(n_under);
    u32 ns = (n_steps == 0) ? u32(1) : (n_steps > PF_MAX_STEPS ? u32(PF_MAX_STEPS) : u32(n_steps));
    u32 np = (n_paths == 0) ? u32(1) : u32(n_paths);
    bool uids = (use_ids != 0) && (n_ids == n_trades);
    u32 nid = uids ? u32(n_ids) : u32(0);
    ap_uint<33> ilm = IL - 1;
    ap_uint<33> c33 = (chunk_paths == 0) ? ap_uint<33>(np) : ap_uint<33>(chunk_paths);
    ap_uint<33> npr = ((ap_uint<33>(np) + ilm) >> PF_LOG_IL) << PF_LOG_IL;
    if (c33 > npr) c33 = npr;
    c33 = ((c33 + ilm) >> PF_LOG_IL) << PF_LOG_IL;
    ap_uint<33> nsub = (ap_uint<33>(np) + c33 - 1) / c33;
    if (nsub > L) {
        ap_uint<33> per = (ap_uint<33>(np) + (L - 1)) / L;
        c33 = ((per + ilm) >> PF_LOG_IL) << PF_LOG_IL;
        nsub = (ap_uint<33>(np) + c33 - 1) / c33;
    }

    pf_dataflow(trades_f, trades_i, ids, out, spot, rate, divq, fw0, fw1, fw2, fw3, prm[0], prm[1], prm[2],
                n_trades, nid, id_base, uids ? 1 : 0, nu, np, ap_uint<11>(ns), u32(c33), ap_uint<8>(nsub),
                ap_uint<64>(seed), stage, ap_uint<1>(flags & 1));
}

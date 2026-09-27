// ref_bm.cpp - independent double-precision reference (std::mt19937_64 + Box-Muller, v1 kernel
// semantics: 4-factor + idiosyncratic spot normal, reflection or full truncation, log-Euler,
// arithmetic Asian average over steps 1..n). Checks |price_v2 - price_ref| <= 3 * combined SE.
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>
#include <omp.h>
#include "pf_engine.hpp"
#include "pf_io.hpp"

struct BM {
    std::mt19937_64 g;
    bool have = false; double spare = 0;
    explicit BM(uint64_t s) : g(s) {}
    double operator()() {
        if (have) { have = false; return spare; }
        double u1, u2;
        do { u1 = (g() >> 11) * 0x1p-53; } while (u1 <= 0);
        u2 = (g() >> 11) * 0x1p-53;
        double r = std::sqrt(-2 * std::log(u1)), t = 2 * M_PI * u2;
        spare = r * std::sin(t); have = true;
        return r * std::cos(t);
    }
};

static void ref_price(const Trade& t, const float* mkt, const pf::RunParams& rp, uint64_t npaths, uint64_t seed,
                      double& price, double& se) {
    uint32_t uid = t.underlying_id < rp.n_under ? t.underlying_id : 0;
    double S0 = mkt[uid] * (1.0 + rp.dspot), r = (double)mkt[1024 + uid] + rp.drate, q = mkt[2048 + uid];
    double dt = (double)t.T / rp.n_steps, sdt = std::sqrt(dt);
    double W[4], w2 = 0;
    for (int k = 0; k < 4; ++k) { W[k] = mkt[3072 + 4 * uid + k]; w2 += W[k] * W[k]; }
    double R = std::sqrt(std::max(0.0, 1 - w2)), rho = t.rho, srho = std::sqrt(std::max(0.0, 1 - rho * rho));
    double sig = (double)t.sigma * rp.volscale, v0 = t.v0 > 0 ? t.v0 : 1e-4;
    int nth = omp_get_max_threads();
    std::vector<double> S1(nth, 0), S2(nth, 0);
    #pragma omp parallel
    {
        int id = omp_get_thread_num();
        BM bm(seed * 1000003 + id);
        uint64_t mine = npaths / nth + (id < (int)(npaths % nth));
        for (uint64_t p = 0; p < mine; ++p) {
            double v = v0, lr = 0, sum = 0;
            for (uint32_t s = 0; s < rp.n_steps; ++s) {
                double zs = 0;
                for (int k = 0; k < 4; ++k) zs += W[k] * bm();
                zs += R * bm();
                double zv = rho * zs + srho * bm();
                double sq = std::sqrt(v);
                double vn = v + t.kappa * (t.theta - v) * dt + sig * sq * sdt * zv;
                lr += (r - q - 0.5 * v) * dt + sq * sdt * zs;
                v = rp.ft ? std::max(vn, 0.0) : std::fabs(vn);
                if (t.option_kind == 1) sum += S0 * std::exp(lr);
            }
            double Sp = t.option_kind == 1 ? sum / rp.n_steps : S0 * std::exp(lr);
            double po = std::max(t.option_type ? t.K - Sp : Sp - t.K, 0.0);
            S1[id] += po; S2[id] += po * po;
        }
    }
    double s1 = 0, s2 = 0;
    for (int i = 0; i < nth; ++i) { s1 += S1[i]; s2 += S2[i]; }
    double mean = s1 / npaths, var = std::max(0.0, s2 / npaths - mean * mean);
    double disc = std::exp(-r * t.T);
    price = disc * mean * t.notional * t.position;
    se = disc * std::sqrt(var / npaths) * t.notional;
}

int main(int argc, char** argv) {
    bench::Args a(argc, argv);
    std::string port = a.str("portfolio"), mk = a.str("market");
    uint32_t ntr = (uint32_t)a.i64("trades", 12);
    uint64_t P = (uint64_t)a.i64("paths", 200000);
    uint32_t steps = (uint32_t)a.i64("steps", 16);
    uint32_t n = pf::portfolio_count(port);
    std::vector<Trade> tr(n);
    pf::read_trades(port, 0, n, tr.data());
    std::vector<float> mkt(PF_MARKET_WORDS);
    pf::read_market(mk, mkt.data());
    // pick a mix: first calls/puts, European/Asian
    std::vector<uint32_t> pick;
    for (int want = 0; want < 4; ++want)
        for (uint32_t i = 0, got = 0; i < n && got < (ntr + 3) / 4; ++i)
            if (tr[i].option_type == (want & 1) && tr[i].option_kind == (want >> 1)) { pick.push_back(i); ++got; }
    int fails = 0;
    double worst = 0;
    pfcpu::Avx512Engine eng(pfcpu::Lut::gather);
    for (int ft = 0; ft <= 1; ++ft) {
        pf::RunParams rp;
        rp.n_under = 1024; rp.n_paths = (uint32_t)P; rp.n_steps = steps; rp.seed = 99; rp.stage = 1; rp.ft = ft;
        pf::CallConst cc = pf::call_const(rp);
        for (uint32_t i : pick) {
            pf::TradeConst c = pf::setup(tr[i], i, mkt.data(), rp, cc);
            std::vector<pf::Mom> part(64);
            #pragma omp parallel for schedule(dynamic, 1)
            for (int b = 0; b < 64; ++b) eng.sim(c, (uint32_t)(P * b / 64), (uint32_t)(P * (b + 1) / 64), part[b]);
            pf::Mom m;
            for (auto& x : part) m.add(x);
            Result r = pf::finalize(tr[i], mkt.data(), rp, m);
            double pr, se;
            ref_price(tr[i], mkt.data(), rp, P, 7 + i, pr, se);
            double comb = std::sqrt(se * se + (double)r.std_err * r.std_err);
            double zs = comb > 0 ? std::fabs(r.price - pr) / comb : 0;
            worst = std::max(worst, zs);
            std::printf("  %s trade %5u %s %s: v2 %10.5f +- %.5f   ref %10.5f +- %.5f   z=%.2f\n", ft ? "ft " : "ref", i,
                        tr[i].option_type ? "put " : "call", tr[i].option_kind ? "asian" : "euro ", r.price, r.std_err, pr,
                        se, zs);
            if (zs > 3) ++fails;
        }
    }
    std::printf("ref_bm: %zu trades x 2 schemes, %llu paths, %u steps, worst z=%.2f\n", pick.size() , (unsigned long long)P, steps, worst);
    std::printf(fails ? "ref_bm FAIL\n" : "ref_bm PASS\n");
    return fails ? 1 : 0;
}

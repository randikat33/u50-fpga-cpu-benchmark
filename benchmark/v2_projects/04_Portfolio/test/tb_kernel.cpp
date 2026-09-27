// tb_kernel.cpp - C-simulation testbench: kernel == scalar reference == AVX-512 (bit-identical moments)
// on a mixed small portfolio, for path counts not divisible by 16 / IL / lanes, stage-1 (sequential ids)
// and stage-2 (explicit ids), reflection and full truncation, split and unsplit lane jobs.
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>
#include "pf_model.hpp"
#include "pf_engine.hpp"
#include "pf_kernel.h"

static std::vector<Trade> make_trades(int n, std::vector<float>& mkt) {
    std::mt19937 g(777);
    std::uniform_real_distribution<float> U(0, 1);
    mkt.assign(PF_MARKET_WORDS + PF_PARAM_WORDS, 0.0f);
    for (int u = 0; u < PF_MAX_UNDER; ++u) {
        mkt[u] = 50.0f + 0.05f * u;
        mkt[PF_MAX_UNDER + u] = 0.01f + 0.00001f * u;
        mkt[2 * PF_MAX_UNDER + u] = (u % 3 == 0) ? 0.02f : 0.0f;
        for (int k = 0; k < PF_NF; ++k)
            mkt[3 * PF_MAX_UNDER + PF_NF * u + k] = (u == 5) ? 0.6f : 0.4f * U(g);   // u=5: sum w^2 > 1
    }
    std::vector<Trade> t(n);
    for (int i = 0; i < n; ++i) {
        Trade x{};
        x.underlying_id = (i % 11 == 10) ? 5000u : (i % 13 == 3 ? 5u : static_cast<uint32_t>(g() % 40));
        float S0 = x.underlying_id < 1024 ? mkt[x.underlying_id] : mkt[0];
        x.T = 0.02f + 2.9f * U(g);
        x.K = S0 * (0.6f + 0.8f * U(g));
        x.option_type = i % 2;
        x.option_kind = (i % 5 == 1) ? 1 : ((i % 7 == 6) ? 2 : 0);   // kind 2 -> European (v1)
        x.notional = 0.5f + 4.5f * U(g);
        x.position = (i % 9 == 0) ? -1.0f : 1.0f;
        x.v0 = (i % 17 == 4) ? 0.0f : 0.01f + 0.3f * U(g);
        x.kappa = 0.5f + 3.5f * U(g);
        x.theta = 0.01f + 0.09f * U(g);
        x.sigma = (i % 4 == 0) ? 1.0f : 0.2f + 0.8f * U(g);
        x.rho = (i % 8 == 0) ? -0.99f : (i % 8 == 1 ? 0.95f : -0.9f + 0.8f * U(g));
        t[i] = x;
    }
    return t;
}

int main(int argc, char** argv) {
    int n = argc > 1 ? atoi(argv[1]) : 29;
    std::vector<float> mkt;
    std::vector<Trade> tr = make_trades(n, mkt);
    mkt[PF_MARKET_WORDS] = 0.01f;    // dspot
    mkt[PF_MARKET_WORDS + 1] = 0.002f; // drate
    mkt[PF_MARKET_WORDS + 2] = 1.1f;   // volscale
    struct Cfg { uint32_t paths, steps, chunk, stage, ft, use_ids, n_under; };
    const Cfg cfgs[] = {
        {1, 1, 0, 1, 0, 0, 1024},   {13, 3, 0, 1, 0, 0, 1024},  {PF_IL + 1, 2, 0, 2, 1, 1, 1024},
        {300, 5, PF_IL, 1, 0, 0, 40}, {1000, 4, 1, 2, 0, 1, 1024}, {37, 7, 0, 1, 1, 0, 1024},
        {2 * PF_IL, 32, 0, 1, 0, 0, 1024},
    };
    bool avx = pfcpu::have_avx512();
#if PF_HAVE_AVX512
    pfcpu::Avx512Engine eng_g(pfcpu::Lut::gather), eng_s(pfcpu::Lut::scalar);
#endif
    int fails = 0, checks = 0;
    for (const Cfg& c : cfgs) {
        pf::RunParams rp;
        rp.n_under = c.n_under; rp.n_paths = c.paths; rp.n_steps = c.steps; rp.seed = 0x1234567890ABCDEFull;
        rp.stage = c.stage; rp.ft = c.ft;
        rp.dspot = mkt[PF_MARKET_WORDS]; rp.drate = mkt[PF_MARKET_WORDS + 1]; rp.volscale = mkt[PF_MARKET_WORDS + 2];
        pf::CallConst cc = pf::call_const(rp);
        std::vector<uint32_t> ids(n);
        uint32_t id_base = 1000;
        for (int i = 0; i < n; ++i) ids[i] = c.use_ids ? 70000 + 7 * i : id_base + i;
        std::vector<uint64_t> out(4 * n, 0xDEADull);
        pf_kernel(reinterpret_cast<const float*>(tr.data()), reinterpret_cast<const uint32_t*>(tr.data()),
                  ids.data(), mkt.data(), out.data(), n, c.use_ids ? n : 0, id_base, c.use_ids, c.n_under,
                  c.paths, c.steps, c.chunk, rp.seed, c.stage, c.ft);
        for (int i = 0; i < n; ++i) {
            pf::TradeConst tc = pf::setup(tr[i], ids[i], mkt.data(), rp, cc);
            pf::Mom ref;
            pf::sim_range(tc, 0, c.paths, ref);
            Moments rec;
            std::memcpy(&rec, &out[4 * i], 32);
            pf::Mom k = pf::from_record(rec);
            ++checks;
            if (k != ref || rec.tag != (ids[i] | (1ull << 63))) {
                ++fails;
                std::printf("MISMATCH kernel cfg(paths=%u steps=%u chunk=%u) trade %d: %llu vs %llu\n", c.paths,
                            c.steps, c.chunk, i, (unsigned long long)k.sum, (unsigned long long)ref.sum);
            }
#if PF_HAVE_AVX512
            if (avx) {
                // uneven split into blocks to exercise partition independence
                pf::Mom a, b;
                uint32_t cut = c.paths / 3;
                eng_g.sim(tc, 0, cut, a);
                eng_g.sim(tc, cut, c.paths, a);
                eng_s.sim(tc, 0, c.paths, b);
                checks += 2;
                if (a != ref) { ++fails; std::printf("MISMATCH avx512(gather) trade %d\n", i); }
                if (b != ref) { ++fails; std::printf("MISMATCH avx512(scalar-lut) trade %d\n", i); }
            }
#endif
            // float results identical too
            Result r1 = pf::finalize(tr[i], mkt.data(), rp, ref), r2 = pf::finalize(tr[i], mkt.data(), rp, k);
            if (std::memcmp(&r1, &r2, 8)) { ++fails; std::printf("MISMATCH finalize trade %d\n", i); }
        }
    }
    std::printf("tb_kernel: LANES=%d IL=%u trades=%d checks=%d fails=%d avx512=%d\n", PF_LANES, PF_IL, n, checks,
                fails, (int)avx);
    std::printf(fails ? "tb_kernel FAIL\n" : "tb_kernel PASS\n");
    return fails ? 1 : 0;
}

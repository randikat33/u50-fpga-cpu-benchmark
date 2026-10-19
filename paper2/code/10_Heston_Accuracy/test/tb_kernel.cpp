// tb_kernel.cpp - C-simulation testbench: kernel == scalar reference model (bit-identical moments)
// for both test cases families, pair counts not divisible by the lane count, M = 1..3 (settle back-
// pressure), empty calls, pair indices near 2^32, extreme parameters, and partition invariance.
#include <cstdio>
#include <cstdlib>
#include "hq_kernel.h"
#include "hq_model.hpp"

struct TC { const char* cs; uint64_t base, pairs; uint32_t M; uint64_t seed; };

static hq::Mom run_kernel(const float* fp, const mc::Keys& k, uint64_t base, uint64_t pairs, uint32_t M) {
    uint64_t out[HQ_OUT_N] = {0};
    HQ_TOP(fp, out, base, pairs, M, mc::keys_lo(k), mc::keys_hi(k));
    uint32_t lanes = (out[HQ_W_INFO] >> 32) & 0xFF, il = (out[HQ_W_INFO] >> 40) & 0xFFFF, sc = out[HQ_W_INFO] >> 56;
    if (lanes != HQ_LANES || il != HQ_IL || sc != HQ_SCHEME) { std::printf("bad info word\n"); std::exit(1); }
    return hq::mom_from_words(out);
}

int main() {
    int fails = 0;
    const hq::Scheme sc = HQ_SCHEME == 0 ? hq::FT : hq::QE;
    const TC cases[] = {
        {"I", 0, 0, 8, 1},                    // empty call
        {"I", 0, 1, 1, 12345},                // single pair, M = 1 (settle back-pressure)
        {"P1", 0, 7, 2, 12345},               // pairs < lanes, M = 2
        {"II", 5, 37, 3, 777},                // offset base, not divisible by lanes
        {"III", 0, 300, 40, 12345},           // protocol-like step count
        {"I", 1000, 2 * HQ_IL * HQ_LANES + 3, 4, 3},   // several waves per lane + remainder
        {"II", 0, 257, 60, 5},                // long maturity, Feller strongly violated
        {"P1", 0, 513, 16, 8},                // r > 0 (discount and drift)
        {"III", 4000000000ull, 50, 33, 12345},// pair indices near 2^32
        {"I", 0, 200, 1, 99},                 // one huge step (exercises both QE branches heavily)
    };
    for (const TC& t : cases) {
        hq::Case c = hq::get_case(t.cs);
        float fp[HQ_FPAR_N];
        hq::make_fpar(c, t.M, fp);
        mc::Keys k = mc::run_keys(t.seed);
        hq::Mom ref;
        hq::sim_range(fp, sc, k, t.M, t.base, t.base + t.pairs, ref);
        hq::Mom ker = run_kernel(fp, k, t.base, t.pairs, t.M);
        bool ok = ker == ref;
        hq::Price p = hq::finalize_k(c, ref, 1);
        std::printf("%s case=%-3s base=%llu pairs=%llu M=%u  ref=%016llx ker=%016llx ovf=%llu price(K=100)=%.5f\n",
                    ok ? "PASS" : "FAIL", t.cs, (unsigned long long)t.base, (unsigned long long)t.pairs, t.M,
                    (unsigned long long)hq::mom_hash(ref), (unsigned long long)hq::mom_hash(ker),
                    (unsigned long long)ref.ovf, p.price);
        fails += !ok;
    }
    {   // overflow path: huge strikes make every (put) payoff saturate (counted per side and strike)
        hq::Case c = hq::get_case("I");
        for (int j = 0; j < HQ_NK; ++j) c.K[j] = 3.0e7;
        float fp[HQ_FPAR_N];
        hq::make_fpar(c, 4, fp);
        mc::Keys k = mc::run_keys(7);
        hq::Mom ref;
        hq::sim_range(fp, sc, k, 4, 0, 40, ref);
        hq::Mom ker = run_kernel(fp, k, 0, 40, 4);
        bool ok = ker == ref && ref.ovf > 0;
        std::printf("%s saturating payoffs (ovf ref %llu, kernel %llu)\n", ok ? "PASS" : "FAIL",
                    (unsigned long long)ref.ovf, (unsigned long long)ker.ovf);
        fails += !ok;
    }
    {   // partition invariance: [0,a) + [a,n) == [0,n)
        hq::Case c = hq::get_case("II");
        float fp[HQ_FPAR_N];
        hq::make_fpar(c, 6, fp);
        mc::Keys k = mc::run_keys(42);
        hq::Mom a = run_kernel(fp, k, 0, 61, 6), b = run_kernel(fp, k, 61, 140, 6), all = run_kernel(fp, k, 0, 201, 6);
        a.add(b);
        bool ok = a == all;
        std::printf("%s partition invariance (61 + 140 == 201 pairs)\n", ok ? "PASS" : "FAIL");
        fails += !ok;
    }
    std::printf("tb_kernel: scheme=%s lanes=%d IL=%d  %s\n", HQ_SCHEME == 0 ? "FT" : "QE", HQ_LANES, HQ_IL,
                fails ? "FAILED" : "ALL PASS");
    return fails ? 1 : 0;
}

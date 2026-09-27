// tb_kernel.cpp - C-simulation testbench: kernel == scalar reference == AVX-512 (bit-identical)
// for several (pairs, steps, base, seed, call/put, correlation) including pair counts not divisible
// by the lane count or 16, steps smaller than IL, M = 1, empty calls and a partition-invariance check.
#include <cstdio>
#include <cstdlib>
#include "mc_heston_v2.h"
#include "mc_model.hpp"
#include "../cpu/mc_avx512.hpp"

struct Case { uint64_t base, pairs; uint32_t M; uint64_t seed; bool put; double corr; };

static mc::Mom run_kernel(const float* fp, const mc::Keys& k, const Case& c) {
    uint64_t out[8] = {0};
    mc_heston_v2(fp, out, c.base, c.pairs, c.M, c.put ? 1 : 0, mc::keys_lo(k), mc::keys_hi(k));
    uint32_t lanes = (out[7] >> 32) & 0xFF, il = (out[7] >> 40) & 0xFFFF;
    if (lanes != MC_LANES || il != MC_IL) { std::printf("bad info word\n"); std::exit(1); }
    return mc::mom_from_words(out);
}

int main() {
    const Case cases[] = {
        {0, 0, 8, 1, false, 0.0},          // empty call
        {0, 1, 1, 12345, false, 0.0},      // single pair, M = 1
        {0, 7, 3, 12345, false, 0.0},      // pairs < lanes, steps < IL
        {5, 37, 5, 777, true, 0.0},        // put, offset base, not divisible by lanes or 16
        {0, 131, 16, 12345, false, 0.5},   // correlated basket (full Cholesky), > IL pairs
        {1000, 300, 2, 99, false, 0.0},    // several waves per lane
        {0, 128 * MC_LANES + 3, 4, 3, true, 0.3},  // exactly-full waves + remainder
        {4000000000ull, 50, 33, 12345, false, 0.0}, // pair indices near 2^32
    };
    int fails = 0;
    for (const Case& c : cases) {
        mc::Market mk = mc::v1_market();
        mk.put = c.put;
        if (c.corr != 0.0) mc::set_equicorr(mk, c.corr);
        float fp[MC_FPAR_N];
        mc::make_fpar(mk, c.M, fp);
        mc::Keys k = mc::run_keys(c.seed);
        mc::Mom ref, avx;
        mc::sim_range(fp, k, c.M, c.base, c.base + c.pairs, c.put, ref);
#if MC_HAVE_AVX512
        mc::avx::sim_range(fp, k, c.M, c.base, c.base + c.pairs, c.put, avx);
#else
        avx = ref;
#endif
        mc::Mom ker = run_kernel(fp, k, c);
        bool ok = (ker == ref) && (avx == ref);
        mc::Price p = mc::finalize(mk, c.M, ref);
        std::printf("%s base=%llu pairs=%llu M=%u put=%d corr=%.1f  ref=%016llx ker=%016llx avx=%016llx price=%.6f\n",
                    ok ? "PASS" : "FAIL", (unsigned long long)c.base, (unsigned long long)c.pairs, c.M, c.put,
                    c.corr, (unsigned long long)mc::mom_hash(ref), (unsigned long long)mc::mom_hash(ker),
                    (unsigned long long)mc::mom_hash(avx), p.price);
        fails += !ok;
    }
    // partition invariance: two kernel calls over [0,a) and [a,n) == one call over [0,n)
    {
        mc::Market mk = mc::v1_market();
        float fp[MC_FPAR_N];
        mc::make_fpar(mk, 6, fp);
        mc::Keys k = mc::run_keys(42);
        Case c1{0, 61, 6, 42, false, 0}, c2{61, 140, 6, 42, false, 0}, call{0, 201, 6, 42, false, 0};
        mc::Mom a = run_kernel(fp, k, c1), b = run_kernel(fp, k, c2), all = run_kernel(fp, k, call);
        a.add(b);
        bool ok = a == all;
        std::printf("%s partition invariance (61 + 140 == 201 pairs)\n", ok ? "PASS" : "FAIL");
        fails += !ok;
    }
    std::printf("tb_kernel: lanes=%d IL=%d  %s\n", MC_LANES, MC_IL, fails ? "FAILED" : "ALL PASS");
    return fails ? 1 : 0;
}

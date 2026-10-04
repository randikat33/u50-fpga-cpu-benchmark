// tb_kernel.cpp - C-simulation testbench: kernel == scalar reference == AVX-512 (bit-identical)
// for several (pairs, steps, base, seed, call/put) including pair counts not divisible by the lane
// count or 16, M = 1, M < L (merge back-pressure), empty calls and a partition-invariance check.
// Also checks that the vscalefps exp of the AVX path equals the P2-table exp for all exponents used.
#include <cstdio>
#include <cstdlib>
#include "mc_asian.h"
#include "asian_model.hpp"
#include "../cpu/asian_avx512.hpp"

struct Case { uint64_t base, pairs; uint32_t M; uint64_t seed; bool put; double sigma, S0; };

static asn::Mom run_kernel(const float* fp, const asn::Keys& k, const Case& c) {
    uint64_t out[8] = {0};
    mc_asian(fp, out, c.base, c.pairs, c.M, c.put ? 1 : 0, mc::keys_lo(k), mc::keys_hi(k));
    uint32_t lanes = (out[7] >> 32) & 0xFF, il = (out[7] >> 40) & 0xFFFF;
    if (lanes != AS_LANES || il != AS_IL) { std::printf("bad info word\n"); std::exit(1); }
    return mc::mom_from_words(out);
}

int main() {
    int fails = 0;
#if AS_HAVE_AVX512
    {   // exp: vscalefps path == table path over X in [-130, 130] (every exponent n, clamping, NaN)
        uint64_t bad = 0, n = 0;
        alignas(64) float x[16], r[16];
        for (int64_t i = -130 * 4096; i <= 130 * 4096; i += 16) {
            for (int l = 0; l < 16; ++l) x[l] = (float)(i + l) / 4096.0f + 1e-7f * l;
            if (i == 0) x[3] = __builtin_nanf("");
            _mm512_store_ps(r, asn::avx::fexp_half_v(_mm512_load_ps(x)));
            for (int l = 0; l < 16; ++l, ++n) {
                float ref = mc::fexp_half(x[l]);
                if (std::memcmp(&ref, &r[l], 4) != 0) ++bad;
            }
        }
        std::printf("%s exp: vscalefps == P2 table for %llu inputs (%llu differ)\n", bad ? "FAIL" : "PASS",
                    (unsigned long long)n, (unsigned long long)bad);
        fails += bad != 0;
    }
#endif
    const Case cases[] = {
        {0, 0, 8, 1, false, 0.2, 100},             // empty call
        {0, 1, 1, 12345, false, 0.2, 100},         // single pair, M = 1
        {0, 7, 3, 12345, false, 0.2, 100},         // pairs < lanes, steps < lanes (merge back-pressure)
        {5, 37, 5, 777, true, 0.3, 100},           // put, offset base, not divisible by lanes or 16
        {0, 300, 64, 12345, false, 0.2, 100},      // benchmark step count
        {1000, 2 * 128 * AS_LANES + 3, 4, 3, true, 0.2, 95},   // several waves per lane + remainder
        {0, 257, 40, 5, false, 1.5, 100},          // high volatility (large exp range)
        {0, 99, 16, 8, false, 0.2, 0.01},          // tiny prices (payoff 0 everywhere, exp near underflow)
        {4000000000ull, 50, 33, 12345, false, 0.2, 100},   // pair indices near 2^32
    };
    for (const Case& c : cases) {
        asn::Market mk;
        mk.put = c.put; mk.sigma = c.sigma; mk.S0 = c.S0;
        float fp[AS_FPAR_N];
        asn::make_fpar(mk, c.M, fp);
        asn::Keys k = mc::run_keys(c.seed);
        asn::Mom ref, avx;
        asn::sim_range(fp, k, c.M, c.base, c.base + c.pairs, c.put, ref);
#if AS_HAVE_AVX512
        asn::avx::sim_range<2>(fp, k, c.M, c.base, c.base + c.pairs, c.put, avx);
#else
        avx = ref;
#endif
        asn::Mom ker = run_kernel(fp, k, c);
        bool ok = (ker == ref) && (avx == ref);
        mc::Price p = asn::finalize(mk, c.M, ref);
        std::printf("%s base=%llu pairs=%llu M=%u put=%d sigma=%.1f  ref=%016llx ker=%016llx avx=%016llx price=%.6f\n",
                    ok ? "PASS" : "FAIL", (unsigned long long)c.base, (unsigned long long)c.pairs, c.M, c.put,
                    c.sigma, (unsigned long long)mc::mom_hash(ref), (unsigned long long)mc::mom_hash(ker),
                    (unsigned long long)mc::mom_hash(avx), p.price);
        fails += !ok;
    }
    {   // partition invariance: two kernel calls over [0,a) and [a,n) == one call over [0,n)
        asn::Market mk;
        float fp[AS_FPAR_N];
        asn::make_fpar(mk, 6, fp);
        asn::Keys k = mc::run_keys(42);
        Case c1{0, 61, 6, 42, false, 0.2, 100}, c2{61, 140, 6, 42, false, 0.2, 100}, call{0, 201, 6, 42, false, 0.2, 100};
        asn::Mom a = run_kernel(fp, k, c1), b = run_kernel(fp, k, c2), all = run_kernel(fp, k, call);
        a.add(b);
        bool ok = a == all;
        std::printf("%s partition invariance (61 + 140 == 201 pairs)\n", ok ? "PASS" : "FAIL");
        fails += !ok;
    }
    std::printf("tb_kernel: lanes=%d IL=%d  %s\n", AS_LANES, AS_IL, fails ? "FAILED" : "ALL PASS");
    return fails ? 1 : 0;
}

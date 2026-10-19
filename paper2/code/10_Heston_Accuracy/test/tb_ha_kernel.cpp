// tb_ha_kernel.cpp - C-simulation testbench of the Asian kernel: kernel == ha_model.hpp (bit-identical
// moments): pair counts not divisible by the lane count, M = 1..3 (settle back-pressure), empty calls,
// indices near 2^32, saturating payoffs, clamped prices, partition invariance.
#include <cstdio>
#include <cstdlib>
#include "ha_kernel.h"
#include "ha_model.hpp"

static ha::AMom run_kernel(const float* fp, const mc::Keys& k, uint64_t base, uint64_t pairs, uint32_t M) {
    uint64_t out[HA_OUT_N] = {0};
    HA_TOP(fp, out, base, pairs, M, mc::keys_lo(k), mc::keys_hi(k));
    uint32_t lanes = (out[HA_W_INFO] >> 32) & 0xFF, il = (out[HA_W_INFO] >> 40) & 0xFFFF, sc = out[HA_W_INFO] >> 56;
    if (lanes != HA_LANES || il != HA_IL || sc != 0x20) { std::printf("bad info word\n"); std::exit(1); }
    return ha::mom_from_words(out);
}

int main() {
    int fails = 0;
    struct TC { uint64_t base, pairs; uint32_t M; uint64_t seed; };
    const TC cases[] = {
        {0, 0, 8, 1}, {0, 1, 1, 12345}, {0, 7, 2, 12345}, {5, 37, 3, 777}, {0, 300, 250, 12345},
        {1000, 2 * HA_IL * HA_LANES + 3, 4, 3}, {4294967000ull, 200, 12, 5}, {0, 513, 52, 8},
    };
    ha::ACase c = ha::get_acase("A");
    for (const TC& t : cases) {
        ha::ACase cm = c; cm.M = t.M;
        float fp[HA_FPAR_N];
        ha::make_fpar(cm, t.M, fp);
        mc::Keys k = mc::run_keys(t.seed);
        ha::AMom ref;
        ha::sim_range(fp, k, t.M, t.base, t.base + t.pairs, ref);
        ha::AMom ker = run_kernel(fp, k, t.base, t.pairs, t.M);
        bool ok = ker == ref;
        ha::APrice p = ha::finalize_k(cm, ref, 1, 1.03);
        std::printf("%s base=%llu pairs=%llu M=%u  ref=%016llx ker=%016llx ovf=%llu plain(K=100)=%.5f cv=%.5f\n", ok ? "PASS" : "FAIL",
                    (unsigned long long)t.base, (unsigned long long)t.pairs, t.M, (unsigned long long)ha::mom_hash(ref),
                    (unsigned long long)ha::mom_hash(ker), (unsigned long long)ref.ovf, p.plain, p.cv);
        fails += !ok;
    }
    {   // saturating payoffs (tiny strikes are fine; use a huge S0 so prices clamp and payoffs saturate)
        ha::ACase c2 = c; c2.S0 = 3.0e7; c2.M = 6;
        float fp[HA_FPAR_N];
        ha::make_fpar(c2, 6, fp);
        mc::Keys k = mc::run_keys(7);
        ha::AMom ref;
        ha::sim_range(fp, k, 6, 0, 40, ref);
        ha::AMom ker = run_kernel(fp, k, 0, 40, 6);
        bool ok = ker == ref && ref.ovf > 0;
        std::printf("%s clamped prices and saturating payoffs (ovf ref %llu, kernel %llu)\n", ok ? "PASS" : "FAIL",
                    (unsigned long long)ref.ovf, (unsigned long long)ker.ovf);
        fails += !ok;
    }
    {
        float fp[HA_FPAR_N];
        ha::ACase c3 = c; c3.M = 20;
        ha::make_fpar(c3, 20, fp);
        mc::Keys k = mc::run_keys(42);
        ha::AMom a = run_kernel(fp, k, 0, 61, 20), b = run_kernel(fp, k, 61, 140, 20), all = run_kernel(fp, k, 0, 201, 20);
        a.add(b);
        bool ok = a == all;
        std::printf("%s partition invariance (61 + 140 == 201 pairs)\n", ok ? "PASS" : "FAIL");
        fails += !ok;
    }
    std::printf("tb_ha_kernel: lanes=%d IL=%d  %s\n", HA_LANES, HA_IL, fails ? "FAILED" : "ALL PASS");
    return fails ? 1 : 0;
}

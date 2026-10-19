// tb_hb_kernel.cpp - C-simulation testbench of the basket kernel: kernel == hb_model.hpp (bit-identical
// moments): pair counts not divisible by the lane count, M = 1..3, empty calls, indices near 2^29,
// saturating payoffs, clamped prices, partition invariance.
#include <cstdio>
#include <cstdlib>
#include "hb_kernel.h"
#include "hb_model.hpp"

static hq::Mom run_kernel(const float* fp, const mc::Keys& k, uint64_t base, uint64_t pairs, uint32_t M) {
    uint64_t out[HQ_OUT_N] = {0};
    HB_TOP(fp, out, base, pairs, M, mc::keys_lo(k), mc::keys_hi(k));
    uint32_t lanes = (out[HQ_W_INFO] >> 32) & 0xFF, il = (out[HQ_W_INFO] >> 40) & 0xFFFF, sc = out[HQ_W_INFO] >> 56;
    if (lanes != HQ_LANES || il != HQ_IL || sc != (HQ_SCHEME | 0x10)) { std::printf("bad info word\n"); std::exit(1); }
    return hq::mom_from_words(out);
}

int main() {
    int fails = 0;
    const hq::Scheme sc = HQ_SCHEME == 0 ? hq::FT : hq::QE;
    struct TC { uint64_t base, pairs; uint32_t M; uint64_t seed; };
    const TC cases[] = {
        {0, 0, 8, 1}, {0, 1, 1, 12345}, {0, 7, 2, 12345}, {5, 37, 3, 777}, {0, 300, 4, 12345},
        {1000, 2 * HQ_IL * HQ_LANES / 8 + 3, 2, 3}, {0, 129, 16, 8}, {(1ull << 29) - 50, 50, 5, 12345}, {0, 200, 1, 99},
    };
    hb::BCase c = hb::get_bcase("B");
    for (const TC& t : cases) {
        float fp[HB_FPAR_N];
        hb::make_fpar(c, t.M, fp);
        mc::Keys k = mc::run_keys(t.seed);
        hq::Mom ref;
        hb::sim_range(fp, sc, k, t.M, t.base, t.base + t.pairs, ref);
        hq::Mom ker = run_kernel(fp, k, t.base, t.pairs, t.M);
        bool ok = ker == ref;
        std::printf("%s base=%llu pairs=%llu M=%u  ref=%016llx ker=%016llx ovf=%llu price(K=100)=%.5f\n", ok ? "PASS" : "FAIL",
                    (unsigned long long)t.base, (unsigned long long)t.pairs, t.M, (unsigned long long)hq::mom_hash(ref),
                    (unsigned long long)hq::mom_hash(ker), (unsigned long long)ref.ovf, hb::finalize_k(c, ref, 1).price);
        fails += !ok;
    }
    {   // saturating payoffs (huge strikes, put form) and clamped prices (huge S0)
        hb::BCase c2 = c;
        for (int j = 0; j < HQ_NK; ++j) c2.K[j] = 3.0e7;
        c2.S0[3] = 5.0e8;
        float fp[HB_FPAR_N];
        hb::make_fpar(c2, 4, fp);
        fp[hq::P_PUTS] = 7.0f;
        mc::Keys k = mc::run_keys(7);
        hq::Mom ref;
        hb::sim_range(fp, sc, k, 4, 0, 40, ref);
        hq::Mom ker = run_kernel(fp, k, 0, 40, 4);
        bool ok = ker == ref && ref.ovf > 0;
        std::printf("%s saturating payoffs and clamped prices (ovf ref %llu, kernel %llu)\n", ok ? "PASS" : "FAIL",
                    (unsigned long long)ref.ovf, (unsigned long long)ker.ovf);
        fails += !ok;
    }
    {
        float fp[HB_FPAR_N];
        hb::make_fpar(c, 6, fp);
        mc::Keys k = mc::run_keys(42);
        hq::Mom a = run_kernel(fp, k, 0, 61, 6), b = run_kernel(fp, k, 61, 140, 6), all = run_kernel(fp, k, 0, 201, 6);
        a.add(b);
        bool ok = a == all;
        std::printf("%s partition invariance (61 + 140 == 201 basket pairs)\n", ok ? "PASS" : "FAIL");
        fails += !ok;
    }
    std::printf("tb_hb_kernel: scheme=%s lanes=%d IL=%d  %s\n", HQ_SCHEME == 0 ? "FT" : "QE", HQ_LANES, HQ_IL,
                fails ? "FAILED" : "ALL PASS");
    return fails ? 1 : 0;
}

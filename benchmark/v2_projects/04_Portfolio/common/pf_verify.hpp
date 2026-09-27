// pf_verify.hpp - untimed verification against the scalar reference model (OpenMP-parallel).
#pragma once
#include <vector>
#include "pf_io.hpp"
#ifdef _OPENMP
#include <omp.h>
#endif

namespace pf {

// Recompute moments for trades `list` (positions into `ids`/`recs`) and compare exactly.
// Returns number of mismatches.
inline long verify_moments(const std::vector<uint32_t>& list, const Trade* const* recs, const uint32_t* ids,
                           const Mom* got, const float* mkt, const RunParams& rp) {
    CallConst cc = call_const(rp);
    long bad = 0;
    #pragma omp parallel for schedule(dynamic, 1) reduction(+ : bad)
    for (long j = 0; j < (long)list.size(); ++j) {
        uint32_t t = list[j];
        TradeConst c = setup(*recs[t], ids[t], mkt, rp, cc);
        Mom m;
        sim_range(c, 0, rp.n_paths, m);
        if (m != got[t]) ++bad;
    }
    return bad;
}

inline std::vector<uint32_t> verify_sample(uint32_t n, uint32_t want, int mode) {
    std::vector<uint32_t> v;
    if (mode == 2 || want >= n) {
        for (uint32_t i = 0; i < n; ++i) v.push_back(i);
    } else {
        for (uint32_t i = 0; i < want; ++i) v.push_back((uint32_t)((uint64_t)i * n / want));
        if (n) v.push_back(n - 1);
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
    }
    return v;
}

}  // namespace pf

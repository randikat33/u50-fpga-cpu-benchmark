// hb_double.hpp - double-precision basket (Case B): the same units and draws as hb_model.hpp (same
// streams, same integer normals), double arithmetic and library exp/log/sqrt. Used for the reference price
// (protocol v0.5 Section 4b: QE at h = 1/128, FT at h = 1/512) and for the float-minus-double check.
#pragma once
#include "hb_model.hpp"
#include "hq_double.hpp"

namespace hbd {

// one unit (asset j): log-price x (natural units, without the drift), from ln S0_j
inline void sim_unit(const hqd::DPar& p, double lnS0, hq::Scheme sc, const mc::Keys& k, uint32_t M, uint32_t u,
                     double& xA, double& xB) {
    mc::Xo s = mc::pair_seed(k, u);
    double VA = p.U0, VB = p.U0;
    xA = xB = lnS0;
    for (uint32_t t = 0; t < M; ++t) {
        uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
        if (sc == hq::FT) {
            double z1 = hqd::zn(u1), z2 = hqd::zn(u2), zv = p.rho * z1 + p.srho * z2;
            hqd::ft_side(p, xA, VA, z1, zv, p.dt);
            hqd::ft_side(p, xB, VB, -z1, -zv, p.dt);
        } else {
            double z = hqd::zn(u2);
            hqd::qe_side(p, xA, VA, u1, z);
            hqd::qe_side(p, xB, VB, ~u1, -z);
        }
    }
}
inline void bpayoff(const hqd::DPar& p, double B, double out[HQ_NK]) {
    for (int k = 0; k < HQ_NK; ++k) {
        double d = ((p.puts >> k) & 1u) ? p.K[k] - B : B - p.K[k];
        out[k] = d > 0 ? d : 0.0;
    }
}
// both sides of one basket pair; payoffs undiscounted
inline void sim_pair(const hqd::DPar& p, const hb::BCase& c, hq::Scheme sc, const mc::Keys& k, uint32_t M, uint32_t a,
                     double pA[HQ_NK], double pB[HQ_NK]) {
    double BA = 0, BB = 0, g = p.D2 * 0.5;            // r T
    for (int j = 0; j < HB_NA; ++j) {
        double xA, xB;
        sim_unit(p, std::log(c.S0[j]), sc, k, M, (uint32_t)(HB_NA * a + j), xA, xB);
        BA += std::exp(xA + g);
        BB += std::exp(xB + g);
    }
    bpayoff(p, BA / HB_NA, pA);
    bpayoff(p, BB / HB_NA, pB);
}
inline hq::Price finalize_k(const hb::BCase& c, const hqd::DMom& m, int k) {
    hq::Case u = c.unit_case();
    u.S0 = c.B0();
    return hqd::finalize_k(u, m, k);
}

}  // namespace hbd

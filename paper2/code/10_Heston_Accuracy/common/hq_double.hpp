// hq_double.hpp - double-precision versions of the two schemes, used only for calibration:
//   - "double": the same draws as the float model (same pair streams, same integer normals), but
//     double arithmetic and library exp/log/sqrt. With --impl diff the float and double payoffs of
//     the same path are subtracted, which measures the float32 + table error with a tiny variance.
//   - "coupled" (FT only): the payoff difference between step h and step 2h on the same Brownian
//     path (coarse normal = (Z_a + Z_b)/sqrt 2), as in multilevel MC. Its mean is b(h) - b(2h),
//     estimated with far fewer paths than two separate runs need.
#pragma once
#include <cmath>
#include "hq_model.hpp"

namespace hqd {
using hq::Case;

struct DPar {
    double X0, D2, dt, kappa, theta, eps, rho, srho, U0;
    double E, QC1, QC2, K0, K1, K2, K3, K4;
    double K[HQ_NK];
    unsigned puts;
};
inline DPar make_dpar(const Case& c, uint32_t M) {
    DPar p{};
    p.dt = c.T / M;
    p.X0 = 2.0 * std::log(c.S0);
    p.D2 = 2.0 * c.r * c.T;
    p.kappa = c.kappa; p.theta = c.theta; p.eps = c.eps; p.rho = c.rho;
    p.srho = std::sqrt(1.0 - c.rho * c.rho);
    p.U0 = c.v0;                                   // state V (not V dt) in the double model
    double dt = p.dt, E = std::exp(-c.kappa * dt), a = c.kappa * c.rho / c.eps - 0.5;
    p.E = E;
    p.QC1 = c.eps * c.eps * E * (1.0 - E) / c.kappa;
    p.QC2 = c.theta * c.eps * c.eps * (1.0 - E) * (1.0 - E) / (2.0 * c.kappa);
    p.K0 = -c.rho * c.kappa * c.theta * dt / c.eps;
    p.K1 = 0.5 * dt * a - c.rho / c.eps;
    p.K2 = 0.5 * dt * a + c.rho / c.eps;
    p.K3 = 0.5 * dt * (1.0 - c.rho * c.rho);
    p.K4 = p.K3;
    for (int k = 0; k < HQ_NK; ++k) p.K[k] = c.K[k];
    p.puts = c.puts;
    return p;
}
inline double zn(uint32_t u) { return std::ldexp((double)mc::icdf(u), -20); }   // same normal as float model

// FT in natural units: x = ln S (no drift), V may be negative
inline void ft_side(const DPar& p, double& x, double& V, double z1, double zv, double dt) {
    double vp = V > 0 ? V : 0, sv = std::sqrt(vp * dt);
    x += -0.5 * vp * dt + sv * z1;
    V += p.kappa * (p.theta - vp) * dt + p.eps * sv * zv;
}
// QE, natural units
inline void qe_side(const DPar& p, double& x, double& V, uint32_t uv, double z) {
    double m = p.theta + (V - p.theta) * p.E;
    double s2 = V * p.QC1 + p.QC2;
    double psi = s2 / (m * m), vn;
    if (psi <= 1.5) {
        double ip = 2.0 / psi, b2 = ip - 1.0 + std::sqrt(ip) * std::sqrt(ip - 1.0);
        double a = m / (1.0 + b2), t = std::sqrt(b2) + zn(uv);
        vn = a * t * t;
    } else {
        double pp = (psi - 1.0) / (psi + 1.0), beta = (1.0 - pp) / m;
        double omu = std::ldexp((double)(~uv) + 0.5, -32);           // 1 - U
        vn = (1.0 - omu <= pp) ? 0.0 : std::log((1.0 - pp) / omu) / beta;
    }
    x += p.K0 + p.K1 * V + p.K2 * vn + std::sqrt(p.K3 * V + p.K4 * vn) * z;
    V = vn;
}
inline void payoff(const DPar& p, double x, double out[HQ_NK]) {
    double S = std::exp(0.5 * (2.0 * x + p.D2));                    // x is ln S without drift
    for (int k = 0; k < HQ_NK; ++k) {
        double d = ((p.puts >> k) & 1u) ? p.K[k] - S : S - p.K[k];   // put or call payoff
        out[k] = d > 0 ? d : 0.0;
    }
}
// both sides of one pair, double; returns payoffs (undiscounted)
inline void sim_pair(const DPar& p, hq::Scheme sc, const mc::Keys& k, uint32_t M, uint32_t a,
                     double pA[HQ_NK], double pB[HQ_NK]) {
    mc::Xo s = mc::pair_seed(k, a);
    double xA = 0.5 * p.X0, xB = xA, VA = p.U0, VB = p.U0;
    for (uint32_t t = 0; t < M; ++t) {
        uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
        if (sc == hq::FT) {
            double z1 = zn(u1), z2 = zn(u2), zv = p.rho * z1 + p.srho * z2;
            ft_side(p, xA, VA, z1, zv, p.dt);
            ft_side(p, xB, VB, -z1, -zv, p.dt);
        } else {
            double z = zn(u2);
            qe_side(p, xA, VA, u1, z);
            qe_side(p, xB, VB, ~u1, -z);
        }
    }
    payoff(p, xA, pA);
    payoff(p, xB, pB);
}
// FT, fine step dt (M steps) and coarse step 2 dt (M/2 steps) on the same Brownian path (M even)
inline void ft_coupled_pair(const DPar& p, const mc::Keys& k, uint32_t M, uint32_t a,
                            double dA[HQ_NK], double dB[HQ_NK]) {
    mc::Xo s = mc::pair_seed(k, a);
    double xf[2], Vf[2], xc[2], Vc[2];
    for (int j = 0; j < 2; ++j) { xf[j] = xc[j] = 0.5 * p.X0; Vf[j] = Vc[j] = p.U0; }
    const double r2 = 1.0 / std::sqrt(2.0);
    for (uint32_t t = 0; t < M; t += 2) {
        double z1[2], zv[2];
        for (int h = 0; h < 2; ++h) {
            uint32_t u1 = mc::xo_next(s), u2 = mc::xo_next(s);
            z1[h] = zn(u1);
            zv[h] = p.rho * z1[h] + p.srho * zn(u2);
        }
        double c1 = (z1[0] + z1[1]) * r2, cv = (zv[0] + zv[1]) * r2;
        for (int j = 0; j < 2; ++j) {
            double sg = j == 0 ? 1.0 : -1.0;
            ft_side(p, xf[j], Vf[j], sg * z1[0], sg * zv[0], p.dt);
            ft_side(p, xf[j], Vf[j], sg * z1[1], sg * zv[1], p.dt);
            ft_side(p, xc[j], Vc[j], sg * c1, sg * cv, 2.0 * p.dt);
        }
    }
    double pf[HQ_NK], pc[HQ_NK];
    payoff(p, xf[0], pf); payoff(p, xc[0], pc);
    for (int k2 = 0; k2 < HQ_NK; ++k2) dA[k2] = pf[k2] - pc[k2];
    payoff(p, xf[1], pf); payoff(p, xc[1], pc);
    for (int k2 = 0; k2 < HQ_NK; ++k2) dB[k2] = pf[k2] - pc[k2];
}

// moments of (pair-average) quantities, long double, additive over threads
struct DMom {
    long double s1[HQ_NK] = {0, 0, 0}, sp[HQ_NK] = {0, 0, 0};
    uint64_t pairs = 0;
    void add(const double A[HQ_NK], const double B[HQ_NK]) {
        for (int k = 0; k < HQ_NK; ++k) {
            long double s = (long double)A[k] + B[k];
            s1[k] += s; sp[k] += s * s;
        }
        ++pairs;
    }
    void add(const DMom& o) {
        for (int k = 0; k < HQ_NK; ++k) { s1[k] += o.s1[k]; sp[k] += o.sp[k]; }
        pairs += o.pairs;
    }
};
inline hq::Price finalize_k(const Case& c, const DMom& m, int k) {
    long double np = (long double)m.pairs, disc = std::exp(-(long double)c.r * c.T);
    hq::Price p{};
    if (m.pairs == 0) return p;
    long double mean = m.s1[k] / (2 * np);
    long double vp = m.sp[k] / 4 / np - mean * mean;
    if (vp < 0) vp = 0;
    p.mean_payoff = (double)mean;
    p.pv = (double)(disc * mean);
    p.price = p.pv + (((c.puts >> k) & 1u) ? (double)(c.S0 - c.K[k] * disc) : 0.0);   // call (parity for puts)
    p.se = (double)(disc * std::sqrt(vp / (np > 1 ? np - 1 : 1)));
    return p;
}

}  // namespace hqd

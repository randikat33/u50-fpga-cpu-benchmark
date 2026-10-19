// ha_double.hpp - double-precision Asian (Case A): the same pair streams and integer normals as ha_model.hpp,
// double arithmetic and library exp/log. Used for the reference price (protocol v0.5 Section 4b) and for the
// float-minus-double check (--impl diff).
#pragma once
#include "ha_model.hpp"
#include <algorithm>
#include "hq_double.hpp"

namespace had {

struct DPar { double x0, mu, sd, K[HQ_NK]; uint32_t M; };
inline DPar make_dpar(const ha::ACase& c, uint32_t M) {
    DPar p{};
    double h = c.T / M;
    p.x0 = std::log(c.S0);
    p.mu = (c.r - 0.5 * c.sigma * c.sigma) * h;
    p.sd = c.sigma * std::sqrt(h);
    for (int k = 0; k < HQ_NK; ++k) p.K[k] = c.K[k];
    p.M = M;
    return p;
}
// both sides of one pair: undiscounted payoffs y (arithmetic) and g (geometric) per strike
inline void sim_pair(const DPar& p, const mc::Keys& k, uint32_t a, double yA[HQ_NK], double yB[HQ_NK],
                     double gA[HQ_NK], double gB[HQ_NK]) {
    mc::Xo s = mc::pair_seed(k, a);
    double xa = p.x0, xb = p.x0, sa = 0, sb = 0, la = 0, lb = 0;
    for (uint32_t t = 0; t < p.M; ++t) {
        double z = hqd::zn(mc::xo_next(s));
        xa += p.mu + p.sd * z;
        xb += p.mu - p.sd * z;
        sa += std::exp(xa); sb += std::exp(xb); la += xa; lb += xb;
    }
    double Aa = sa / p.M, Ab = sb / p.M, Ga = std::exp(la / p.M), Gb = std::exp(lb / p.M);
    for (int j = 0; j < HQ_NK; ++j) {
        yA[j] = std::max(Aa - p.K[j], 0.0); yB[j] = std::max(Ab - p.K[j], 0.0);
        gA[j] = std::max(Ga - p.K[j], 0.0); gB[j] = std::max(Gb - p.K[j], 0.0);
    }
}

struct DAMom {
    long double sy[HQ_NK] = {0, 0, 0}, syy[HQ_NK] = {0, 0, 0}, sg[HQ_NK] = {0, 0, 0}, sgg[HQ_NK] = {0, 0, 0},
                syg[HQ_NK] = {0, 0, 0};
    uint64_t pairs = 0;
    void add(const double yA[HQ_NK], const double yB[HQ_NK], const double gA[HQ_NK], const double gB[HQ_NK]) {
        for (int k = 0; k < HQ_NK; ++k) {
            long double ys = (long double)yA[k] + yB[k], gs = (long double)gA[k] + gB[k];
            sy[k] += ys; syy[k] += ys * ys; sg[k] += gs; sgg[k] += gs * gs; syg[k] += ys * gs;
        }
        ++pairs;
    }
    void add(const DAMom& o) {
        for (int k = 0; k < HQ_NK; ++k) {
            sy[k] += o.sy[k]; syy[k] += o.syy[k]; sg[k] += o.sg[k]; sgg[k] += o.sgg[k]; syg[k] += o.syg[k];
        }
        pairs += o.pairs;
    }
};
inline ha::APrice finalize_k(const ha::ACase& c, const DAMom& m, int k, double beta) {
    ha::APrice p{};
    if (m.pairs == 0) return p;
    long double np = (long double)m.pairs, disc = std::exp(-(long double)c.r * c.T);
    long double ym = m.sy[k] / (2 * np), gm = m.sg[k] / (2 * np);
    long double vyy = m.syy[k] / 4 / np - ym * ym, vgg = m.sgg[k] / 4 / np - gm * gm, vyg = m.syg[k] / 4 / np - ym * gm;
    if (vyy < 0) vyy = 0;
    if (vgg < 0) vgg = 0;
    long double dn = np > 1 ? np - 1 : 1;
    p.geo_cf = ha::geo_call(c, c.K[k]);
    p.geo_mc = (double)(disc * gm);
    p.plain = (double)(disc * ym);
    p.se_plain = (double)(disc * std::sqrt(vyy / dn));
    p.beta = beta;
    p.beta_hat = vgg > 0 ? (double)(vyg / vgg) : 0.0;
    long double vcv = vyy - 2 * beta * vyg + (long double)beta * beta * vgg;
    if (vcv < 0) vcv = 0;
    p.cv = (double)(disc * ym - beta * (disc * gm - p.geo_cf));
    p.se_cv = (double)(disc * std::sqrt(vcv / dn));
    p.var_ratio = vcv > 0 ? (double)(vyy / vcv) : 0.0;
    return p;
}

}  // namespace had

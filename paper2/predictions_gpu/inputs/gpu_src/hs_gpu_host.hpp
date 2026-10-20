// hs_gpu_host.hpp - host side of the GPU-native code (shared by hs_gpu.cu and host_emul_test.cpp):
// the six cases (protocol Sections 4 and 4b), the per-step parameters, and the estimators from the sums.
#pragma once
#include <cmath>
#include <cstdio>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "hs_gpu_core.cuh"

struct CaseDef {
    std::string name;
    int kind;
    double S0, T, v0, kappa, theta, eps, rho, r;   // Heston (S0: single asset; basket: S0_j = 100 + 0.1 j)
    double sigma;                                  // Asian
    double K[HS_NK], ref[HS_NK];
    unsigned puts;
    int M_fixed;                                   // Asian: 250 dates
};

inline CaseDef get_case(const std::string& n) {
    if (n == "I")   return {"I",   K_HESTON, 100, 10, 0.04, 0.5, 0.04, 1.0, -0.9, 0.0,  0, {70, 100, 140}, {35.849770, 13.084670, 0.295774}, 1u, 0};
    if (n == "II")  return {"II",  K_HESTON, 100, 15, 0.04, 0.3, 0.04, 0.9, -0.5, 0.0,  0, {70, 100, 140}, {37.169665, 16.649223, 5.138190}, 7u, 0};
    if (n == "III") return {"III", K_HESTON, 100, 5,  0.09, 1.0, 0.09, 1.0, -0.3, 0.0,  0, {70, 100, 140}, {38.772044, 21.795288, 9.983068}, 7u, 0};
    if (n == "P1")  return {"P1",  K_HESTON, 100, 1,  0.04, 1.5, 0.04, 0.3, -0.6, 0.01, 0, {70, 100, 140}, {31.260968, 8.095646, 0.141170}, 1u, 0};
    if (n == "B")   return {"B",   K_BASKET, 100.35, 1, 0.04, 1.5, 0.04, 0.3, -0.6, 0.01, 0, {90, 100, 110}, {11.39530252, 3.43639142, 0.32110944}, 3u, 0};
    if (n == "A")   return {"A",   K_ASIAN,  100, 1,  0, 0, 0, 0, 0, 0.05, 0.2, {90, 100, 110}, {12.61135515, 5.78194327, 2.00405513}, 0u, 250};
    throw std::runtime_error("unknown case '" + n + "' (I, II, III, P1, B, A)");
}
inline const double BETA_A[HS_NK] = {1.042439138, 1.032183413, 1.058812805};   // results/beta_A.csv (calibration)

inline Job make_job(const CaseDef& c, int scheme, int M, int cv, unsigned long long a0, unsigned long long n,
                    unsigned long long seed) {
    Job P{};
    P.kind = c.kind; P.scheme = scheme; P.M = M; P.cv = cv;
    P.a0 = a0; P.n = n; P.seed = seed;
    for (int k = 0; k < HS_NK; ++k) P.K[k] = (float)c.K[k];
    P.puts = c.puts;
    const double dt = c.T / M;
    if (c.kind == K_ASIAN) {
        P.S0[0] = (float)c.S0;
        P.mu = (float)((c.r - 0.5 * c.sigma * c.sigma) * dt);
        P.sd = (float)(c.sigma * std::sqrt(dt));
        return P;
    }
    if (c.kind == K_BASKET) for (int j = 0; j < HS_NA; ++j) P.S0[j] = (float)(100.0 + 0.1 * j);
    else P.S0[0] = (float)c.S0;
    const double E = std::exp(-c.kappa * dt), a = c.kappa * c.rho / c.eps - 0.5;
    P.v0 = (float)c.v0;
    P.hdt = (float)(0.5 * dt); P.sdt = (float)std::sqrt(dt);
    P.kdt = (float)(c.kappa * dt); P.kthdt = (float)(c.kappa * c.theta * dt);
    P.er = (float)(c.eps * c.rho); P.es = (float)(c.eps * std::sqrt(1.0 - c.rho * c.rho));
    P.th = (float)c.theta; P.E = (float)E;
    P.QC1 = (float)(c.eps * c.eps * E * (1.0 - E) / c.kappa);
    P.QC2 = (float)(c.theta * c.eps * c.eps * (1.0 - E) * (1.0 - E) / (2.0 * c.kappa));
    P.Q0 = (float)(-c.rho * c.kappa * c.theta * dt / c.eps);
    P.Q1 = (float)(0.5 * dt * a - c.rho / c.eps);
    P.Q2 = (float)(0.5 * dt * a + c.rho / c.eps);
    P.Q3 = (float)(0.5 * dt * (1.0 - c.rho * c.rho));
    P.Q4 = P.Q3;
    P.rT = (float)(c.r * c.T);
    return P;
}

inline double ncdf(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }
inline double geo_call(const CaseDef& c, int M, double K) {    // Kemna-Vorst, discrete dates j T / M, j = 1..M
    const double h = c.T / M;
    const double mu = std::log(c.S0) + (c.r - 0.5 * c.sigma * c.sigma) * h * (M + 1) / 2.0;
    const double s2 = c.sigma * c.sigma * h * (M + 1) * (2.0 * M + 1) / (6.0 * M);
    const double d2 = (mu - std::log(K)) / std::sqrt(s2), d1 = d2 + std::sqrt(s2);
    return std::exp(-c.r * c.T) * (std::exp(mu + 0.5 * s2) * ncdf(d1) - K * ncdf(d2));
}

struct Price { double price, se, plain, se_plain, cv, se_cv, beta_hat; };
// sums over all pairs (acc[HS_NACC]) -> prices per strike (the CPU-native code's formulas)
inline std::vector<Price> finalize(const CaseDef& c, int M, int cv, const double* acc, double np, const double* beta) {
    std::vector<Price> out(HS_NK);
    const double disc = std::exp(-c.r * c.T), dn = np > 1 ? np - 1 : 1;
    for (int k = 0; k < HS_NK; ++k) {
        Price p{};
        const double ym = acc[k] / (2 * np);
        const double vyy = std::max(acc[3 + k] / 4 / np - ym * ym, 0.0);
        p.plain = disc * ym;
        p.se_plain = disc * std::sqrt(vyy / dn);
        if (c.kind == K_ASIAN) {
            p.price = p.plain; p.se = p.se_plain; p.cv = p.plain; p.se_cv = p.se_plain;
            if (cv) {
                const double gm = acc[6 + k] / (2 * np);
                const double vgg = std::max(acc[9 + k] / 4 / np - gm * gm, 0.0);
                const double vyg = acc[12 + k] / 4 / np - ym * gm;
                p.beta_hat = vgg > 0 ? vyg / vgg : 0.0;
                p.cv = p.plain - beta[k] * (disc * gm - geo_call(c, M, c.K[k]));
                p.se_cv = disc * std::sqrt(std::max(vyy - 2 * beta[k] * vyg + beta[k] * beta[k] * vgg, 0.0) / dn);
                p.price = p.cv; p.se = p.se_cv;
            }
        } else {
            const bool put = (c.puts >> k) & 1u;
            p.price = p.plain + (put ? c.S0 - c.K[k] * disc : 0.0);    // basket: c.S0 = B(0) = 100.35
            p.se = p.se_plain;
        }
        out[k] = p;
    }
    return out;
}

// --key value arguments
struct Args {
    std::map<std::string, std::string> m;
    Args(int argc, char** argv) {
        for (int i = 1; i < argc; ++i) {
            std::string a = argv[i];
            if (a.rfind("--", 0) != 0) throw std::runtime_error("bad argument " + a);
            a = a.substr(2);
            if (i + 1 < argc && std::string(argv[i + 1]).rfind("--", 0) != 0) m[a] = argv[++i];
            else m[a] = "1";
        }
    }
    bool has(const std::string& k) const { return m.count(k) > 0; }
    std::string str(const std::string& k, const std::string& d) const { return has(k) ? m.at(k) : d; }
    long long i64(const std::string& k, long long d) const { return has(k) ? std::stoll(m.at(k)) : d; }
    double f64(const std::string& k, double d) const { return has(k) ? std::stod(m.at(k)) : d; }
};

inline std::vector<double> parse_list(const std::string& s, int n, const double* dflt) {
    std::vector<double> v;
    if (s.empty()) { v.assign(dflt, dflt + n); return v; }
    std::stringstream ss(s);
    std::string t;
    while (std::getline(ss, t, ',')) v.push_back(std::stod(t));
    if ((int)v.size() == 1) v.assign(n, v[0]);
    if ((int)v.size() != n) throw std::runtime_error("--beta needs 1 or 3 values");
    return v;
}

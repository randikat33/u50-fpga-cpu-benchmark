// pf_gen.cpp - portfolio/market generator. Port of v1 gen_portfolio.cpp with a CLI.
// With the default options the output is byte-identical to the v1 generator (same RNG call order).
//   pf_gen --trades N --underlyings U --portfolio P --market M [--seed 12345]
//   pf_gen N U P M            (v1 positional form)
#include <cstdio>
#include <random>
#include <string>
#include <vector>
#include "bench_common.hpp"
#include "pf_types.hpp"

int main(int argc, char** argv) {
    bench::Args a(argc, argv);
    if (a.has("help")) {
        std::printf("pf_gen [--trades 1000000] [--underlyings 1024] [--portfolio portfolio.bin] "
                    "[--market market.bin] [--seed 12345]\n   or: pf_gen N U portfolio.bin market.bin (v1)\n");
        return 0;
    }
    const auto& p = a.pos();
    uint32_t n_trades = p.size() >= 1 ? (uint32_t)std::stoul(p[0]) : 1000000;
    uint32_t n_under = p.size() >= 2 ? (uint32_t)std::stoul(p[1]) : 1024;
    std::string out_port = p.size() >= 3 ? p[2] : "portfolio.bin";
    std::string out_mkt = p.size() >= 4 ? p[3] : "market.bin";
    n_trades = (uint32_t)a.i64("trades", n_trades);
    n_under = (uint32_t)a.i64("underlyings", n_under);
    out_port = a.str("portfolio", out_port);
    out_mkt = a.str("market", out_mkt);
    uint32_t seed = (uint32_t)a.i64("seed", 12345);
    if (n_under < 1 || n_under > MAX_UNDERLYINGS) { std::fprintf(stderr, "underlyings must be 1..%d\n", MAX_UNDERLYINGS); return 1; }

    std::mt19937 rng(seed);
    std::uniform_int_distribution<uint32_t> uid_dist(0, n_under - 1);
    std::uniform_real_distribution<float> T_dist(0.05f, 3.0f);
    std::uniform_real_distribution<float> mny_dist(0.8f, 1.2f);
    std::bernoulli_distribution call_dist(0.5);
    std::bernoulli_distribution asian_dist(0.2);
    std::uniform_real_distribution<float> notional_dist(0.5f, 5.0f);
    std::bernoulli_distribution long_dist(0.9);
    std::uniform_real_distribution<float> rho_dist(-0.9f, -0.1f);
    std::uniform_real_distribution<float> v0_dist(0.01f, 0.10f);
    std::uniform_real_distribution<float> kappa_dist(0.5f, 4.0f);
    std::uniform_real_distribution<float> theta_dist(0.01f, 0.10f);
    std::uniform_real_distribution<float> sigma_dist(0.2f, 1.0f);

    static Market m{};
    for (uint32_t u = 0; u < MAX_UNDERLYINGS; u++) {
        m.spot[u] = 50.0f + 0.05f * (float)u;
        m.rate[u] = 0.01f + 0.00001f * (float)u;
        m.divq[u] = 0.00f;
        for (int k = 0; k < MAX_FACTORS; k++) m.factor_w[u][k] = (k < 4) ? 0.15f : 0.0f;
    }
    std::vector<Trade> trades(n_trades);
    for (uint32_t i = 0; i < n_trades; i++) {
        Trade t{};
        t.underlying_id = uid_dist(rng);
        float S0 = m.spot[t.underlying_id];
        t.T = T_dist(rng);
        t.K = S0 * mny_dist(rng);
        t.option_type = call_dist(rng) ? 0 : 1;
        t.option_kind = asian_dist(rng) ? 1 : 0;
        t.notional = notional_dist(rng);
        t.position = long_dist(rng) ? 1.0f : -1.0f;
        t.v0 = v0_dist(rng);
        t.kappa = kappa_dist(rng);
        t.theta = theta_dist(rng);
        t.sigma = sigma_dist(rng);
        t.rho = rho_dist(rng);
        trades[i] = t;
    }
    bench::write_from(out_port, &n_trades, 4);
    bench::write_from(out_port, trades.data(), sizeof(Trade) * trades.size(), true);
    bench::write_from(out_mkt, &m, sizeof(Market));
    std::printf("Wrote %u trades to %s, market to %s\n", n_trades, out_port.c_str(), out_mkt.c_str());
    return 0;
}

---
title: "Study protocol, paper 2: energy to a target accuracy in Heston Monte Carlo on an FPGA card and its host"
subtitle: "Version 0.5 (the secondary cases specified; written after the main-case measurements, before any secondary-case code or card run), 15 October 2026. Author: R. T. P. G. Pathiranage (sole author)"
---

# 1. Summary

Paper 1 compared the Alveo U50 with its host on a fixed amount of work. In option pricing the user fixes an accuracy, not an amount of work, and the work needed to reach it depends on the simulation scheme. This study measures the **time and energy needed to reach a stated price error** for each combination of platform (U50, CPU algorithm-matched, CPU-native) and Heston scheme (Euler with full truncation, Andersen's QE), and tests whether paper 1's time–energy model, extended by an error model, predicts the cheapest combination before any card run.

The pilot in Section 9 already points to the main result to test: when the Feller condition is violated, Euler full truncation needs 12 to 52 times more path-steps than QE for a 1% error. That is larger than the card's energy advantage in paper 1 (3.8× against one socket). If this holds, **the scheme matters more than the platform**, and the card pays only when it runs the better scheme.

Working title: *Accuracy per joule: when does a PCIe FPGA card price Heston options with less energy than its host?*

# 2. What is already known

I checked the closest studies. Accuracy-aware FPGA Monte Carlo is not new, so the paper must state what it adds.

| Study | What it did | What it did not do |
|---|---|---|
| de Schryver et al. 2011 (ReConFig) | Energy-efficient FPGA Heston pricer; energy compared with an 8-core CPU and a GPU (as summarised in the 2013 paper) | Energy compared at equal throughput, not at a fixed total error |
| de Schryver, Torruella, Wehn 2013 (DATE) | Multilevel MC Heston on a Virtex-6, Euler with full truncation | Power estimated with XPower (chip only, 3.58 W); no CPU measured ("a fair comparison is not feasible"); no numeric accuracy target |
| Chow et al. 2012 (FPGA) | Mixed-precision MC on a Maxeler card, all platforms run to the same statistical tolerance; wall-socket power | Black–Scholes Asian (no time-step bias); one scheme; boundary not stated; no prediction |
| Haas and Giles 2025 (arXiv) | Nested multilevel MC with low-precision fixed point for FPGAs | No energy measurement mentioned in the abstract; FPGA and CPU not named there |
| Andersen 2008; Lord et al. 2010 | Bias of Heston schemes, standard test cases | CPU only; no energy |

**The gap this study fills:** no study measures energy *to a fixed total error* (discretisation bias plus statistical error) across Heston schemes on an FPGA card and its host, with stated energy boundaries, algorithm-matched and CPU-native baselines, and a prediction made before the measurement.

Multilevel Monte Carlo (Giles 2008) is the strongest method for reaching a fixed accuracy cheaply. It is out of scope for the measurements (coupling QE across levels is not straightforward), and the paper will say so and discuss it.

# 3. Research questions

- **RQ1.** For each test case and accuracy target, which combination of platform, scheme, time step and number of paths reaches the target with the least time, and which with the least energy?
- **RQ2.** Is the gain from choosing the better scheme larger than the gain from choosing the better platform?
- **RQ3.** Can the cost-optimal setting and the winner be predicted from CPU-only runs and the routed FPGA design, before any card run?

# 4. Test cases

All cases are European calls on one asset, $X(0)=100$, strikes $K = 70, 100, 140$. Cases I–III are Andersen's (2008) test cases; he uses undiscounted prices, so $r=0$. The fourth set uses paper 1's MC Heston parameters on one asset.

| Case | $T$ | $V(0)=\theta$ | $\kappa$ | $\varepsilon$ (vol of vol) | $\rho$ | $r$ | Feller ratio $2\kappa\theta/\varepsilon^2$ |
|---|---|---|---|---|---|---|---|
| I | 10 | 0.04 | 0.5 | 1.0 | −0.9 | 0 | 0.04 (violated) |
| II | 15 | 0.04 | 0.3 | 0.9 | −0.5 | 0 | 0.03 (violated) |
| III | 5 | 0.09 | 1.0 | 1.0 | −0.3 | 0 | 0.18 (violated) |
| P1 | 1 | 0.04 | 1.5 | 0.3 | −0.6 | 0.01 | 1.33 (satisfied) |

**Reference prices.** Computed with two independent formulas: Heston's (1993) two-probability form with the "little trap" characteristic function (Albrecher et al. 2007), which is paper 1's validation code, and Lewis's single-integral form. The two agree to better than $3\times10^{-9}$ in every row.

| Case | $K=70$ | $K=100$ | $K=140$ |
|---|---|---|---|
| I | 35.849770 | 13.084670 | 0.295774 |
| II | 37.169665 | 16.649223 | 5.138190 |
| III | 38.772044 | 21.795288 | 9.983068 |
| P1 | 31.260968 | 8.095646 | 0.141170 |

**Secondary cases (included, measured after the main cases):** paper 1's 8-asset basket, and the arithmetic Asian call under Black–Scholes with the geometric-Asian control variate (no time-step bias; tests variance reduction instead). They are specified in Section 4b (added in v0.5).

# 4b. Secondary cases (specified in v0.5)

v0.4 named the two secondary cases but did not fix their parameters, targets or hypotheses. This section fixes them before any code for them is written and before they run on any platform other than the pilot of Section 9c.

**Case B: paper 1's 8-asset basket.** Paper 1's market (its `v1_market`), unchanged:

| Parameter | Value |
|---|---|
| Assets | 8, independent (paper 1's Cholesky factor is the identity) |
| Start prices | 100.0, 100.1, …, 100.7 |
| Heston, each asset | V(0) = θ = 0.04, κ = 1.5, ε = 0.3, ρ = −0.6 (Feller ratio 1.33, satisfied) |
| Rate, maturity | r = 0.01, T = 1, no dividends |

- Payoff: European call on the arithmetic basket B, the average of the 8 prices at T; strikes K = 90, 100, 110. The basket's standard deviation at $T$ is about 7, so the main cases' strikes 70 and 140 would be almost certain or almost worthless.
- Payoff form per strike as in Section 7, with parity $C = P + B(0) - K e^{-rT}$, $B(0) = 100.35$.
- Schemes: FT and QE, applied to each asset as in Section 5; time steps are powers of two per year.
- Work is counted in basket path-steps (one basket path-step = 8 asset-steps), so the work ratio of the two schemes is comparable with the main cases; the rates are measured in the same unit.
- **Reference price** (no closed form): double-precision CPU runs of QE at $h=1/128$ and FT at $h=1/512$, each with a standard error of at most $\varepsilon_{\min}/10$ at every strike. The QE value is the reference; it is accepted only if the FT value agrees within 3 combined standard errors and halving the step (QE $1/64 \to 1/128$) changes the price by less than one standard error. If a check fails, both runs are repeated at half the step, and this is reported.

**Case A: arithmetic Asian call under Black–Scholes.**

| Parameter | Value |
|---|---|
| Start price, volatility | S(0) = 100, σ = 0.2 |
| Rate, maturity | r = 0.05, T = 1, no dividends |
| Monitoring | 250 equally spaced dates t~j~ = j/250 (daily) |
| Strikes | 90, 100, 110 |

- Payoff: max(A − K, 0), discounted, with A the average of the 250 prices S(t~j~).
- The log-price step is exact, $\ln S(t_{j+1}) = \ln S(t_j) + (r-\sigma^2/2)h + \sigma\sqrt{h}\,Z$, so there is **no time-step bias**: the time step is fixed by the contract and the only choices are the number of paths and the estimator.
- Two estimators, both from the same antithetic paths:
  - **plain:** the discounted payoff $Y$;
  - **control variate (CV):** $Y - \beta\,(G - \mathrm{E}[G])$, where $G$ is the discounted call on the geometric average of the same 250 prices and $\mathrm{E}[G]$ is its closed form for discrete monitoring (Kemna and Vorst 1990). $\beta$ is fixed per strike from the calibration and is not re-estimated from the measured run, so the estimator stays unbiased.
- The card and the matched CPU code compute $Y$ and $G$ on every path, so the two estimators cost the same per path-step on those platforms; the CPU-native code computes $G$ only for the CV estimator.
- **Reference price:** double-precision CPU run with the CV estimator, standard error at most $\varepsilon_{\min}/20$ at every strike, accepted if a plain-estimator run agrees within 3 standard errors.

**Targets and setting rule (both cases).** RMSE of 1%, 0.3% and 0.1% of the at-the-money ($K=100$) price, holding at all three strikes, as in Section 7. The settings are chosen by the empirical rule of Section 7 from a calibration of the float model, which is bit-identical to the card. In Case A the rule chooses only the number of pairs ($N = 2\sigma^2/\varepsilon^2$). This leaves half the error budget for a bias that should be negligible (float rounding only), so it is conservative; it is kept so that one rule applies to every case. The calibration measures the float model's bias against the reference, as for the main cases.

**Platforms, outcomes and boundaries:** as for the main cases (Sections 6, 7 and 10): the card, the CPU algorithm-matched code and the CPU-native code; one socket and the whole server; the primary outcome (energy per pricing run) and the secondary outcome (steady state).

# 5. Schemes

| Scheme | Variance step | Random numbers per step | Where it is used now |
|---|---|---|---|
| Euler, full truncation (FT) | *V*⁺ = max(*V*, 0) in drift and diffusion (Lord et al. 2010) | 2 normals | new (paper 1's MC Heston kernel truncates the stored variance, which Lord et al. call absorption) |
| Andersen QE | quadratic branch *V* = *a*(*b* + *Z*~V~)² if *ψ* ≤ 1.5; exponential branch otherwise; log-price by central discretisation (*γ*~1~ = *γ*~2~ = ½) | 1 uniform (gives *Z*~V~ by inverse CDF) + 1 normal | new |
| Euler, reflection | absolute value \|*V*\| | 2 normals | paper 1's portfolio kernel; shown in the pilot only |

QE is used without Andersen's martingale correction; whether its bias is small enough is part of the result (Section 9). Both measured schemes need two random numbers per step, so the generator and inverse-CDF table of paper 1 can be reused.

Time steps are powers of two per year: $h = 1, \tfrac12, \dots, \tfrac{1}{4096}$.

# 6. Platforms and baselines

As in paper 1, on frodo (2× Xeon Gold 5318Y) with the U50:

- **U50**: one kernel per scheme, single asset, float32, bit-identical to the matched CPU code.
- **CPU algorithm-matched**: AVX-512, same generator, inverse-CDF table, exp/log tables and float32 arithmetic; bit-identical output.
- **CPU-native**: what a CPU programmer would write (Box–Muller or a vendor generator, FMA, library `exp`/`log`), checked to agree within 3 SE.
- Boundaries: one socket (24 cores), whole server, wall check — as in paper 1.

# 7. Accuracy targets and error model

**Targets.** Root-mean-square error $\varepsilon$ of 1%, 0.3% and 0.1% of the at-the-money price of the case. The target must hold for all three strikes, which are priced from the same paths.

**Payoff form per strike (added in v0.4).** Each call price is estimated either from the call payoff, or from the put payoff and put-call parity, $C = P + S_0 - K e^{-rT}$, which is exact. For each case and strike the form with the smaller variance is used, among the forms whose variance is finite. Reason: in Case II the call payoff has **infinite variance**. The Heston model's moment E[S(T)^ω^] becomes infinite after a finite time T\* (Andersen and Piterbarg 2007); for Case II, T\* for ω = 2 is 13.2 years, shorter than the maturity of 15 years. The CPU-native check on the v0.3 settings found such paths (one payoff of 3.7 million in $1.1\times10^{8}$ pairs), so no number of paths guarantees an RMSE target with the call payoff. A put payoff is bounded by $K$.

| Case | T\* (ω = 2), years | T\* (ω = 3), years | Form at K = 70, 100, 140 | SD of a pair average, call / put (pilot) |
|---|---|---|---|---|
| I | ∞ | ∞ | put, call, call | 15.4 / 11.1, 9.2 / 17.7, 1.8 / 23.1 |
| II | 13.2 | 3.3 | put, put, put | call variance infinite; put 12, 19, 25 |
| III | ∞ | 3.2 | put, put, put | 40 / 11, 36 / 18, 31 / 24 |
| P1 | ∞ | ∞ | put, call, call | 5.6 / 2.0, 5.6 / 6.3, 1.1 / 5.9 |

The put payoff is computed as −(S − K), an exact negation, so the card and the matched CPU code stay bit-identical. The form is the same for every platform and scheme.

**Error model.** For time step $h$ and $N$ paths, with bias $b(h) \approx c\,h^{p}$ and payoff variance $\sigma^2$ per path:

$$\text{MSE} = b(h)^2 + \frac{\sigma^2}{N}.$$

The work is $W = N\,T/h$ path-steps. Minimising $W$ subject to $\text{MSE} \le \varepsilon^2$ gives the classical split (Duffie and Glynn 1995):

$$b^2 = \frac{\varepsilon^2}{1+2p}, \qquad h_{\text{opt}} = \left(\frac{\varepsilon}{c\sqrt{1+2p}}\right)^{1/p}, \qquad N_{\text{opt}} = \frac{(1+2p)\,\sigma^2}{2p\,\varepsilon^2}.$$

**Rule used for the settings (changed in v0.3).** For both schemes the setting is chosen by the empirical rule: the coarsest grid step whose measured absolute bias plus twice its standard error is at most $\varepsilon/\sqrt{2}$ at all three strikes, with $N = 2\sigma^2/\varepsilon^2$ pairs ($\sigma^2$: variance of one antithetic pair average at that step, largest over the strikes), rounded up to whole work units.

The formulas above (the "model rule": $h_{\text{opt}}$ rounded down to the grid, $N_{\text{opt}}$) are reported for comparison only. In v0.2 they were the rule for FT. The calibration showed that extrapolating the fitted power law underestimates the bias at fine steps: the model rule missed the target in 3 of 12 FT settings (by up to 6%) and met it with no margin in a fourth (0.998 of the target), while the empirical rule met it in all 24 settings. The change was made before any kernel was written or any card run.

**Cost model.** $h_{\text{opt}}$ and $N_{\text{opt}}$ do not depend on the platform; the platform enters only through the time per path-step $\tau$ and the power $P$. For platform $a$ and scheme $s$: time $t = W_s\,\tau_{a,s} + t_0$ and energy $E = t\,P_a$, with $\tau$, $t_0$ and $P$ from paper 1's model (routed design and Vivado power estimate for the card; CPU-only runs for the CPU). The energy ratio of two settings is therefore

$$\frac{E_{a,s}}{E_{b,s'}} \approx \frac{W_s}{W_{s'}} \cdot \frac{\tau_{a,s}\,P_a}{\tau_{b,s'}\,P_b},$$

the product of an **algorithm factor** and a **platform factor**. RQ2 compares the two factors directly.

**Fixed start-up cost and the secondary outcome (added in v0.4).** Many settings are very small: in Case P1 at 1% the work is $1.9\times10^{4}$ (QE) to $1.7\times10^{5}$ (FT) path-steps, a few microseconds on the card, against a fixed start-up cost $t_0$ of about 0.7 ms per card run in paper 1. In such settings the energy to target mostly measures $t_0$, not the scheme or the platform. In practice many options are priced together, which shares $t_0$ among them. Two outcomes are therefore reported:

- **Primary (unchanged):** energy to target of one pricing run, $E = (W_s\,\tau_{a,s} + t_0)\,P_a$, including the start-up cost.
- **Secondary (steady state):** energy to target without the start-up cost, $E_{\text{ss}} = W_s\,\tau_{a,s}\,P_a = W_s\,P_a / R_{a,s}$, where $R_{a,s}$ and $P_a$ are the rate and power of a long run (at least 10 s) of the same scheme at the same time step. This is the cost per option when many options are priced in one batch.

Both are predicted in the prediction file (phase 4) before any card run. The hypotheses of Section 8 are tested on the primary outcome, as registered in v0.3; for each of them the paper also states whether it holds in the steady state.

# 8. Hypotheses (fixed in v0.3; numbers updated in v0.4 from the second calibration, before any card run)

The numbers come from the CPU calibration (Section 9b, v0.4). In v0.4 they were recomputed from the new work ratios by the same rule as in v0.3: the work ratio divided by the CPU cost ratio per path-step (1.67, unchanged: the payoff form does not change the cost per step) is compared with the card's energy advantage per path-step in paper 1 (3.8×). No property of the new card designs was used. The platform numbers ($\tau$, $P$ of the new kernels) do not exist yet; the quantitative prediction of every comparison is made from the routed designs and registered in the prediction file (phase 4) before any card run.

- **H1 (scheme).** In cases I–III, FT needs more path-steps than QE at every target: at least 8× at 1% and at least 30× at 0.3% and 0.1% (calibration v0.4: 8.6× to 132×; v0.3: 8.5× to 132×).
- **H2 (cost per step).** QE costs more per path-step than FT on every platform, by a factor between 1.2 and 3 (CPU, scalar float model: 1.67).
- **H3 (main).** In cases I–III, at every target, the CPU socket running QE (algorithm-matched code) needs less energy than the card running FT: the algorithm factor exceeds the platform factor. The narrowest case is Case III at 1% (work ratio 8.6, about 5 after the per-step cost, against the card's 3.8× energy advantage per path-step in paper 1).
- **H4 (Feller satisfied: a crossover).** In case P1 the work ratio is 8.7× at 1%, 4.3× at 0.3% and 8.2× at 0.1% (about 5.2, 2.6 and 4.9 after the per-step cost). The card running FT therefore needs less energy than the CPU running QE at 0.3%, and more at 1% and 0.1%: the ranking changes with the target. (v0.3, from the call calibration: 4.7×, 4.3× and 16×, so the card was expected to win at 1% and 0.3%. QE now reaches 1% with one step per year, see Section 9b.)
- **H5 (model).** The model of Section 7 predicts the energy to target within 20% and the winner of every pairwise comparison (platform × scheme) correctly, and the measured RMSE of every predicted setting is within its target (allowing 10% for the uncertainty of the bias estimate).
- **H6 (reflection, shown, not measured on the card).** The reflection scheme of paper 1's portfolio cannot reach the 1% target in cases I–III at any affordable step (pilot bias at $h=1/32$: 25.8 in Case I, against a 1% target of 0.13).

# 8b. Hypotheses for the secondary cases (added in v0.5, before any secondary-case code or card run)

These were fixed after the main cases had been measured. What was known from the main cases, and used here, is stated with each hypothesis; nothing about the secondary cases was known except the pilot of Section 9c.

- **HB1 (scheme, Feller satisfied).** In Case B, FT needs more path-steps than QE at every target, by a factor between 2 and 20 (pilot, Section 9c: about 8 at every target).
- **HB2 (algorithm factor smaller than in I–III).** In Case B the algorithm factor of Section 7 (FT/QE, best platform for each scheme, steady state, socket) is between 1 and 10 at every target: QE stays the cheaper scheme, but by far less than in Cases I–III (measured there: 3.8 to 64.7). Basis: the pilot's work ratio of about 8, divided by the measured cost ratio QE/FT per path-step (2.1 on the card, 3.7–4.4 on the CPU).
- **HA1 (variance reduction).** In Case A the control variate cuts the work to target by at least 100× at every target (pilot: 407×; the factor is the same at every target because there is no bias).
- **HA2 (algorithm against platform, Case A).** In Case A the algorithm factor (plain/CV, best platform for each estimator) is larger than the platform factor (best CPU / card, same estimator) at every target, in both outcomes. In particular, the CPU socket running the CV estimator needs less energy than the card running the plain estimator.
- **HA3 (an exp-heavy step favours the card).** In the steady state (socket boundary), the card needs less energy than the best CPU code for Case A with the CV estimator at every target. Basis: the Asian step evaluates an exponential at every step, as QE evaluates a logarithm, and in the main cases the card's steady-state advantage was 1.8–2.0× for QE but only 1.1× for FT.
- **H5 (model), repeated.** H5 of Section 8 is tested again on the secondary cases with the same wording and thresholds, and reported separately from the main cases.

**Two predictions for the secondary cases.** Both are written to the prediction file and registered before any secondary-case card run:

1. **Same method as the main cases:** routed design, Vivado power estimate, CPU-only runs, $t_0$ = 0.7 ms from paper 1. H5 is tested on this one.
2. **Informed:** the same, but with the start-up cost $t_0$ and the card's power model (the ratio of measured to Vivado-estimated power) taken from the main-case measurements. The paper reports how much this improves the prediction. Reason: the main cases showed that paper 1's $t_0$ overstates the start-up cost (about 0.2 ms measured) and that the Vivado estimate overstates the card's power by 15–25%.

# 9. Pilot results (done, CPU, double precision, 0.4–4 million paths)

Bias at $K=100$ against the reference price. The standard error (SE) of the FT and QE values is 0.006–0.05 in cases I and P1 and 0.02–0.13 in cases II and III (reflection: 0.1–1.9). Values smaller than about twice their SE are not resolved.

| Case | Scheme | $h=1$ | $1/4$ | $1/16$ | $1/32$ | $1/128$ |
|---|---|---|---|---|---|---|
| I | reflection | 47.9 | 37.5 | 29.4 | 25.8 | – |
| I | FT | 6.34 | 2.05 | 0.558 | 0.208 | 0.057 |
| I | QE | 0.978 | 0.058 | −0.013 | −0.013 | – |
| II | FT | 7.06 | 2.28 | 0.716 | 0.417 | – |
| II | QE | −0.369 | −0.008 | −0.020 | −0.007 | – |
| III | FT | 4.15 | 1.06 | 0.309 | 0.087 | −0.047 |
| III | QE | −0.284 | −0.053 | −0.022 | −0.042 | – |
| P1 | FT | 0.343 | 0.109 | 0.011 | 0.007 | – |
| P1 | QE | −0.011 | 0.001 | 0.000 | 0.003 | – |

What the pilot shows:

- FT bias is first order in cases I–III (fitted $p$ = 0.82–1.00, $c$ = 3.8–8.2). For the 1% target FT needs 64 to 256 steps per year; QE needs 4.
- Path-steps needed at 1%: FT/QE = 24 (Case I), 52 (II), 12 (III); at 0.3% in Case I, 48.
- Case I QE shows a small negative bias of about −0.013 at $h=1/16$ (2 SE; also −0.013 at $1/32$, with a larger SE). That is 0.1% of the price, the size of the tightest target. Phase 2 must resolve it; if it is real, QE without the martingale correction may not reach 0.1% in Case I, which is itself a result worth reporting.
- With the Feller condition satisfied (P1), both schemes are accurate at a few steps per year; paper 1 used 128.
- Reflection is unusable when the Feller condition fails, which supports what paper 1 already states about its portfolio kernel.

# 9b. Calibration results

**Second calibration (v0.4, payoff forms of Section 7, done 14 October 2026).** The float model (bit-identical to the card) was run on frodo's CPU for 4 cases × 2 schemes × 7 (QE) or 13 (FT) step sizes, until each strike's bias was known to $\varepsilon_{\min}/5$ or resolved to 10% (96 jobs, new seeds, up to 63 million antithetic pairs per job, no payoff overflow).

**FT bias fit**, $|b| = c\,h^p$ at $K=100$: Case I $c=8.46$, $p=1.016\pm0.009$; Case II $c=9.96$, $p=1.027\pm0.011$; Case III $c=5.11$, $p=1.13\pm0.03$; P1 $c=1.20$, $p=1.67\pm0.09$ (three resolved points). FT is first order when the Feller condition fails, as in v0.3.

**Work to target, FT/QE (path-steps, empirical rule; v0.3 in brackets):**

| Case | 1% | 0.3% | 0.1% |
|---|---|---|---|
| I | 32.3 (32.9) | 64.2 (65.0) | 64.0 (129) |
| II | 33.0 (55.4) | 132 (126) | 129 (119) |
| III | 8.6 (8.5) | 34.3 (33.3) | 66.8 (132) |
| P1 | 8.7 (4.7) | 4.3 (4.3) | 8.2 (16.3) |

All 24 settings meet their target (predicted RMSE 0.71–0.87 of $\varepsilon$). The number of pairs fell to 0.36–0.53 of v0.3 in cases I–III and stayed within 4% in P1. The largest setting is FT in Case II at 0.1%: 2,048 steps per year, 4.6 million pairs, $2.8\times10^{11}$ path-steps.

**Five settings moved to another time step.** Two reflect a real effect: QE is not exactly a martingale, so its put form and call form differ in bias by $E[S_T]e^{-rT} - S_0$. In Case II at 2 steps per year this is about +0.012 at all three strikes, which moves QE at 1% from 2 to 4 steps per year; in P1 at 1 step per year the put at $K=70$ has a smaller bias than the call (−0.047 against −0.070), which moves QE at 1% from 2 to 1 step per year. The other three were within about two standard errors of the rule's limit in one of the two calibrations (QE in Case I at 0.1%: 16 → 32; FT in Case III at 0.1%: 1024 → 512; FT in P1 at 0.1%: 128 → 64). FT is exactly a martingale, so its bias does not depend on the form.

**Float32 against double precision on the same paths:** QE differs by at most $3\times10^{-5}$; P1 by $2\times10^{-5}$ for both schemes. FT in cases I–III differs path by path, with mean differences consistent with zero within 2 SE. As in v0.3, the calibration measures the float model's own bias, which is the card's.

**First calibration (v0.3, call payoffs, 11 October 2026).** Superseded by the second; its data are kept and published. It gave the same FT bias fit and the work ratios in brackets above, and its Case II settings rested on a finite-sample estimate of an infinite variance.

# 9c. Pilot of the secondary cases (v0.5; CPU, double precision, numpy; not on frodo, not on the card)

Scripts `pilot4_basket.py` and `pilot5_asian.py`, published with the protocol.

**Case B** (4 million antithetic pairs per step size; bias against QE at $h=1/64$; SE about 0.001 at $K=100$, 0.0004 at $K=110$). Lower-variance forms: put at $K=90$ and $K=100$ (standard deviation of a pair average 0.61 and 2.04), call at $K=110$ (0.89). Price at $K=100$: 3.4372 (QE, $h=1/64$); FT at $h=1/64$ agrees within one SE.

| Scheme | Strike (form) | $h=1$ | $1/2$ | $1/4$ | $1/8$ | $1/16$ | $1/32$ |
|---|---|---|---|---|---|---|---|
| FT | 100 (put) | +0.120 | +0.110 | +0.037 | +0.0070 | +0.0013 | +0.0006 |
| FT | 110 (call) | +0.144 | +0.086 | +0.027 | +0.0072 | +0.0030 | +0.0010 |
| QE | 100 (put) | +0.0064 | +0.0048 | −0.0015 | −0.0022 | – | – |
| QE | 110 (call) | +0.0041 | +0.0034 | +0.0010 | +0.0002 | – | – |

With the targets of Section 4b (bias limit $\varepsilon/\sqrt2$ = 0.024, 0.0073 and 0.0024), FT needs about 8, 16 and 32 steps per year and QE about 1, 2 and 4, with nearly equal variance: a work ratio of about 8 at every target (4 to 16 within the pilot's standard errors). The largest setting is small: about $7.5\times10^{5}$ pairs at 0.1%. The FT bias decays faster than first order, as in P1.

**Case A** (200,000 antithetic pairs, 250 dates):

| Strike | Plain price | CV price | SD of a pair average, plain | SD, CV | Variance ratio | $\beta$ |
|---|---|---|---|---|---|---|
| 90 | 12.606 | 12.611 | 2.56 | 0.194 | 174 | 1.042 |
| 100 | 5.771 | 5.782 | 3.92 | 0.162 | 582 | 1.032 |
| 110 | 1.997 | 2.004 | 3.15 | 0.139 | 516 | 1.059 |

Because the target must hold at all three strikes, the work is set by the largest variance: $K=100$ for the plain estimator, $K=90$ for CV. The work ratio is $(3.92/0.194)^2 \approx 407$ at every target. At 0.1% the plain estimator needs about $9\times10^{5}$ pairs ($4.6\times10^{8}$ path-steps) and CV about 2,300 pairs ($1.1\times10^{6}$ path-steps). Pricing runs with CV are therefore very short, so their primary outcome will mostly measure the start-up cost; the steady-state outcome shows the cost when many options are priced together.

# 10. Measurement plan

1. **Calibration (phase 2, CPU only; done, Section 9b).** The matched float32 CPU code, which is bit-identical to the card, measures bias and $\sigma$ at each grid step with $\text{SE}_b \le \varepsilon_{\min}/5$. For FT, the bias at the finest steps is taken from the fitted power law and checked at one step; the change in bias between $h$ and $h/2$ is measured on coupled paths (the same Brownian increments, as in multilevel MC), which needs far fewer paths. Because the output is bit-identical, the bias it measures is the card's bias, so the prediction stays "before any card run". The double-precision code measures the float32 rounding error separately. The CPU-native code is calibrated at the chosen steps only.
2. **Prediction (phase 4).** For each case, target, scheme and platform: $h_{\text{opt}}$, $N_{\text{opt}}$, predicted time and energy, and the predicted winner. The file is hashed and registered before the card is used, as in paper 1.
3. **Runs (phase 5).** Each predicted setting runs 5 times in each of 2 sessions, with the steady-clock protocol and power logging of paper 1 (card rails, RAPL per socket, wall check). The seeds differ between repeats. A run shorter than 10 s is repeated back to back within one measurement window of at least 10 s, and its time and energy are the window's divided by the number of runs (each repetition is a complete pricing run, including its start-up). For the steady-state outcome, each platform, scheme and time step used in a setting also runs once per repeat with enough pairs to last at least 10 s.
4. **Accuracy check.** For every run: price error against the reference for each strike; RMSE from the bias (calibration) and the run's SE.
5. **Secondary cases (added in v0.5).** The same steps in the same order, after the main cases: reference prices (Section 4b); calibration of the float model on the CPU (bias, $\sigma$, and $\beta$ for Case A) with new seeds; CPU-native check; card designs built and routed; CPU-only measurements; both predictions of Section 8b hashed and registered; a card check (card equal to the matched CPU code, bit for bit); then 5 runs in each of 2 sessions per setting, with the steady-clock protocol, power logging and the 10 s rule above.

# 11. Analysis plan

- Primary outcome: energy to target, per case, target, platform and scheme, with bootstrap 95% confidence intervals over repeats.
- Secondary: energy to target in the steady state (Section 7); time to target; the algorithm factor and the platform factor of Section 7.
- Figures: error against energy (one panel per case, one line per platform × scheme); a bar chart of the two factors.
- Prediction test: verdicts correct, relative errors of time and energy, all reported, including failures.

# 12. FPGA kernel notes

- Two kernels (FT, QE) derived from paper 1's MC Heston kernel: interleaved path slots per lane, xoshiro128** streams, segmented inverse-CDF table, exact 128-bit payoff accumulation.
- QE needs $\ln$ for the exponential branch and $\sqrt{\ }$ for the diffusion; to keep bit identity with the CPU, $\ln$ will use a table method as $\exp$ did in paper 1. Each lane evaluates both branches and selects one, so the pipeline keeps one step per cycle.
- If QE needs fewer lanes or a lower clock, that is part of the platform factor and is reported, not avoided.
- **Case B (v0.5).** Paper 1's assets are independent, so each lane simulates the 8 assets of a basket path in 8 consecutive path slots of the FT or QE pipeline, each asset with its own random stream and start value. A new payoff stage adds the 8 terminal prices and evaluates the three strikes. The step datapaths are those of the main kernels; the slot count, payoff stage and accumulation change, so the designs are built and routed again. A correlated basket would need a mixing stage across the slots; paper 1's market does not.
- **Case A (v0.5).** Derived from the FT kernel with $\varepsilon = 0$: the variance stays at $\sigma^2$, so the log-price step is exact. Added per step: a table exponential of the log-price, an arithmetic-sum accumulator and a log-sum accumulator. At maturity: arithmetic and geometric payoffs for the three strikes, with exact integer accumulation of $\sum Y$, $\sum G$, $\sum Y^2$, $\sum G^2$ and $\sum YG$ per strike; $\beta$ is applied on the host. The card stays bit-identical to the matched CPU code.

# 13. Registration and openness

- As in paper 1: this protocol and, later, the prediction file are committed to the public GitHub repository and their SHA-256 hashes recorded before the work they govern (the protocol before any kernel is measured; the prediction file before any card run).
- Code, calibration data, predictions, logs and results go to GitHub and a new Zenodo record, as for paper 1.

# 14. Risks

| Risk | What we do |
|---|---|
| QE does not fit the card at the paper-1 clock or lane count | Report the reduced design; the model includes it |
| QE bias in Case I is larger than the 0.1% target | Report it; add the martingale-corrected variant only if the CPU calibration shows it is needed, and say so before the card runs |
| Very long FT runs at 0.1% (up to $6\times10^{11}$ path-steps in Case II) | About 4 to 10 minutes per run if the single-asset kernels reach paper 1's path-step rate; drop Case II at 0.1% if they do not |
| Reviewers ask for multilevel MC | Discuss it as the main alternative and as future work |
| Finance reviewers question the test cases | They are Andersen's published cases; the reference prices are checked two ways |
| Secondary-case runs are very short (Case A with CV: about $10^{6}$ path-steps at 0.1%) | Report the primary outcome as measured (start-up cost dominates) and the steady-state outcome alongside; no target is changed |
| The basket or Asian design does not meet timing at 225 MHz | Use the build script's lower-clock fallback, as for the main kernels; report the clock |
| A payoff with infinite variance (Case II calls) | Found by the CPU-native check before any card run; each strike uses its finite-variance, lower-variance payoff form (Section 7, v0.4) |

# 15. Plan and roles

| # | Phase | Claude | You | Server time | Weeks |
|---|---|---|---|---|---|
| 1 | Protocol | this document | read, agree, register on OSF | – | 1 |
| 2 | Calibration | CPU code (matched, native, double), scripts | run on frodo when idle | a few hours | 2–3 |
| 3 | Kernels | FT and QE kernels, C-simulation tests | build bitstreams, run checks | 1–3 builds of 8–20 h | 3–8 |
| 4 | Prediction | model, prediction file | register it | – | 9 |
| 5 | Measurement | queue scripts | start the queue | 2–4 days | 10–11 |
| 5b | Secondary cases (v0.5) | reference, calibration, kernels, prediction, queue scripts | builds, calibration, registration, measurement on frodo | 3 builds of 8–20 h; 2 sessions | about 2–3 weeks |
| 6 | Writing | draft, figures, supplement, dataset | review | – | after 5b |

# 16. Decisions (11 October 2026)

1. The four test cases and the three targets (1%, 0.3%, 0.1%) are agreed.
2. The secondary cases (8-asset basket; Asian call with control variate) are included.
3. Registration as in paper 1: hashed commits in the public GitHub repository.
4. Dr. Hettiarachchi agrees to the paper. It is a single-author paper; he is thanked in the acknowledgements, and paper 1 is cited for the platform, the baselines and the time–energy model.
5. (15 October 2026) The secondary cases are measured before the paper is written, so that the paper reports all cases of the protocol.

# 17. Changes from v0.2 (12 October 2026)

1. Section 7: the empirical rule chooses the settings for both schemes; the FT model rule is kept for comparison (reason in Section 7).
2. Section 8: hypotheses fixed with the calibration numbers; H4 restated as a crossover between targets.
3. Section 9b: calibration results added.

Nothing else changed. No kernel had been written and the card had not been used for this study when these changes were made.

# 18. Changes from v0.3 (13–14 October 2026)

1. Section 7: the steady-state energy to target (without the fixed start-up cost) is added as a secondary outcome. Reason: many settings take microseconds of work against a start-up cost of about 0.7 ms per card run.
2. Section 7: each strike is priced from the call payoff or from the put payoff and put-call parity, whichever has the smaller finite variance. Reason: the call payoff has infinite variance in Case II (moment explosion), found by the CPU-native check of the v0.3 settings. The card kernels were rebuilt with this change (QE again met timing at 225 MHz; FT was being rebuilt at 225 MHz) and the calibration was repeated with new seeds.
3. Section 8: the numbers in H1, H3 and H4 recomputed from the new calibration by the v0.3 rule. H4 changes: the card running FT is now expected to win at 0.3% only (v0.3: at 1% and 0.3%).
4. Section 9b: results of the second calibration; the first is kept for reference.
5. Section 10: short runs are repeated within a measurement window of at least 10 s; long runs added for the steady-state outcome.
6. Section 11: the secondary outcome added to the analysis.

The primary outcome, the targets, the cases, the setting rule and H1, H2, H3, H5 and H6 are unchanged in substance. The card had not been used for this study when these changes were made, and no prediction had been computed.

# 19. Changes from v0.4 (15 October 2026)

1. Section 4b (new): the two secondary cases specified: parameters, strikes, payoff forms, reference prices, estimators, targets and setting rule.
2. Section 8b (new): hypotheses HB1, HB2, HA1, HA2, HA3 for the secondary cases; H5 repeated on them; two registered predictions (same method; informed by the main-case measurements).
3. Section 9c (new): pilot of the secondary cases.
4. Sections 10, 12, 14, 15, 16: measurement steps, kernel notes, risks, plan and decision for the secondary cases.

**When v0.5 was written.** After the main-case measurements (phase 5, two sessions, 14–15 October 2026) and their analysis. The secondary cases had not been run anywhere except the numpy pilots of Section 9c; no code or card design for them existed. Main-case results used in v0.5 are named where they are used: the measured cost ratio QE/FT per path-step and the algorithm factors (HB2), the card's steady-state advantage by scheme (HA3), and the measured start-up cost and card power (the informed prediction). Nothing about the main cases changed: their cases, targets, settings, hypotheses H1–H6, prediction file and analysis plan are as registered in v0.4 and in the prediction file.

# References

- Albrecher H., Mayer P., Schoutens W., Tistaert J. (2007). The little Heston trap. *Wilmott Magazine*, January, 83–92.
- Andersen L., Piterbarg V. (2007). Moment explosions in stochastic volatility models. *Finance and Stochastics* 11(1), 29–50.
- Andersen L. (2008). Simple and efficient simulation of the Heston stochastic volatility model. *Journal of Computational Finance* 11(3), 1–42.
- Chow G.C.T., Tse A.H.T., Jin Q., Luk W., Leong P.H.W., Thomas D.B. (2012). A mixed precision Monte Carlo methodology for reconfigurable accelerator systems. *FPGA '12*.
- de Schryver C., Shcherbakov I., Kienle F., Wehn N., Marxen H., Kostiuk A., Korn R. (2011). An energy efficient FPGA accelerator for Monte Carlo option pricing with the Heston model. *ReConFig 2011*, 468–474.
- de Schryver C., Torruella P., Wehn N. (2013). A multi-level Monte Carlo FPGA accelerator for option pricing in the Heston model. *DATE 2013*.
- Duffie D., Glynn P. (1995). Efficient Monte Carlo simulation of security prices. *Annals of Applied Probability* 5(4), 897–905.
- Giles M.B. (2008). Multilevel Monte Carlo path simulation. *Operations Research* 56(3), 607–617.
- Haas I.-B., Giles M.B. (2025). A nested MLMC framework for efficient simulations on FPGAs. arXiv:2502.07123.
- Kemna A.G.Z., Vorst A.C.F. (1990). A pricing method for options based on average asset values. *Journal of Banking and Finance* 14(1), 113–129.
- Heston S.L. (1993). A closed-form solution for options with stochastic volatility with applications to bond and currency options. *Review of Financial Studies* 6(2), 327–343.
- Lewis A.L. (2000). *Option Valuation under Stochastic Volatility*. Finance Press.
- Lord R., Koekkoek R., van Dijk D. (2010). A comparison of biased simulation schemes for stochastic volatility models. *Quantitative Finance* 10(2), 177–194.

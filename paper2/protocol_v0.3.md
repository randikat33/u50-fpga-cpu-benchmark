---
title: "Study protocol, paper 2: energy to a target accuracy in Heston Monte Carlo on an FPGA card and its host"
subtitle: "Version 0.3 (after the CPU calibration, before any kernel or card run), 12 October 2026. Author: R. T. P. G. Pathiranage (sole author)"
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

**Secondary cases (included, measured after the main cases):** paper 1's 8-asset basket (reference from a long double-precision QE run on the CPU), and the arithmetic Asian call under Black–Scholes with the geometric-Asian control variate (no time-step bias; tests variance reduction instead).

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

**Error model.** For time step $h$ and $N$ paths, with bias $b(h) \approx c\,h^{p}$ and payoff variance $\sigma^2$ per path:

$$\text{MSE} = b(h)^2 + \frac{\sigma^2}{N}.$$

The work is $W = N\,T/h$ path-steps. Minimising $W$ subject to $\text{MSE} \le \varepsilon^2$ gives the classical split (Duffie and Glynn 1995):

$$b^2 = \frac{\varepsilon^2}{1+2p}, \qquad h_{\text{opt}} = \left(\frac{\varepsilon}{c\sqrt{1+2p}}\right)^{1/p}, \qquad N_{\text{opt}} = \frac{(1+2p)\,\sigma^2}{2p\,\varepsilon^2}.$$

**Rule used for the settings (changed in v0.3).** For both schemes the setting is chosen by the empirical rule: the coarsest grid step whose measured absolute bias plus twice its standard error is at most $\varepsilon/\sqrt{2}$ at all three strikes, with $N = 2\sigma^2/\varepsilon^2$ pairs ($\sigma^2$: variance of one antithetic pair average at that step, largest over the strikes), rounded up to whole work units.

The formulas above (the "model rule": $h_{\text{opt}}$ rounded down to the grid, $N_{\text{opt}}$) are reported for comparison only. In v0.2 they were the rule for FT. The calibration showed that extrapolating the fitted power law underestimates the bias at fine steps: the model rule missed the target in 3 of 12 FT settings (by up to 6%) and met it with no margin in a fourth (0.998 of the target), while the empirical rule met it in all 24 settings. The change was made before any kernel was written or any card run.

**Cost model.** $h_{\text{opt}}$ and $N_{\text{opt}}$ do not depend on the platform; the platform enters only through the time per path-step $\tau$ and the power $P$. For platform $a$ and scheme $s$: time $t = W_s\,\tau_{a,s} + t_0$ and energy $E = t\,P_a$, with $\tau$, $t_0$ and $P$ from paper 1's model (routed design and Vivado power estimate for the card; CPU-only runs for the CPU). The energy ratio of two settings is therefore

$$\frac{E_{a,s}}{E_{b,s'}} \approx \frac{W_s}{W_{s'}} \cdot \frac{\tau_{a,s}\,P_a}{\tau_{b,s'}\,P_b},$$

the product of an **algorithm factor** and a **platform factor**. RQ2 compares the two factors directly.

# 8. Hypotheses (fixed in v0.3, after the CPU calibration, before any card run)

The numbers come from the CPU calibration (Section 9b). The platform numbers ($\tau$, $P$ of the new kernels) do not exist yet; the quantitative prediction of every comparison is made from the routed designs and registered in the prediction file (phase 4) before any card run.

- **H1 (scheme).** In cases I–III, FT needs more path-steps than QE at every target: at least 8× at 1% and at least 30× at 0.3% and 0.1% (calibration: 8.5× to 132×).
- **H2 (cost per step).** QE costs more per path-step than FT on every platform, by a factor between 1.2 and 3 (CPU, scalar float model: 1.67).
- **H3 (main).** In cases I–III, at every target, the CPU socket running QE (algorithm-matched code) needs less energy than the card running FT: the algorithm factor exceeds the platform factor. The narrowest case is Case III at 1% (work ratio 8.5, about 5 after the per-step cost, against the card's 3.8× energy advantage per path-step in paper 1).
- **H4 (Feller satisfied: a crossover).** In case P1 the work ratio is small at 1% and 0.3% (4.7× and 4.3×, about 2.7 after the per-step cost) and larger at 0.1% (16×, about 10). The card running FT therefore needs less energy than the CPU running QE at 1% and 0.3%, and more at 0.1%: the ranking changes with the target.
- **H5 (model).** The model of Section 7 predicts the energy to target within 20% and the winner of every pairwise comparison (platform × scheme) correctly, and the measured RMSE of every predicted setting is within its target (allowing 10% for the uncertainty of the bias estimate).
- **H6 (reflection, shown, not measured on the card).** The reflection scheme of paper 1's portfolio cannot reach the 1% target in cases I–III at any affordable step (pilot bias at $h=1/32$: 25.8 in Case I, against a 1% target of 0.13).

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

# 9b. Calibration results (phase 2, done 11 October 2026)

The float model (bit-identical to the card) was run on frodo's CPU for 4 cases × 2 schemes × 7 (QE) or 13 (FT) step sizes, until each strike's bias was known to $\varepsilon_{\min}/5$ or resolved to 10% (96 jobs, up to 138 million antithetic pairs per job, no payoff overflow).

**FT bias fit**, $|b| = c\,h^p$ at $K=100$: Case I $c=8.21$, $p=0.994\pm0.011$; Case II $c=8.89$, $p=0.985\pm0.015$; Case III $c=4.67$, $p=1.09\pm0.04$; P1 $c=1.20$, $p=1.69\pm0.08$ (three resolved points). FT is first order when the Feller condition fails.

**QE.** The pilot's possible bias in Case I is real but small: $-0.0059\pm0.0015$ at 16 steps per year (0.045% of the price). QE without the martingale correction meets all targets. Per path-step, QE costs 1.67× FT on the CPU (scalar float model, 48 threads).

**Work to target, FT/QE (path-steps, empirical rule):**

| Case | 1% | 0.3% | 0.1% |
|---|---|---|---|
| I | 32.9 | 65.0 | 129 |
| II | 55.4 | 126 | 119 |
| III | 8.5 | 33.3 | 132 |
| P1 | 4.7 | 4.3 | 16.3 |

The largest single setting is FT in Case II at 0.1%: 2,048 steps per year, 9.1 million pairs, $5.6\times10^{11}$ path-steps.

**Float32 against double precision on the same paths:** QE differs by at most $4\times10^{-5}$; P1 by $2.5\times10^{-5}$ for both schemes (0.3% of the smallest target). FT in cases I–III differs path by path (the scheme is very sensitive near zero variance when the Feller condition fails), with mean differences consistent with zero within 3 SE. The calibration measures the float model's own bias, which is the card's, so this does not affect the settings.

# 10. Measurement plan

1. **Calibration (phase 2, CPU only; done, Section 9b).** The matched float32 CPU code, which is bit-identical to the card, measures bias and $\sigma$ at each grid step with $\text{SE}_b \le \varepsilon_{\min}/5$. For FT, the bias at the finest steps is taken from the fitted power law and checked at one step; the change in bias between $h$ and $h/2$ is measured on coupled paths (the same Brownian increments, as in multilevel MC), which needs far fewer paths. Because the output is bit-identical, the bias it measures is the card's bias, so the prediction stays "before any card run". The double-precision code measures the float32 rounding error separately. The CPU-native code is calibrated at the chosen steps only.
2. **Prediction (phase 4).** For each case, target, scheme and platform: $h_{\text{opt}}$, $N_{\text{opt}}$, predicted time and energy, and the predicted winner. The file is hashed and registered before the card is used, as in paper 1.
3. **Runs (phase 5).** Each predicted setting runs 5 times in each of 2 sessions, with the steady-clock protocol and power logging of paper 1 (card rails, RAPL per socket, wall check). The seeds differ between repeats.
4. **Accuracy check.** For every run: price error against the reference for each strike; RMSE from the bias (calibration) and the run's SE.

# 11. Analysis plan

- Primary outcome: energy to target, per case, target, platform and scheme, with bootstrap 95% confidence intervals over repeats.
- Secondary: time to target; the algorithm factor and the platform factor of Section 7.
- Figures: error against energy (one panel per case, one line per platform × scheme); a bar chart of the two factors.
- Prediction test: verdicts correct, relative errors of time and energy, all reported, including failures.

# 12. FPGA kernel notes

- Two kernels (FT, QE) derived from paper 1's MC Heston kernel: interleaved path slots per lane, xoshiro128** streams, segmented inverse-CDF table, exact 128-bit payoff accumulation.
- QE needs $\ln$ for the exponential branch and $\sqrt{\ }$ for the diffusion; to keep bit identity with the CPU, $\ln$ will use a table method as $\exp$ did in paper 1. Each lane evaluates both branches and selects one, so the pipeline keeps one step per cycle.
- If QE needs fewer lanes or a lower clock, that is part of the platform factor and is reported, not avoided.

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

# 15. Plan and roles

| # | Phase | Claude | You | Server time | Weeks |
|---|---|---|---|---|---|
| 1 | Protocol | this document | read, agree, register on OSF | – | 1 |
| 2 | Calibration | CPU code (matched, native, double), scripts | run on frodo when idle | a few hours | 2–3 |
| 3 | Kernels | FT and QE kernels, C-simulation tests | build bitstreams, run checks | 1–3 builds of 8–20 h | 3–8 |
| 4 | Prediction | model, prediction file | register it | – | 9 |
| 5 | Measurement | queue scripts | start the queue | 2–4 days | 10–11 |
| 6 | Writing | draft, figures, supplement, dataset | review; co-author approval | – | 12–16 |

# 16. Decisions (11 October 2026)

1. The four test cases and the three targets (1%, 0.3%, 0.1%) are agreed.
2. The secondary cases (8-asset basket; Asian call with control variate) are included.
3. Registration as in paper 1: hashed commits in the public GitHub repository.
4. Dr. Hettiarachchi agrees to the paper. It is a single-author paper; he is thanked in the acknowledgements, and paper 1 is cited for the platform, the baselines and the time–energy model.

# 17. Changes from v0.2 (12 October 2026)

1. Section 7: the empirical rule chooses the settings for both schemes; the FT model rule is kept for comparison (reason in Section 7).
2. Section 8: hypotheses fixed with the calibration numbers; H4 restated as a crossover between targets.
3. Section 9b: calibration results added.

Nothing else changed. No kernel had been written and the card had not been used for this study when these changes were made.

# References

- Albrecher H., Mayer P., Schoutens W., Tistaert J. (2007). The little Heston trap. *Wilmott Magazine*, January, 83–92.
- Andersen L. (2008). Simple and efficient simulation of the Heston stochastic volatility model. *Journal of Computational Finance* 11(3), 1–42.
- Chow G.C.T., Tse A.H.T., Jin Q., Luk W., Leong P.H.W., Thomas D.B. (2012). A mixed precision Monte Carlo methodology for reconfigurable accelerator systems. *FPGA '12*.
- de Schryver C., Shcherbakov I., Kienle F., Wehn N., Marxen H., Kostiuk A., Korn R. (2011). An energy efficient FPGA accelerator for Monte Carlo option pricing with the Heston model. *ReConFig 2011*, 468–474.
- de Schryver C., Torruella P., Wehn N. (2013). A multi-level Monte Carlo FPGA accelerator for option pricing in the Heston model. *DATE 2013*.
- Duffie D., Glynn P. (1995). Efficient Monte Carlo simulation of security prices. *Annals of Applied Probability* 5(4), 897–905.
- Giles M.B. (2008). Multilevel Monte Carlo path simulation. *Operations Research* 56(3), 607–617.
- Haas I.-B., Giles M.B. (2025). A nested MLMC framework for efficient simulations on FPGAs. arXiv:2502.07123.
- Heston S.L. (1993). A closed-form solution for options with stochastic volatility with applications to bond and currency options. *Review of Financial Studies* 6(2), 327–343.
- Lewis A.L. (2000). *Option Valuation under Stochastic Volatility*. Finance Press.
- Lord R., Koekkoek R., van Dijk D. (2010). A comparison of biased simulation schemes for stochastic volatility models. *Quantitative Finance* 10(2), 177–194.

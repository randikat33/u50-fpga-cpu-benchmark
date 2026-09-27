# 03_MC_Heston: v2 Monte Carlo Heston basket option (Alveo U50 vs Xeon Gold 5318Y)

## 1. Workload

This is the v1 model (`krnl_heston_mc_p6.cpp`, `cpu_heston_mc_unified.cpp`), unchanged:

* **Option.** An arithmetic-basket call (or `--put`) on 8 assets, with K = 100, T = 1 and r = 0.01, discounted by e^(-rT).
* **Heston parameters per asset.** S0_i = 100 + 0.1·i, v0 = θ = 0.04, κ = 1.5, σ = 0.3, ρ = −0.6, q = 0.
* **Correlation.** The 8 spot shocks are correlated through a lower-triangular Cholesky factor L. v1 uses L = I; `--corr c` gives an equicorrelated test basket. The variance shock of asset i is ρ_i·Z_S,i + √(1−ρ_i²)·Z_⊥,i.
* **Discretisation.**
  * Log-price: Euler, x += (r−q−½v)dt + √(v·dt)·Z_S.
  * Variance: Euler, v ← max(v + κ(θ−v)dt + σ√(v·dt)·Z_v, 0). This is v1's "truncation" scheme: the stored v is never negative, so full truncation and absorption coincide.
* **Antithetic sampling.** Paths come in pairs (+Z, −Z). The path count is rounded up to an even number (`paths_eff`), as v1 also did (it rounded up to whole batches).
* **Precision.** The path state is float32, as in v1. The payoff moments are accumulated **exactly** as integers.
* **Outputs.** `price`, `stderr` (the correct standard error for antithetic pairs), `stderr_naive` (the v1 formula, which treats the paths as independent) and `mom_hash`.
* **Paper configurations** (`MC_CONFIGS`, paths:steps): 131072:32, 262144:32, 524288:32, 1048576:32, 2097152:32, 524288:64, 524288:128, 524288:256 and 4194304:64.

v2 prices agree with v1 within statistical error at 524288×32:

| Run | Price |
|---|---|
| v2 | 3.43875 ± 0.0041 |
| v1 CPU | 3.4396 ± 0.0060 |
| v1 FPGA | 3.4352 ± 0.0060 |

## 2. v1 diagnosis (thesis reports)

* **Architecture.** There were 7 engines in 1 CU, with 1 CU in the xclbin.
* **RNG is the throughput limit.** Each engine used two MT19937 engines with the PPND7 inverse CDF, one normal at a time. `RNG_PATH_IN_BATCH` was not flattened and took **157 cycles per path-step** (16 normals). The path stage waited on it.
  * Measured throughput: 131072×32 took 503 ms, i.e. **8.3 M path-steps/s**.
  * This matches the estimate 7 × 185 MHz / 157 = 8.2 M path-steps/s.
* **Pricing loop.** It ran at II = 4, with a float accumulator recurrence.
* **Timing.** The target was 300 MHz, but the reported Fmax was **185 MHz**: WNS was −2.07 ns on `clk_kernel_00`, and `hbm_aclk` also failed.
* **Utilisation.** DSP was 74.1 % (4410), LUT 67.4 % and BRAM 24.5 %. Each asset update used ~30 DSP, because float add/sub was mapped to 2 DSP.
* **Antithetic sampling was ineffective.** The "antithetic" batches negated **fresh** random numbers, so v1 got no variance reduction. That is why v1's SE equals the naive SE.
* **v1 CPU baseline.** It used `mt19937_64` + `std::normal_distribution` with scalar OpenMP code. It measured 28.5 M path-steps/s on 24 cores, so the FPGA was 3.4–4.5× **slower**.

## 3. v2 FPGA micro-architecture

```
 fpar (96 f32, HBM0) -> LOAD -> DATAFLOW ------------------------------------------------------------+
   split: pair range -> L lane jobs (contiguous), pricer consts, totals                                |
   lane[l] (x L, II=1)                                    merge (1 side/cycle)   price (II=1)   accum  |
   +--------------------------------------------------+   rotating priority,     8 x exp(X/2)   128-bit|
   | slot RAM (IL=128 slots: XA,XB,UA,UB,rng,cnt)      |  fin[l]  side A then B -> tree basket -> exact |
   |  read slot t mod IL --> [empty? seed(pair) : state]|--FIFO-->                  payoff m (int) S1,S2,SP
   |  --> 16x xoshiro128** --> 16x ICDF (ROM+2 DSP)     | depth IL                                      |
   |  --> Cholesky tree (36 mul) + w_i (16 mul)         |                                               |
   |  --> side A (+Z) and side B (-Z): 8 assets each    |                                               |
   |      P=sqrt(U); U'=max(U*omk+kth2+P*w,0);          |                                               |
   |      X'=X+(P*zS2-U)                                |                                               |
   |  --> write slot (latency ~60 < IL); emit if cnt==M |                                               |
   +--------------------------------------------------+                                  out (8 x u64, HBM1)
```

**Loop interleaving.** Each lane holds IL = 128 slots in a RAM. On every clock cycle it reads slot `t mod IL`, advances it by one time step and writes it back about 60 stages later. The same slot is next read IL cycles later, so `DEPENDENCE inter false` is valid when IL > the update latency (check this in the report, §8).

**Antithetic pairs share one datapath.** One slot is one antithetic pair. The RNG, the ICDF and the Cholesky product are computed once and used by both sides, because negation is exact. Each lane therefore does **2 path-steps per cycle**.

**Seeding is feed-forward.** Every cycle, the lane hashes the next pair index into a fresh xoshiro state (4 × fmix32 of `pair ^ key_j`). A slot that is empty takes this fresh state and performs its first step in the same visit. Seeding therefore never limits the rate and needs no minimum step count; M ≥ 1 is correct.

**Pricer rate.**
* Slots start and finish in waves of ≤ IL pairs per lane. The merger drains 1 path per cycle.
* With FIFO depth IL, **no back-pressure occurs when M ≥ 2·L** (12 for L = 6; the paper uses M ≥ 32).
* For smaller M the lanes stall briefly. This is still correct, but slower.

**Exact trip count.** A lane with n pairs runs for T = ((⌊(n−1)/IL⌋ + 1)·M − 1)·IL + ((n−1) mod IL) + 1 cycles.

**Numerics.** The kernel, the CPU and the tests use identical numerics, all defined in `common/mc_model.hpp`:
* **RNG.** Per-pair xoshiro128** streams, keyed by (seed, global pair index). Results therefore do not depend on the CU, lane or thread split.
* **Normals.** A segmented quadratic ICDF: the index is (lz = clz of the 31-bit magnitude, next 6 bits), giving 2048 ROM words; x has 15 bits. It uses 2 small integer multiplies and outputs an int in units of 2^-20, which converts exactly to float. The shock scale 2^-20 is folded into the constants L2 and c2q, so no extra multiply is needed.
* **State.** X = 2·ln S (without drift) and U = v·dt. This removes the ½ and dt multiplies: 3 fmul + 4 fadd + 1 fsqrt per asset side, versus 8 + 7 in v1. The total drift 2(r−q)T is added once, in the pricer.
* **exp(X/2).** A fixed-point table method with 4 integer multiplies and 1 exact float scaling. The relative error is < 1.1e-7.
* **Payoff.** Every float payoff of 8·basket − 8K is an integer multiple of 2^E8, with E8 = ilogb(8K) − 24. The kernel converts it exactly to m < 2^40 (saturating, with a counter) and accumulates S1 = Σ(mA+mB), S2 = Σ(mA²+mB²) and SP = Σ(mA+mB)² in 128 bits.
  * These sums do not depend on order, which makes FPGA == CPU **bit-identical** (`mom_hash`), not merely close to 1e-12.
  * The price and standard errors are then computed in long double on the host.

**Throughput equation.** Path-steps/s = 2 × L × Fmax × N_CU. The design point is L = 6, N_CU = 1:

| Fmax | Path-steps/s |
|---|---|
| 300 MHz | 3.6e9 |
| 250 MHz | 3.0e9 |
| 200 MHz | 2.4e9 |

* **Where the bottleneck is.** Compute is the limit. PCIe carries only 384 B in and 64 B out per call. The host overhead per call is ~1 ms (XRT start/wait).
* **Target clock.** 300 MHz, with a realistic expectation of 230–280 MHz.

### Resource estimate

Vitis HLS 2023.1 float cores on UltraScale+ are assumed to cost:

| Core | LUT | FF | DSP |
|---|---|---|---|
| fmul (fulldsp) | ~90 | ~150 | 2 |
| fadd/fsub (fulldsp) | ~230 | ~300 | 2 |
| fsqrt (fabric, latency ~20) | ~900 | ~1500 | 0 |
| sitofp | ~200 | — | — |

The shell (static and dynamic region) is taken as ≈ 110 k LUT, based on the v1 util report.

| Block | fmul | fadd | fsqrt | int→f | int mul | DSP | LUT | FF | BRAM36 |
|---|---|---|---|---|---|---|---|---|---|
| 16 × RNG + ICDF | – | – | – | 16 | 32 | 32 | 7 k | 8 k | 25 (8 ROMs × 2048×54) |
| seeding (4 × fmix32) | – | – | – | – | 8 const | 16 | 1.5 k | 2 k | – |
| Cholesky + w | 52 | 36 | – | – | – | 176 | 13 k | 19 k | – |
| 2 sides × 8 assets | 48 | 64 | 16 | – | – | 224 | 33 k | 60 k | – |
| slot RAM, muxes, control, pipeline registers | – | – | – | – | – | – | 8 k | 40 k | 11 (IL=128 × 1169 bits) |
| **per lane** | 100 | 100 | 16 | 16 | 40 | **~448** | **~63 k** | **~130 k** | **~36** |
| pricer + merge + accum (per CU) | 9 | 16 | – | 8 | 34 | ~146 | ~28 k | ~45 k | ~2 |
| **L = 6, 1 CU (+ shell)** | | | | | | **~2.84 k (48 %)** | **~516 k (59 %)** | **~0.9 M (52 %)** | **~220 (16 %) + shell** |
| L = 7 | | | | | | 3.28 k (55 %) | 579 k (66 %) | | |
| L = 8 | | | | | | 3.73 k (63 %) | 642 k (74 %) ✗ | | |

LUT is the binding resource. L = 6 is the default because these estimates are uncertain by ±30 % and v1 failed timing at 64 % LUT. `make xclbin LANES=7` is the documented step up (see §8). A single CU is used because one pricer and one pair of m_axi adapters serve all lanes. `cfg/link_2cu.cfg` (`NCU=2 LANES=3`) is the fallback if one large CU cannot be placed.

## 4. v2 host (`host/mc_fpga.cpp`)

* **API.** Native XRT API. CUs are opened as `mc_heston_v2:{mc_heston_v2_i}`, probed until one fails; with xrt_sim, use `--cus`.
* **Buffers.** Each CU gets two 4 KB BOs (`group_id` of each argument), allocated once. Only the used bytes are synced: 384 B H2D and 64 B D2H. A failed allocation stops the host with a clear message.
* **Splitting.** The antithetic pairs [0, pairs) are split into contiguous per-CU ranges, and each CU splits its range across its lanes. The results are independent of both splits (tested).
* **One run.**
  1. Sync the parameters of all CUs.
  2. Start every `xrt::run` (the CUs execute concurrently).
  3. Wait for all of them.
  4. Sync the outputs.

  This makes the phases sequential windows: `t_compute_s = t_h2d_s + t_kernel_s + t_d2h_s` for the median of `--runs` runs, after `--warmup` (default 1).
* **Consistency.** Repeated runs must produce identical moments.
* **`--verify`.** Recomputes the scalar reference (std::thread) over all pairs if pairs·M ≤ 2^24. Otherwise it recomputes the first `--verify-pairs` (65536) pairs and makes an extra kernel call over the same range. The result must be **bit-identical**.

## 5. v2 CPU (`cpu/mc_cpu.cpp`, `cpu/mc_avx512.hpp`)

**`--impl avx512` (default).**
* Hand-written AVX-512F/CD/DQ code. One zmm holds 16 antithetic pairs, i.e. 32 paths.
* Per step, per zmm:
  * 16 vectorised xoshiro128** draws.
  * 16 vectorised ICDFs: `vplzcntd`, 2 × `vpgatherdd` and 2 × `vpmulld`.
  * The Cholesky tree.
  * Two side updates with `vsqrtps`.
* The pricer is vectorised too: fexp uses 8-lane 64-bit `vpmuldq`/`vpmuludq`, `vpgatherqd`/`vgatherqps` and `vcvtqq2ps`.
* **Alternatives measured and rejected, both slower.**
  * One 64-bit gather per 8 lanes: +27 %.
  * `vpermi2d` lookups for the two central octaves plus masked tail gathers: +28 %.

**`--impl scalar`.** The reference model itself.

**Common to both.**
* OpenMP `schedule(static)` over 16-pair blocks.
* Per-thread, 64-byte-aligned accumulators, first-touched by the thread that owns them. The per-block state is ~2 KB of stack, so memory placement is irrelevant.
* Flags: `-O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off`. `-ffp-contract=off` is required for bit-identity: no FMA. No `-ffast-math`.
* The hot loops are intrinsics, so auto-vectorisation is not relied on and no `-fopt-info` notes are needed.
* No industry library exists for this exact model; QuantLib is scalar and has different numerics.

**Measured in the sandbox** (Xeon @ 2.8 GHz, AVX-512, 1 core; 524288×32):

| Impl | ns per path-step (1 core) | M path-steps/s |
|---|---|---|
| avx512 | **18.0** | 55.4 (2 cores: 110, linear) |
| scalar | 64 | 15.7 |

## 6. Fairness

**Identical on both sides.**
* The model and parameters.
* The RNG algorithm and streams, the ICDF table, the float32 recurrence with the same operation order, the exp and the payoff quantisation.
* The exact accumulation, and therefore the bit-identical price and SE.
* The path count (`paths_eff`) and the warm-up/median policy.

**Different.**
* The FPGA runs the steps of a pair interleaved in time. The CPU runs 16 pairs per zmm.
* The FPGA timing window includes the (tiny) PCIe transfers and XRT call overhead. The CPU timing includes OpenMP fork/join.
* Neither side specialises for L = I. A sparse-Cholesky CPU build would be ~15 % faster, and a constant-folded FPGA build would also shrink. Neither is used.

## 7. Build and run

```
# server
source /tools/Xilinx/Vitis/2023.1/settings64.sh; source /opt/xilinx/xrt/setup.sh
make cpu host                      # build/mc_cpu, build/mc_fpga
make xclbin LANES=6                # build/mc_heston.xclbin (1 CU); rm -rf build/hls when changing LANES
make fpga_reports
numactl -N0 -m0 taskset -c 0-23 build/mc_cpu --paths 524288 --steps 32 --runs 5 --verify
build/mc_fpga --xclbin build/mc_heston.xclbin --paths 524288 --steps 32 --runs 5 --verify
```

**CLI.** Both programs take the v1 options `--paths N --steps M --seed S --runs R --strike --r --T --put`. They also take `--warmup W`, `--corr c`, `--verify`, `--verify-pairs`, `--out FILE` and `--help`.
* CPU only: `--impl avx512|scalar` and `--threads N`.
* FPGA only: `--xclbin X` (required), `--device N` and `--cus K`.

**RESULT keys.**
* **Common:** `project=mc_heston`, `platform`, `impl`, `paths`, `paths_eff`, `pairs`, `steps`, `seed`, `option`, `strike`, `corr`, `runs`, `t_read_s`, `t_compute_s`, `t_compute_min_s`, `msteps_per_s`, `paths_per_s`, `price`, `stderr`, `stderr_naive`, `payoff_overflows`, `mom_hash`, `verify_pairs`, `verify_bit_identical`, `ok`, `t_write_s` and `t_total_s`.
* **CPU:** `isa`, `threads` and `ns_per_path_step_core`.
* **FPGA:** `t_xclbin_s`, `n_cu`, `t_alloc_s`, `lanes`, `il`, `t_h2d_s`, `t_kernel_s`, `t_d2h_s` and `msteps_per_s_kernel`.

`MARK start`/`MARK end` enclose the timed runs.

**`make sim_test` in the sandbox: ALL PASS, about 2 min.**
1. The ROM header is reproducible from the generator.
2. Kernel C-sim == scalar == AVX-512, bit-identical, for 8 cases × LANES 6, 1 and 3. The cases include 0 pairs, M = 1, pairs < lanes, pair counts not divisible by the lanes or by 16, steps < IL, several waves, full waves plus a remainder, pair indices near 2^32, a put, a correlated basket, and partition invariance (61 + 140 == 201).
3. ICDF quality over 4e6 draws:

   | Statistic | Value |
   |---|---|
   | mean | +3.8e-4 |
   | variance | 1.00045 |
   | skewness | −8.4e-4 |
   | excess kurtosis | −5.9e-4 |
   | KS √N·D | **0.83** (5 % critical value 1.358) |
   | P(\|z\|>3) | 10825 (expected 10799 ± 104) |
   | max \|icdf − Φ⁻¹\| | 9.3e-7 |
   | adjacent-stream correlation | 5e-4 |
   | fexp relative error | 1.1e-7 |

4. CPU avx512 == scalar, with 1 or 2 threads, for 5 sizes. `--verify` passes, and `--steps > 65535` is rejected.
5. Host end-to-end via xrt_sim with 1, 2 and 3 CUs: `mom_hash` and price are identical to the CPU, subset verify passes, and the keys, JSON and MARK lines are present.
6. The independent double-precision reference (mt19937_64 + Box-Muller) agrees within 3 SE:

   | Case | v2 | Double reference |
   |---|---|---|
   | v1 market, call, M = 32 | 3.44119 ± 0.0047 | 3.43871 ± 0.0048 |
   | correlated basket, put, M = 8 | | within 3 SE |

7. The host passes the real-XRT header syntax check.

## 8. Synthesis risks and what to check

| # | Risk | What to check / what to do |
|---|---|---|
| 1 | **Interleave validity.** In `csynth.rpt`, the `lane_RING` pipeline depth (iteration latency) must be < 127. | If it is ≥ 127, set `MC_IL_LOG2` to 8. The IL = 128 margin covers an estimated ~60–70 stages. II must be 1 for RING, MERGE, PRICE and ACCUM. |
| 2 | **Bit-exactness of the Xilinx float cores.** IEEE rounding, correctly rounded `fsqrt`, and exact `sitofp` for \|n\| < 2^24 cannot be checked in the sandbox. | Run `cosim_design` with `test/tb_kernel.cpp` (small cases). On the card, always run once with `--verify`. Subnormals cannot occur: U' is either exactly 0 or ≥ ~κθdt² minus a term of comparable magnitude, and terms are multiples of ulps ≫ 2^-126. |
| 3 | **`tree_sum_hw` with an `int n` argument.** It relies on constant propagation after INLINE. | If HLS reports variable-bound loops in RING, write the 8 row trees explicitly. |
| 4 | **2-D float arrays** with `ARRAY_RESHAPE` + `ram_s2p` + DEPENDENCE; `ringC` in LUTRAM. | The path LUTRAM read → 33-bit compare → `nxt` increment must fit in 3.3 ns. Otherwise move `ringC` to BRAM, or shorten `nxt` to 32 bits. |
| 5 | **Dataflow.** `lane()` is instantiated L times (the static const ROMs must replicate per instance). Stream arrays `job[]`/`fin[]` carry the depth pragmas. `merge` uses `empty()` and a conditional read in an II = 1 loop. | Check the dataflow viewer. |
| 6 | **ROM access.** Each ICDF ROM copy has exactly 2 readers (8 copies per lane), and so does each exp ROM (4 copies). | If HLS merges the copies, II rises. |
| 7 | **Timing.** A float-IP-dense design at ~60 % LUT. | If WNS < 0: first relink at 250 MHz (`freqHz` in `cfg/link.cfg`, `PERIOD=4.0`). If that fails, use `LANES=5`. If post-route LUT < 55 % and DSP < 60 % with timing met, try `LANES=7`. Always rebuild with `rm -rf build/hls`. |
| 8 | **Back-pressure when M < 2L.** It is only exercised in RTL: the C-sim shim streams are unbounded and the dataflow runs sequentially. | Correctness does not depend on it (blocking writes stall the whole pipeline). |
| 9 | **Minor unverified items.** The 64-bit trip counter compare; `split` division by a constant (not in a pipeline); `ap_fixed<32,9>` = float and `ap_ufixed<40,40>` = float conversions; the vivado retiming property syntax in `link.cfg`. | — |

## 9. Expected results (honest prediction)

**FPGA.** L = 6 lanes × 2 path-steps per cycle × 230–280 MHz gives **2.8–3.4e9 path-steps/s**, kernel only. That is about 340–400× v1 (8.3 M/s). For 524288×32 (1.68e7 path-steps) the kernel time is ≈ 5–6 ms, plus ~1 ms of XRT overhead.

**CPU.** The sandbox Xeon measures 18 ns/path-step per core. On the 5318Y (2.1 GHz base, turbo up to 3.4 GHz, AVX-512 licence down-clocking) expect 16–24 ns. With 24 cores at ~90 % scaling (no memory bottleneck; the state fits in L1), that is **0.9–1.35e9 path-steps/s**, about 30–45× v1's CPU.

**Prediction.**
* Compute-only: the FPGA is **≈ 2–3.5× faster** than the tuned 24-core AVX-512 socket.
* Energy: **≈ 15–25× better**, from ~20–25 W on the card versus ~150–200 W for the socket.
* The smallest configuration (131072×32 = 4.2 M path-steps, CPU ≈ 4 ms) is dominated by fixed overheads on both sides. There the two will be within ~1.5× of each other, and the CPU may win once `t_xclbin_s` or the XRT call overhead is counted.

**When the CPU could win or tie.**
* Timing closes at ~200 MHz with only 5 lanes: 2.0e9 path-steps/s, or ~1.5–2×.
* The Xeon reaches < 10 ns/path-step/core.
* The workload is split into many tiny calls.

**How to report it.** The result is reported as measured. The bit-identical `mom_hash` guarantees that both sides computed the same estimator.

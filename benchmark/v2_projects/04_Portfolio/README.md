# 04_Portfolio (v2): two-stage Heston Monte Carlo portfolio analysis, FPGA vs CPU

Alveo U50 (4 CUs x 8 lanes, 300 MHz) against a Xeon Gold 5318Y (24 cores, AVX-512). The RNG, the
normal transform, the float arithmetic and the accumulation are identical on both sides, and the
results are **bit-identical**: kernel C-sim, CPU scalar and CPU AVX-512 produce the same
`results_final.bin` byte for byte (`make sim_test`).

## 1. Workload definition

Inputs are the v1 file formats, unchanged (`common/pf_types.hpp`):

| file | layout |
|---|---|
| `portfolio.bin` | `uint32 n`, then `n x Trade` (44 bytes = 11 x 4-byte words: `uid, K, T, {type,kind,pad16}, notional, position, v0, kappa, theta, sigma, rho`) |
| `market.bin` | `Market` = 7168 float32: `spot[1024], rate[1024], divq[1024], factor_w[1024][4]` |
| `results_final.bin` | `uint32 n`, then `n x {float price, float std_err}` (written with `--out`) |

The generator `build/pf_gen` is a port of v1 `gen_portfolio`. For the same arguments its output is
byte-identical to v1 (tested against the v1 `market.bin` and the v1 generator). It generates 20%
Asian and 80% European trades, calls and puts in equal shares, and Heston parameters
`v0,theta in [.01,.1]`, `kappa in [.5,4]`, `sigma in [.2,1]`, `rho in [-.9,-.1]`, `T in [.05,3]`.

The model is the v1 kernel's model. Per time step it draws 6 normals: 4 factor normals, 1
idiosyncratic normal and 1 variance normal.
`zS = sum_k W_k z_k + sqrt(1-|W|^2) z_4`, `zV = rho zS + sqrt(1-rho^2) z_5`. The log-price uses a
log-Euler step and the variance an Euler step with **reflection** `v = |v'|`, as the v1 kernel does
(default). `--scheme ft` selects full truncation, which is what the v1 CPU code used. The Asian
payoff is the arithmetic average of `S` over steps `1..n`. Payoffs are discounted with
`exp(-(r+drate) T)` and scaled by `notional*position`; `std_err` is scaled by `notional`.

The two-stage flow, with the v1 defaults as CLI defaults:

1. **Stage 1 (screening):** all `n` trades x `paths1=256` x `steps1=32`.
2. **Selection:** the top `K=10000` trades by `|price| + 2|std_err|`. Ties are broken by trade
   index, so every program selects the same set.
3. **Stage 2 (refinement):** those K trades x `paths2=131072` x `steps2=32`. The results
   replace the stage-1 values.

Paper size: `n = 1e6` (the v1 default). The logical path-steps are
`1e6*256*32 + 1e4*131072*32 = 8.19e9 + 4.19e10 = 5.01e10`, and stage 2 is 84% of the work.

The CLI is identical for `pf_fpga` and `pf_cpu` (`--help`):
`--portfolio F --market F [--out F] [--out-stage1 F] [--out-topk F] [--paths1 N --steps1 M --paths2 N --steps2 M] [--topk K] [--seed S] [--dspot --drate --volscale] [--scheme reflect|ft] [--verify [sample|full]] [--verify-n N]`.
As a fallback both programs read the v1 environment variables `PATHS_SMALL PATHS_LARGE N_STEPS TOP_K SEED D_SPOT D_RATE VOL_SCALE`.
FPGA only: `--xclbin X --device N --cus K --max-bo-mb M`. CPU only: `--impl avx512|scalar --lut auto|gather|scalar --threads N --grain N`.

RESULT keys:
- **Common:** `project=portfolio platform impl ok t_total_s t_compute_s t_read_s t_write_s t_stage1_s t_select_s t_stage2_s n_trades n_under paths1 steps1 paths2 steps2 topk seed scheme path_steps msteps_per_s checksum price_sum`, plus `verify_trades verify_mismatch t_verify_s` when `--verify` is given.
- **FPGA:** `t_xclbin_s t_alloc_s t_h2d_s t_kernel_s t_d2h_s t_accel_window_s n_cu lanes_per_cu il kernel_calls msteps_per_s_kernel bad_tags`.
- **CPU:** `threads isa grain t_setup_s`.

`checksum` is the FNV-1a 64 hash of the final `(price, std_err)` array, so equal checksums mean
equal output files. `msteps_per_s = path_steps / t_compute_s / 1e6`.

## 2. v1 diagnosis (thesis version, from its reports and sources)

| issue | evidence | consequence |
|---|---|---|
| **Interleave distance violated** | csynth: `step_loop_interleave_loop` has II=1 but **iteration latency 182** with `IL=32` and `DEPENDENCE inter false` | In RTL, slot state is read about 5 rotations before its previous update is written back. The hardware computes something other than the C model: the dynamics effectively have ~n/5 steps and interleaved stale RNG states. **The v1 FPGA prices are very likely wrong.** Check by running the v1 xclbin and v1 C-sim on the same small portfolio. C-sim cannot detect this. |
| Timing not met | HLS target 5 ns (200 MHz) on a 300 MHz platform clock. Routed WNS **-0.907 ns**, TNS -26 742 ns, 92 383 failing endpoints | Fmax about 236 MHz, so the bitstream is formally unsafe |
| Resources | 783 DSP / 55 k LUT / 71 k FF / 26 BRAM per CU; 6 CUs = **4698 DSP (79%)**, 335 k LUT (48%) | Only 6 "lanes" in total. The ICDF (BSM rational with `fdiv` and two `flog`) dominates |
| Possible layout bug | csynth `m_axi_gmem0` width **352 -> 512**: the 44-byte `Trade` struct was widened to 64-byte words, but the host writes 44-byte records | Trades i >= 1 may be read misaligned. Not verified here; check with the v1 xclbin |
| Serial REDACTED, LANES=1 | `trade_loop`/`path_block` are sequential, and `payoff_reduce` has **II=3** | Stage 1 (256 paths) pays init (34) + reduce (126) cycles per 32-path block, 15% overhead |
| RNG seeding | xorshift128 seeded with `h, h^C1, h^C2, h^C3` (correlated words). The seed uses the **batch-local** trade index | Trade *i* of every 8192-trade batch reuses the same random numbers (123 batches in stage 1). Stage 2 reuses stage-1 streams |
| Baseline mismatch | CPU: xorshift32 + Box-Muller (log/sqrt/cos), **full truncation**, scalar, OpenMP per trade. FPGA: xorshift128 + BSM ICDF, **reflection** | The two platforms did not run the same computation. `ref_bm` shows the two schemes differ by up to 10% in price for these parameters, which is far beyond MC error |
| Host round trips | Stage 1 in 8192-trade calls and stage 2 in 512-trade calls; BOs re-allocated between stages | About 140 calls, avoidable overhead |

## 3. v2 FPGA micro-architecture

```
 host BOs (HBM)                         pf_kernel (one CU, dataflow)
 trades ──gmem0 (float view)──┐
        └─gmem1 (u32 view)────┴─> read_trades ─┐                                   (II=1, sequential bursts)
 ids ─────gmem2──────────────────> read_ids ───┼─> setup ──> split ──┬─> lane 0 ─┐  (per-trade constants, II=1;
 market+params ─gmem3─> load_market (BRAM) ────┘   (fdiv, 3 fsqrt,   ├─> lane 1 ─┤   trade -> <=L jobs, round robin)
                                                    mix64 keys)      ├─>  ...   ─┤
                                                                     └─> lane L-1┘
 out <────gmem4──────────────── write_out <── collect (exact 64/96-bit moment sums, trade order) <─┘
```

**Lane** (`lane_run`): an `IL=128`-slot state RAM (288 bits per slot: xoshiro state, carried
`zS`/`zVs`, `v`, `logR`, `sumE`). One pipelined loop with II=1 runs `IL + nblk*n*IL` iterations
per job. Each iteration advances one slot by one time step:

```
 slot RAM[i] ─> (seed? fmix32 x4 : state) ─> xoshiro128** x6 ─> ICDF x6 ─> sitofp ─> factor mix ─> zS', zVs' ─┐
            └─> Heston step (sqrt, 5 fmul, 6 fadd) with stored zS,zVs ─> v', logR' ─> fixed exp ─> sumE'      ├─> RAM[i]
                                            rotation n: payoff -> q (2^-16, exact) -> sum += q, sum2 += q^2   ┘
```

- **Rotation 0** seeds and primes the IL slots. **Rotation n** of every block prices the IL paths
  and reseeds the slots for the next block, so there are no separate init/reduce loops.
- The normals for the *next* step are computed in the same iteration and stored in the slot. The
  RNG/ICDF path and the Heston path therefore run in parallel, and the read-to-write latency is
  about 90 stages (estimate). `#pragma HLS DEPENDENCE ... dependent=true distance=128` gives HLS
  the real distance, so HLS **raises II instead of silently producing wrong hardware**, which is
  exactly the v1 failure mode.
- **ICDF:** the table is indexed by `{clz(m), next 6 bits}` (2048 segments), each segment is a
  quadratic in the following 15 bits, and each normal costs 2 multipliers. All products fit in 32
  bits. The fit error is 5e-7, and the total error vs. Phi^-1 is 9.75e-7 (exhaustive), including the final
  rounding to 2^-20. The output is exact in float, the table is monotone and symmetric, and it
  reaches |z| = 6.338 at u=0. Three dual-port BRAM ROMs serve the 6 reads per lane.
- **exp:** there is no `flog`/`fexp` core. `x -> Q7.24 -> *1/ln2 -> 2^(i/256)` table, then
  `x (1 + d + d^2/2)`, then `m24 x 2^n`, exact in float, with a relative error of 6.6e-8.
- **RNG:** each path has its own stream. The 128-bit per-trade key is `mix64(seed ^ (stage<<32|trade_id))`;
  the path state is `fmix32(path ^ key_j)`; the generator is xoshiro128**. The global trade id and
  the global path index make the results independent of CUs, lanes, chunks and threads. The stage
  number is part of the key, so stage 2 uses fresh streams.
- **Exact moments:** each payoff is quantised to `q = round(min(po, 16384) * 2^16)` and summed in
  64/96-bit integers. The sums are associative, so any partition gives identical bits. The host
  finalises in double precision.
- **Trade dispatch, and why:** a lane runs one job (a trade and its path range) at a time. The
  trade constants are then lane registers, which avoids a per-slot trade-constant RAM and its
  read-modify-write hazards. Stage 1 uses 256 = 2*IL paths per trade, so there are no empty
  slots; stage 2 trades are whole 131072-path jobs. All jobs in a call cost the same, so
  round-robin dispatch is optimal. With fewer trades than lanes, the host requests
  `chunk_paths < n_paths` and each trade is split over at most L lanes. Heterogeneous slots, where
  slots carry a trade id, would only help when path counts are not multiples of IL: at most IL-1
  wasted slots per job.
- **Deadlock-free:** `collect` consumes results in dispatch order, and each lane holds at most one
  pending result of the trade being collected (nsub <= L is enforced), so `s_res` depth >= 2 is
  sufficient. All processes terminate on counts or `last` tokens, and scalars are sanitised before
  the dataflow.
- **Feed-forward stages keep up:** setup and split run at II=1, so their capacity is 1 trade per
  cycle. The lanes consume L/(IL*n + IL) trades per cycle, which is 1e-3 in stage 1.
- **Clock:** 300 MHz (3.333 ns). All float operators are Xilinx FP cores at full latency, and the
  integer paths are at most 32x32 multiplies with registers.

### 3.1 Throughput equation
`path-steps/s = f_clk x N_CU x L x eta`, where `eta = nblk*n / (nblk*n + 1 + D/IL)` (priming
rotation plus pipeline flush D of about 95 cycles per job). Stage 1: eta = 0.974; stage 2: eta > 0.999.

With 300 MHz x 4 x 8 = **9.6e9 path-steps/s**, the paper workload (5.01e10) takes about **5.3 s**
(stage 1 about 0.9 s, stage 2 about 4.4 s).

### 3.2 PCIe and host
Stage 1 moves 44 MB of trades to the device and 32 MB of moments back; stage 2 moves 0.48 MB
each way; the market is 28 KB per CU. At 12 GB/s that is under 10 ms, **0.2% of the window**.
Host finalisation and top-K selection over 1e6 trades take about 30 ms. The design is
**compute-bound**; PCIe and the host are irrelevant.

### 3.3 Where the bottleneck is
The lanes are the bottleneck: Fmax x lanes. Every feed-forward stage has more than 100x headroom.

### 3.4 Resource estimate (U50: 872k LUT, 1.74M FF, 5952 DSP, 1344 BRAM36)

These are estimates from Vitis 2023.1 FP-core characteristics at 300 MHz, **to be replaced by the
csynth numbers**.

| block | fmul | fadd/sub | fsqrt/fdiv | sitofp | int mult | DSP | LUT | FF | BRAM36 |
|---|---|---|---|---|---|---|---|---|---|
| lane (x1) | 15 | 13 | 1 / 0 | 7 | 8 hash + 12 ICDF + 4 exp + 1 q^2 | ~105 | ~14 k | ~25 k | ~10 (3 ICDF ROMs x 3 + state RAM) |
| per CU overhead (setup: 13 fmul, 7 fadd, 3 fsqrt, 1 fdiv, 4x64-bit mult; 5 m_axi; collect) | | | | | | ~85 | ~30 k | ~45 k | ~20 |
| **CU with L=8** | | | | | | **~925 (16%)** | **~142 k (16%)** | **~245 k (14%)** | **~100 (7%)** |
| **4 CUs (default)** | | | | | | **~3700 (62%)** | **~570 k (65%)** | **~980 k (56%)** | **~400 (30%)** |

Alternatives: 4 x 7 lanes = 56% DSP; 3 x 10 = 60%; 6 x 5 = 57% (more m_axi overhead). If DSP
is short, bind `fadd` to `impl=fabric`: -26 DSP / +~3 k LUT per lane.

## 4. v2 host design (`host/pf_fpga.cpp`)

- **CUs:** found by opening `pf_kernel:{pf_kernel_i}` until the open fails, capped with `--cus`.
  Under xrt_sim the default is 2.
- **Buffers, allocated once before the window:**
  - per CU: stage-1 trade BOs (one per <= `--max-bo-mb` chunk, default 1 GiB), a stage-2 trade BO,
    an ids BO, a market BO (7168+4 words) and an out BO (32 B/trade).
  - The trades BO is passed to **two** kernel arguments (float and uint32 views, same HBM bank),
    which removes all bit-casting from the kernel.
- **Read path:** `portfolio.bin` slices and `market.bin` are read **straight into `bo.map()`**.
  The scenario parameters are written into the 4 spare market words, so there are no float scalar
  arguments.
- **Stage 1:** one `std::thread` per CU runs `sync(size, 0)` on the used range, **one kernel call
  per chunk** (a single call per CU for the paper workload), then syncs the moments back.
- **Selection:** the host finalises and runs the deterministic top-K (shared code with the CPU).
- **Stage 2:** the selected records are gathered from the stage-1 BO maps into each CU's stage-2
  BO, together with the global ids, followed by **one call per CU**. The paper workload therefore
  needs exactly `2 x N_CU` kernel calls.
- **Timing:** `t_compute_s = t_accel_window_s` is one wall-clock window around stage 1, selection
  and stage 2; the CPU program uses the same boundary. `t_h2d/kernel/d2h` are summed over CU
  threads. `MARK start/end` enclose the window. The program does not change CPU pinning (the
  suite runs it under numactl).
- **Checks:** allocation failures produce a clear error. The kernel echoes each trade id (with a
  valid bit), and the host counts mismatches in `bad_tags`.
- **`--verify`:** untimed. By default it recomputes 64 stage-1 trades and 4 stage-2 trades with the
  scalar reference model (OpenMP) and requires identical integer moments; it also re-runs the
  selection. `--verify full` checks every trade, which takes about 65 s on 24 cores (scalar) for the
  paper workload.

## 5. v2 CPU design (`cpu/pf_cpu.cpp`, `cpu/pf_engine.hpp`)

- **`--impl avx512`:** 16 paths per zmm. xoshiro128** uses `vprold` and shift-add, the seed hash
  uses `vpmulld`, and the ICDF uses `vplzcntd`, `vpsllvd`, table lookup, and `vpmulld` +
  `vpsrad`. The factor mix and the Heston step use `mul/add/sqrt_ps` in exactly the kernel's
  order, and v-clamping uses mask blends. The fixed-point exp runs on 8-lane 64-bit halves
  (`vpmuldq`, `vpmuludq`, `vcvtqq2ps`).
- **Table lookup:** `--lut gather` uses `vpgatherdd`. `--lut scalar` extracts the indices and
  loads a packed 64-bit table, which is faster in the sandbox and likely on Ice Lake with the GDS
  microcode mitigation. The default `auto` times both at start-up (`t_setup_s`, untimed for
  compute), and `impl` reports the choice.
- **`--impl scalar`:** the reference model itself.
- **Threading:** OpenMP `schedule(dynamic,1)` over work items. Stage 1 items are groups of
  `4096/paths1` trades; stage 2 items are (trade, 4096-path block). Dynamic scheduling is used
  instead of the suite's `static` because Asian items cost about 2x European items on the CPU
  (exp in every step). The FPGA computes the exp for every trade, which costs it nothing extra.
- **Memory and results:** the trade buffer and result arrays are first-touched in parallel. Exact
  integer moments make the results independent of threads and grain; this is tested.
- **Build flags:** `-O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno
  -ffp-contract=off`, with no `-ffast-math`. `-ffp-contract=off` is required for bit-identity; the
  hot loop is intrinsics, so it costs nothing. There is no auto-vectorisation reliance, so no
  `-fopt-info` notes are needed.
- **Libraries:** no industry library offers this exact workload (Heston MC with a factor model and
  a custom RNG), so there is no library baseline. QuantLib would be a slower scalar double baseline
  with different numerics.

**Sandbox measurement** (2 cores, `make sim_test` section 9: 20 000 trades, stage 2 100 x 16 384
paths): AVX-512 **~227 Msteps/s on 2 threads (~115 per core)**, scalar ~63 on 2 threads (~32
per core). AVX-512 is **3.6x** faster than scalar. Using the gather path gives ~80 per core.

## 6. Fairness statement

**Identical on both sides:**
- inputs, outputs, the two-stage flow and the selection rule
- the RNG algorithm and streams, the ICDF (integer ops, same table), the exp, and the float32
  operation order
- the exact integer accumulation, and the double-precision finalisation code (shared)
- the measured window: stage 1 + selection + stage 2, with I/O excluded and reported separately

**Different:**
- The FPGA computes the exp in every step for European trades too (constant-cost hardware); the
  CPU skips it.
- The CPU picks the best lookup method at run time.
- The FPGA host does a small gather/memcpy of the selected records (FPGA-only work, inside its
  window).

**Differences from v1 semantics:**
- a different RNG and normal transform
- payoff quantisation at 2^-16: a bias of at most 1e-5 per trade, far below SE
- payoffs saturated at 16384 (S_T > ~160 S0; not reached in practice)
- global trade ids for seeding, and separate stage-2 streams

Prices agree with an independent double-precision Box-Muller implementation of the v1 model within
3 SE. In `ref_bm`, 24 trade/scheme pairs at 200k paths have a worst z of 2.39.

## 7. Build and run

```
# server
source /tools/Xilinx/Vitis/2023.1/settings64.sh; source /opt/xilinx/xrt/setup.sh
make env_check
make cpu host                 # build/pf_cpu, build/pf_gen, build/pf_fpga
make xclbin                   # csynth + v++ link (LANES=8 LOG_IL=7; cfg/link.cfg = 4 CUs)
make fpga_reports
build/pf_gen --trades 1000000 --portfolio portfolio.bin --market market.bin
build/pf_fpga --xclbin build/pf.xclbin --portfolio portfolio.bin --market market.bin --out results_final.bin --verify
OMP_PROC_BIND=close OMP_PLACES=cores numactl -N0 -m0 taskset -c 0-23 \
  build/pf_cpu --portfolio portfolio.bin --market market.bin --threads 24 --out results_final_cpu.bin
cmp results_final.bin results_final_cpu.bin      # must be identical
# sandbox
make sim_test          # all checks below
make icdf_exhaustive   # ICDF vs Phi^-1 on all 2^31 magnitudes (~3 min)
make tables            # regenerate common/pf_tables.h (ICDF ROM, exp tables)
```

`LANES`/`LOG_IL` must be the same for `make host` and `make xclbin`, because the host uses them to
choose `chunk_paths`. To change the CU count, edit `nk=` and the `sp=` lines in `cfg/link.cfg`
(4 banks per CU).

**`make sim_test` result (sandbox): 35 passed, 0 failed** (about 65 s). `make icdf_exhaustive`: max error 9.75e-7 over all 2^32 inputs (2 min 51 s).
- RNG/ICDF/exp quality:
  - ICDF max error 9.58e-7, monotone, symmetric
  - fexp relative error 6.6e-8
  - AVX-512 icdf/fexp bit-identical to scalar on 16M samples, including NaN/inf/+-0
  - 12.6M normals from the stream: var 1.0001, kurtosis 3.0007, chi2(99) = 114.6,
    adjacent-path and lag-1 correlations about 2e-4 (limit 1.4e-3), tail frequencies as expected,
    bit balance within 2.5 sigma
  - known answers for splitmix64 and xoshiro128**
- Kernel C-sim == scalar == AVX-512 (gather and scalar LUT, uneven splits): 609 checks with
  LANES=8/IL=128 and 840 checks with LANES=3/IL=16. Configurations include paths 1, 13, 129, 300,
  1000, 256, steps 1..32, split and unsplit jobs, both stages and both schemes, v0=0,
  |W|^2 > 1, uid >= n_under, kind=2, and rho = -0.99 and 0.95.
- v1 compatibility: `market.bin` byte-identical to the v1 file, portfolio byte-identical to the v1
  generator.
- The two-stage `results_final`, stage-1 results and top-K file are identical across CPU scalar,
  AVX-512 (1 and 2 threads, grain 64/4096) and the host via xrt_sim with 2 CUs, 1 CU with 47-trade
  BO chunks (8 calls), 3 CUs with full truncation, and 1 trade on 2 CUs (an idle CU plus lane
  splitting). `--verify full` reports 0 mismatches and `bad_tags=0`.
- The v1 environment-variable fallback gives the same output as the equivalent CLI.
- Prices are within 3 SE of the double-precision Box-Muller reference (worst z = 2.39).
- The host compiles against the real XRT 2.16 headers.
- Throughput: see section 5.

## 8. Synthesis risks and what to check

1. **II of `pf_lane_loop` must be 1** (csynth). Because of `dependent=true distance=128`, HLS
   raises II if the state RAM read-to-write latency exceeds 128. If II > 1, set `LOG_IL=8` (also
   in `PF_IL_PRAGMA`; stage 1 then has 1 block per trade). Report the pipeline depth in the paper.
   If HLS rejects `dependent=true distance` on a RAM, fall back to `type=inter dependent=false`
   and **manually check depth < 128**, which is what v1 failed to do.
2. **Fmax:** check the routed WNS at 3.333 ns. If timing fails:
   (a) add `config_op fadd -impl fulldsp -latency <max>`/`BIND_OP` latencies;
   (b) reduce LANES from 8 to 6, or use 3 CUs;
   (c) as a last resort, request a second 250 MHz clock (`[clock] freqHz=250000000:pf_kernel_N`).
   Throughput scales linearly with Fmax x lanes.
3. **Resource estimate (3.4) is not verified here.** Watch the DSP total (budget 70%) and SLR
   crossing: the U50 dynamic region spans 2 SLRs and the HBM is in SLR0. Consider `slr=`
   constraints if CUs in SLR1 fail timing on m_axi.
4. **IEEE conformance of the FP cores.** Bit-identity with the CPU assumes that the Xilinx FP cores
   for add/sub/mul/div/sqrt and sitofp are correctly rounded (round-to-nearest-even) and that HLS
   neither fuses mul+add nor reassociates. The source uses one float operation per statement, so
   `fp-contract=on` cannot fuse anything, and `-unsafe_math_optimizations` must stay off.
   **Run `cosim_design` on the tb (small portfolio)** and compare the result bits, or compare a
   small `pf_fpga --verify full` run on the card. Subnormal inputs never occur, except for market
   factor weights below 1e-30 (document as input restriction).
5. `hls::sqrt(float)` is assumed to map to the FP sqrt core, and `ap_fixed<32,8> = float` /
   `ap_ufixed<31,14> = float` are assumed to truncate toward -inf (the C-sim does, and HLS
   documents AP_TRN).
6. The dataflow uses arrays of `hls::stream` with conditional writes and reads (`if (rr==l)`) in
   II=1 loops, `for(;;)`/`while` loops with `last` tokens, 32 `#if`-guarded lane calls, and three
   `static const` ROMs inside the lane function (one instance per lane). Check that the csynth
   dataflow view shows L lane instances with their own ROMs.
7. Two m_axi bundles on the same BO/bank (`trades_f`, `trades_i`). XRT accepts one BO on two
   arguments connected to the same bank; confirm with the first run (`bad_tags=0`, `--verify`).
8. Several operations run once per call and are acceptable outside pipelines: 33-bit division in
   the top-level sanitiser, a 27x11-bit multiply per job in `lane_run`, and the setup pipeline's
   `fdiv` (II=1, latency ~30).
9. Kernel limits: <= 2^26 trades per call (the host chunks), <= 1024 steps, paths <= 2^32 - 256
   (the host checks).

## 9. Expected results (honest prediction)

- **FPGA:** 32 lanes x 300 MHz x ~0.99 is about **9.5e9 path-steps/s**, so the paper workload
  takes about **5.3 s**. At 250 MHz it takes 6.4 s; with 24 lanes, 7 s.
- **CPU:** per-core AVX-512 throughput is ~115 Msteps/s in the sandbox (a modern Xeon core). The
  5318Y (Ice Lake, 2.1 GHz base, more expensive gathers with the GDS mitigation) is likely
  80–110 per core. With 24 cores and 90–95% scaling (the workload is compute-bound and
  cache-friendly: per-vector state in registers, 16 KB of tables), that is **1.8–2.5e9
  path-steps/s**, or **20–28 s**.
- **Speedup: 3.5–5x** in favour of the FPGA, against v1's claimed 5.87x. The v1 CPU baseline
  was scalar and ran different numerics, and the v1 FPGA result was probably incorrect (section 2).
- **When the CPU wins or ties:** if timing closes only around 200 MHz with 6 lanes per CU (18
  lanes, 3.6e9 steps/s), the advantage shrinks to about 1.5–2x. A 2-socket server (48 cores)
  would roughly tie that configuration. The CPU advantage also grows for Asian-free portfolios on
  the CPU side, since there are fewer exps. Energy per path-step (U50 ~20–25 W vs. 165 W TDP for
  the socket) favours the FPGA by about 20–35x in every plausible case.

# Research and benchmark plan (journal version)

**Working title:** *When does a PCIe FPGA beat an AVX-512 server socket? An expert-vs-expert, end-to-end study of six workloads on the AMD Alveo U50*

**Target journal:** Journal of Parallel and Distributed Computing (JPDC, Elsevier). The subscription route is free to publish.

---

## 1. Research questions

| # | Question | Evidence in this campaign |
|---|---|---|
| RQ1 | When **both** platforms are implemented to a professional standard, for which workloads and problem sizes is the U50 faster (or more energy-efficient) than one 24-core Xeon Gold 5318Y socket? Where are the crossover points? | size sweeps, `speedup_v2.csv`, `crossover_v2.csv`, Fig. v2-1/2 |
| RQ2 | Which boundary decides the verdict: compute only, warm end-to-end job, or cold start (bitstream load)? | three boundaries per point, Fig. v2-5 (stages) |
| RQ3 | How much of the thesis-era (v1) result came from **implementation quality** rather than architecture, on each side? | v1 key points re-measured under identical conditions, `v1_vs_v2.csv`, Fig. v2-7 |
| RQ4 | Is the energy verdict the same as the time verdict? | system-level energy per unit of work, Fig. v2-6 |
| RQ5 | For pipelines (live video), does offloading one stage help when the host is the bottleneck? | pipeline vs resize-only, CPU utilisation, Fig. v2-4/8 |

**Hypotheses, written before measuring (report them as such):**

- **H1 (PCIe-bound).** AES and convolution are limited by PCIe transfers. A VAES/AES-NI and AVX-512 CPU wins at every size. The FPGA beats only the no-AES-NI T-table CPU.
- **H2 (compute-bound).** Monte Carlo Heston and the portfolio have little data to move. With lanes and loop interleaving, the FPGA wins by about 2–5× in time and more in energy. The advantage grows with problem size until the fixed startup cost is amortised.
- **H3 (host-bound).** In the live pipelines, decode and encode limit throughput. The FPGA ties the CPU in FPS and uses more energy, except possibly for the 5-rung ladder when the CPU is saturated.
- **H4 (baselines).** The v1 thesis speedups shrink or reverse once the CPU baselines are professional (v1 AES, Portfolio and Live-single). Where the v1 FPGA design was weak (MC Heston), the speedup rises.

## 2. Systems under test

| | FPGA side | CPU side |
|---|---|---|
| Hardware | Alveo U50 (xcu50), 8 GB HBM2, PCIe Gen3 x16, on the same server | 1 socket Xeon Gold 5318Y, 24 cores (0–23), SMT siblings unused, NUMA node 0 |
| Software | Vitis/Vitis HLS 2023.1, XRT 2.16 native API, Vitis Vision (resize) | GCC `-O3 -march=native`, OpenMP, AVX-512 intrinsics, OpenSSL 3, OpenCV 4 |
| Designs | `v2_projects/*/kernel` (see each README) | `v2_projects/*/cpu` (best CPU implementation reported, all reported) |
| Host code | shared with the CPU wherever possible (I/O, pipeline, timers) | same |

**Fairness rules** (see also the README of every project):

- Same algorithm, numerics and output on both sides. The kernel C-model and the CPU are bit-identical for AES, convolution, MC and Portfolio. The live-stream projects stay within ±1 grey level (Vitis Vision fixed point).
- One card is compared with one socket, with the same pinning, governor and cool-down.
- Industry libraries are included as CPU baselines where they exist (OpenSSL, OpenCV).

## 3. Design of experiments

| Project | Factor(s) and levels | Groups | CPU implementations |
|---|---|---|---|
| 01 AES | size 1, 5, 10, 50, 100, 500, 1024, 2048 MB (encrypt); decrypt 100/1024/2048; no-I/O 256/1024/4096 MB; RAM disk 100/1024 MB | enc, dec, noio, tmpfs, cold | VAES, AES-NI, OpenSSL, T-table, VAES on 1 core |
| 02 Conv | variant rgb16/rgb8/gray8 × {8K PNG end-to-end; synthetic 1080p, 4K, 8K, 8192², 8192×16384} | image, synth, cold | AVX-512, OpenCV `filter2D` |
| 03 MC | paths × steps: the 9 thesis configurations + 16.7 M×64 and 67 M×128 | main, cold | AVX-512, scalar (≤ 6e8 path-steps) |
| 04 Portfolio | stage-2 paths 32k, 65k, 131k (thesis), 262k | main, cold | AVX-512, scalar (at 131k) |
| 05 Live single | quality 240p–1080p × {pipeline with encoder, resize only}; 4-CU ablation | pipe, ro, ablation, cold | OpenCV resize in 3 thread configurations |
| 06 Live multi | 5-rung ladder × {pipeline with 5 encoders, ladder only}; FPGA batch 1, 2, 4; 2-CU ablation | pipe, ro, ablation, cold | OpenCV resize in 3 thread configurations |
| v1 key points | AES 100/2048 MB, Conv 3 images, MC 2 configurations, Portfolio, Live 240p/1080p, Live multi | main | the thesis CPU programs (+ fair live-CPU patch) |

**Protocol for every point:**

1. One profiled FPGA run (full XRT trace, excluded from statistics).
2. **5 timed repetitions.** Each repetition runs FPGA and CPU back to back, followed by an adaptive cool-down: at least 20 s, then until the FPGA is within +3 °C and the CPU within +5 °C of idle.
3. Cold probes (3 per project) load a different bitstream first.
4. The `--verify` self-check runs in repetition 1. It is untimed and placed after the measured section.

**Boundaries.** The same definition is used for FPGA and CPU:

- **compute** = the program's measured section:
  - FPGA: H2D + kernel + D2H wall-clock window.
  - CPU: the compute loop.
  - Live stream: frames per second in the processing window.
- **warm E2E** = process launch → end of one measured section: device open, cached xclbin, allocation, input read and compute.
- **cold E2E** = warm E2E plus the real bitstream load.
- **energy** = socket-0 package + DRAM (RAPL) + card rails, integrated over the measured section and divided by work units (GB, Mpix, path-steps, frames). A CPU-only-server variant, without the card, is also reported.

**Statistics:**

- Per point: median, mean, SD, CV and t-based 95% CI.
- Speedups and energy ratios: median ratio with a 95% bootstrap CI (4000 resamples).
- Any point with CV > 5% is flagged in `data_quality_v2.csv`. Re-run it before publishing.
- Crossovers: log-log interpolation of speedup = 1 along each sweep.

## 4. Threats to validity (write them into the paper)

- **HLS maturity.** The designs are HLS, not hand-written RTL. The achieved Fmax is reported from `xclbinutil`, and the per-project READMEs list the synthesis risks and fallbacks (fewer lanes, 250 MHz).
- **OpenCV build.** Ubuntu's OpenCV has no IPP (`cv_ipp` key). Either rebuild OpenCV with IPP or state this.
- **Single card, single server.** No PCIe Gen4 card was tested. Give a roofline analysis (12 GB/s) for context.
- **Power sensors.** RAPL covers socket 0 and its DRAM only. Card power comes from the XMC or XRT sensors. The idle card is charged to CPU runs in the system boundary; the no-card boundary is also given.
- **v1 Portfolio correctness.** The thesis Portfolio FPGA kernel very likely computed wrong prices: pipeline depth 182 with only 32 interleaved slots, and dependence declared false. Check it once (v1 xclbin vs v1 C-sim on a small portfolio) and report the outcome. Use only v2 numbers for claims.

## 5. Paper outline and where every item comes from

| Section | Content | Source |
|---|---|---|
| 1 Introduction | expert-vs-expert gap in the literature, contributions | — |
| 2 Related work | CPU–FPGA comparisons, baseline-quality critique | thesis ch. 2 |
| 3 Methodology | systems, fairness rules, boundaries, protocol, statistics | this file, `results/system/` |
| 4 Designs | per workload: FPGA micro-architecture + throughput equation + resources; CPU design | `v2_projects/*/README.md`, `system/v2_builds/*_reports` |
| 5 Results | 5.1 overview (Fig. v2-1); 5.2 crossover (Fig. v2-2); 5.3 AES and implementation choice (Fig. v2-3); 5.4 live pipelines (Fig. v2-4/8); 5.5 where the time goes (Fig. v2-5); 5.6 energy (Fig. v2-6); 5.7 v1 → v2 (Fig. v2-7, `v1_vs_v2.csv`) | `results/v2/tables`, `results/v2/figures` |
| 6 Discussion | taxonomy: PCIe-bound / compute-bound / host-bound; guidance for practitioners | crossover + stages tables |
| 7 Threats to validity | section 4 above | — |
| 8 Conclusion | answers to RQ1–RQ5 | — |
| Artifact | this folder (code + scripts + raw data), e.g. on Zenodo with a DOI | — |

## 6. Timeline

| Step | What | Time |
|---|---|---|
| 1 | `./campaign.sh` (in tmux) runs everything below; by hand: `./build_v2.sh`, then `./tune_v2.sh --ablation` (builds all xclbins, reads HLS/timing/utilisation reports and card measurements, selects the lane counts of 03/04 automatically, self-checks every design). Resumable after any interruption. | 1–3 days |
| 2 | `./run_all.sh --check`, then `./run_all.sh --quick`. Upload the quick zip for a parsing check. | 0.5 day |
| 3 | Full campaign `./run_all.sh` (overnight). Upload `UPLOAD_ME_*.zip`. | 1 night + |
| 4 | Analysis review, figures, manuscript draft (Phase 3). | 1–2 weeks |

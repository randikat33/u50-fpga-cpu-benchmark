# Paper 2 protocol, addendum v0.10: the RTX 4070 Ti SUPER's fp64 pipe

**Authors:** Randika T. P. G. Pathiranage, Dasuni Hewawasam (University of Moratuwa)
**Date:** 28 October 2026
**Registers:** before any run of the fp64 microbenchmark (`gpu/fp64_ubench.cu`). It goes on GitHub (this file and the code, with SHA-256) and as a new version of the Zenodo record "Paper 2: registered protocols and predictions", before the run. Nothing registered earlier changes.

**Scope.** A draft of this addendum also contained a scheduling test of four books. Its run-code check (28 October 2026, 17:12–17:15 UTC, 3 s windows, not a result) showed that the v0.9 scheduling model over-predicts the V100's time and energy for books of short options (book of 1% options, V100 alone: 19.3 ms and 4.35 J per pass against a predicted 26.6 ms and 7.30 J). That part was therefore not registered here; it will be registered separately, with the check reported, once its model inputs are re-measured. This addendum contains only the RTX part.

**Build check before registration (28 October 2026, no run).** `nvcc -O3 -arch=sm_89` compiled the microbenchmark; its SASS contains DFMA, DADD, F2F.F64.F32 and F2F.F32.F64 (40, 56, 40, 40 instructions). The SASS of the registered RTX binary (`build/hs_gpu`, SHA-256 d994d172…, as step 4A) was dumped to `results/hs_gpu_rtx.sass` and has not been analysed.

---

## The RTX card's fp64 pipe

### 1. Why

HR1 failed because the 12 settings the model binds by the fp64 pipe ran 1.67–2.06× slower than predicted. The model put every float↔double conversion (F2F) on the conversion/special-function pipe at 16 per SM per clock.

If 64-bit conversions run at the fp64 rate on this card (2 per SM per clock), the fp64-bound kernels carry about twice the fp64 load: the Asian step has 4 DADD and 4 F2F.F64.F32. That would explain the rates.

The registered pipe-energy rule also has no energy term for the fp64 pipe.

### 2. Measurements

1. **`gpu/fp64_ubench.cu`** (built with `nvcc -O3 -arch=sm_89 … -ldl`). It reports, at the locked 2,340 MHz:
   - thread operations per SM per clock and energy per operation above idle (NVML energy counter);
   - for DFMA, DADD, and float→double→float conversion pairs (F2F.F64.F32 + F2F.F32.F64, with one FADD per pair).

   Four measured repeats each, after a sizing run. The guard of v0.9 (`scripts/guard.py --once`) must report QUIET first.
2. **The SASS of the registered RTX binary** (`cuobjdump -sass build/hs_gpu`, binary SHA-256 as step 4A) for the recount.
3. **Descriptive:** the same microbenchmark on the V100 (`-arch=sm_70`, at 1,245 MHz), where conversions are expected to be fast.

### 3. Hypotheses

- **HF1 (blind; the microbenchmark has not been run).** On the RTX card:
  - the measured 64-bit conversion throughput is at most 1.25× the measured DFMA throughput (conversions run at the fp64 rate, not at 16 per SM per clock);
  - the measured DFMA throughput is 1.5–2.5 per SM per clock.
- **HF2 (a registered analysis of existing data; not blind).** With 64-bit conversions counted on the fp64 pipe at the measured DFMA rate (`scripts/recount_fp64.py`, shared variant), the time per path-step of the 12 fp64-bound settings is within 15% of the measured time at ≥ 10 of 12.
  - **Disclosed:** a preview with the V100 binary's conversion counts in place of the RTX binary's gave 12 of 12 (median 1.07, against 1.98 before). HF2 checks the counts on the RTX binary itself.
- **HF3 (blind with respect to its new inputs).** With the recount of HF2 and the pipe-energy rule extended by the measured energies per DFMA/DADD and per 64-bit conversion (the conversion loop's FADD removed at the registered fma energy, 7.12 pJ, per two conversions), the steady-state energy per option is within 30% of the measured at ≥ 30 of 36 settings (HR1's criterion).
  - **Disclosed:** the same preview gives ≥ 30 of 36 for energies per fp64-pipe operation of about 50–250 pJ, and fewer outside that range. The energies themselves come only from the microbenchmark.

**Analysis:** `scripts/recount_fp64.py --sass … --fp64-rate <DFMA> --cvt-rate <F2F64> --e-fp64-pj <DFMA pJ> --e-cvt-pj <F2F64 pJ − 3.56>`. Medians of the four measured repeats.

---

---

## 4. Code and inputs registered

**Code:** `scripts/recount_fp64.py`, `gpu/fp64_ubench.cu`; used unchanged: `gpu_counts.py`, `predict_gpu_4070tis.py`, `guard.py`. Hashes in `protocol_v0.10_code.sha256`.

**Inputs:** the v0.9 RTX analysis (`results/analysis_rtx_v09/rtx_v09.json`), the RTX prediction (`4bee4fe5…`).

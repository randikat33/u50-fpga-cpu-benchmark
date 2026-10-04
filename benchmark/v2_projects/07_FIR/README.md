# 07_FIR: int16 FIR filter (out-of-sample streaming workload)

Written for review round 6. Its time and energy on the card were **predicted** with the paper's model (Section 6.8), and the prediction was committed before the kernel ever ran on the card (`round6_new_kernels.sh`).

## Operation

- y[n] = sat16((Σ_{k<32} c[k]·x[n−k] + 2^14) >> 15).
- int16 samples, Q15 low-pass coefficients (Hamming-windowed sinc, cut-off 0.2).
- Products are exact int32 and the sum wraps modulo 2^32, so every implementation is bit-identical in any summation order (`common/fir_ref.hpp`).

## FPGA kernel (`kernel/fir_v2.cpp`)

- 256-bit words, 16 samples per cycle, 16 × 32 MACs per cycle, II = 1.
- Pipeline: rd → fir_proc → wr (dataflow).
- **2 CUs** (`cfg/link.cfg`), as for AES: one CU streams 9.6 GB/s in and out at 300 MHz, so the design is PCIe-bound by construction.
- Each input chunk carries 2 history words, so chunks are independent.

## FPGA host (`host/fir_fpga.cpp`)

- The AES host's schedule: one worker per CU (and per `--streams` buffer set), with upload, kernel and download in series per chunk.
- `--no-io`: the default, like AES.
- `--mem`: a real signal in host memory.

## CPU baseline (`cpu/fir_cpu.cpp`, `cpu/fir_avx512.hpp`)

- AVX-512, 32 outputs per iteration.
- 32 taps: one aligned load per 32 outputs, with the shifted operands built in registers (valignd / vpermt2w).
- Multiply-accumulate uses `vpdpwssd` (VNNI) or `vpmaddwd` + `vpaddd`.
- OpenMP over contiguous NUMA-local segments.

## Model inputs

- R_k = CUs × 16 × f (Gsample/s).
- b_in = b_out = 2 bytes per sample.
- Host class: streaming.

## Build and test

```bash
make sim_test               # sandbox: C-sim, CPU (VNNI == BW == scalar), xrt_sim host   (22 checks)
make cpu host               # on the server
make xclbin fpga_reports    # HLS + Vivado (6-10 h)
```

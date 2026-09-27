# v2 engineering specification (shared by all six projects)

> Note: this is the engineering specification the v2 code was written and verified against. Paths under `/home/USER/...` refer to the development sandbox (no Vitis there); `make sim_test` needs those software shims and is **not** needed on the server — use the real Vitis flow (`make xclbin`) and run every host once with `--verify`.

Goal: replace the thesis-era kernels and CPU baselines with **expert-quality ("A-grade") implementations on BOTH platforms**, so that the journal paper compares a professionally engineered FPGA design against a professionally engineered CPU design. The result may show that the FPGA or the CPU wins. Correctness and fairness matter more than a flattering number.

Target system (the user's server "SERVER"):
- Host: Lenovo SR650 V2, Intel Xeon Gold 5318Y (Ice Lake-SP, 24 cores/socket, AVX-512 F/BW/VL/DQ, VAES, VPCLMULQDQ, AES-NI, SHA-NI), DDR4, Ubuntu, GCC. CPU runs are pinned to socket 0, cores 0-23 (`numactl -N0 -m0 taskset -c 0-23`).
- Accelerator: AMD Alveo U50 (xcu50-fsvh2104-2-e (as in the v1 HLS reports), 872k LUT, 1.74M FF, 5952 DSP, 1344 BRAM36, 640 URAM, 8 GB HBM2 exposed as banks `HBM[0..31]` (the thesis designs used HBM[0..11]); no DDR. The per-bank size is not confirmed here, so **keep every single BO ≤ 1 GiB and chunk larger inputs**; the host must fail with a clear message if allocation fails). Shell xilinx_u50_gen3x16_xdma_5_202210_1, PCIe Gen3 x16 (about 12-13 GB/s per direction effective). Default kernel clock 300 MHz; a second clock can be requested per CU.
- Tools: Vitis/Vitis HLS/Vivado 2023.1, XRT 2.16 (native C++ API available), OpenCV 4.x, OpenSSL 3.x, FFmpeg libs.

The sandbox has **no Vitis and no card**. Every file must still be verified here:
- HLS kernels are C-simulated with g++ plus the shims in `/home/USER/shim` (see "Sandbox verification").
- Hosts are built against `xrt_sim` (software XRT stand-in that calls the kernel C++ top function) and run end-to-end.
- The CPU baselines are built and run natively (the sandbox CPU has AVX-512 + VAES, but only 2 cores).
- Real XRT header syntax check: `g++ -std=c++17 -fsyntax-only -I/home/USER/shim/XRT/src/runtime_src/core/include -I/home/USER/shim/xrtver host.cpp`.

## 1. Directory layout (per project, under /home/USER/v2/<NN_Name>/)

```
NN_Name/
  README.md             design document (see section 7)
  Makefile              includes ../common/fpga_flow.mk; targets below
  kernel/               HLS sources (*.cpp, *.h). Top function(s) = kernel names.
  common/               headers shared by kernel, host, CPU and tests (types, reference model, constants)
  host/                 FPGA host program (XRT native C++ API)
  cpu/                  CPU baseline(s)
  cfg/link.cfg          v++ link config: nk=, sp= (HBM banks), clock, vivado props
  test/                 C-sim testbench(es), golden vectors, run_sim_tests.sh
  scripts/              optional helpers (data generators, etc.)
```

Makefile targets (all must exist):
- `make sim_test`: sandbox verification. Builds and runs the C-sim testbench, the CPU baseline(s) and the host against `xrt_sim`, and checks the outputs. Exit code 0 on success. Paths: `SHIM ?= /home/USER/shim`.
- `make cpu`: builds the CPU baseline binaries into `build/` with the production flags (section 4).
- `make host`: builds the FPGA host into `build/` against real XRT (`$(XILINX_XRT)/include`, `-L$(XILINX_XRT)/lib -lxrt_coreutil -pthread`).
- `make csynth`, `make xo`, `make xclbin`, `make fpga_reports`: from `../common/fpga_flow.mk`.
- `make all` = `cpu host xclbin`.

## 2. Common output format (mandatory)

Use `/home/USER/v2/common/bench_common.hpp` (`bench::Report`, `bench::Timer`, `bench::Args`, `bench::AlignedBuf`, `bench::mark`). Every binary prints `RESULT key=value` lines and one `RESULT_JSON` line at the end. Mandatory keys:

`project` (aes|conv|mc_heston|portfolio|live_single|live_multi), `platform` (fpga|cpu), `impl` (short name of the implementation), `ok` (1/0 after self-check), `t_total_s` (main() entry to exit), `t_compute_s` (see the boundary definitions), and the workload parameters.

FPGA hosts also report: `t_xclbin_s` (device open plus xclbin load), `t_alloc_s`, `t_read_s`, `t_h2d_s`, `t_kernel_s`, `t_d2h_s`, `t_write_s`, `n_cu`. For those, `t_compute_s = t_h2d_s + t_kernel_s + t_d2h_s` measured as **one wall-clock window around the accelerated section**. If transfers overlap with kernel execution (async pipelines), report the window as `t_accel_window_s`, set `t_compute_s` to it, and keep the per-phase sums as additional information.

CPU baselines report `t_read_s`, `t_compute_s`, `t_write_s`, `threads`, `isa` (e.g. avx512, vaes).

Throughput keys use SI units, e.g. `throughput_gbps` (GB/s = 1e9 bytes/s), `mpix_per_s`, `msteps_per_s`, `fps`.

Print `MARK start <t>` right before the measured section and `MARK end <t>` right after it (`bench::mark`). The suite uses these marks for power integration.

CLI: `--help` prints usage. No hard-coded paths. The FPGA host takes `--xclbin PATH` (required) and `--device N` (default 0). Everything takes `--threads N` where it applies (CPU default: `omp_get_max_threads()`). `--verify` makes the program check its output against the scalar reference model in `common/` (untimed, after the measurement) and set `ok` from it. Outputs are written only when `--out` is given (or where the workload requires them), so I/O can be excluded or included explicitly.

## 3. FPGA design rules ("A-grade" checklist)

1. **Micro-architecture first.** Write the throughput equation in the README: items per clock cycle × Fmax × number of lanes/CUs. Compare it against the PCIe roofline (about 12 GB/s per direction) and say which one limits the design.
2. Critical loops reach **II=1 at the target clock** (300 MHz unless justified). Recurrences with a long update path use **loop interleaving / slot rotation** (state in a shift register with a dependency distance ≥ the update latency) or retiming, **never II>1**.
3. **Spatial parallelism.** Replicate datapaths inside one CU (lanes) when the per-CU control overhead and the m_axi adapters would otherwise dominate. Use multiple CUs only where they help (independent memory banks, host-side parallelism). The lane or CU count is a `#define` / Makefile variable with a documented resource estimate per lane (LUT/FF/DSP/BRAM). Budget at most about 70% of LUT/DSP and 60% of BRAM so timing closes.
4. **Dataflow** with clean read → compute → write stages, `hls::stream` FIFOs with explicit depths, no feedback between processes. Fixed trip counts where possible. Streams must be fully consumed; no deadlocks for any valid size.
5. **m_axi**: sequential access only in the read and write stages (burst inference). `max_read_burst_length=256 max_write_burst_length=256`, `num_read_outstanding/num_write_outstanding=16`, `latency=0` or `auto`, data width 512 where the payload is packed, one bundle per logical buffer, each bundle mapped to its own HBM bank in `link.cfg`. `offset=slave`. Control via `s_axilite bundle=control`. The host must pass buffer sizes and use them.
6. **Arithmetic**: minimum widths (`ap_uint<>`/`ap_int<>`/`ap_fixed<>`) in integer datapaths. Floating point only where the algorithm needs it, with the same IEEE precision on the CPU so results are comparable. Constant multiplies become shift-add. No `%` or `/` by a non-power-of-two constant inside II=1 loops. Use `BIND_OP ... impl=dsp latency=` / `BIND_STORAGE` where it helps timing.
7. **Arrays/ROMs**: `ARRAY_PARTITION` / `ARRAY_RESHAPE` only where parallel access needs it. ROM tables are `static const`. Line buffers go to BRAM/URAM with `DEPENDENCE inter false` only when provably safe.
8. No dynamic memory, no recursion, no `std::` containers in kernels. `static` state only for line buffers/ROMs.
9. The kernel must be **bit-exact against the scalar reference model** in `common/` in C-simulation, for several sizes including edge cases (sizes not divisible by the word/lane width, minimum sizes).
10. Pragmas carry a one-line comment saying why they are there.

## 4. CPU baseline rules ("A-grade")

1. The **same algorithm and numerics** as the FPGA (same RNG algorithm, same normal transform, same precision, same filter definitions and borders). Outputs are compared (bit-exact where the design allows it, otherwise statistically with a stated tolerance).
2. **Multithreading**: OpenMP (`schedule(static)`), threads = 24, `OMP_PROC_BIND=close OMP_PLACES=cores` (the suite sets these). Buffers are allocated with `bench::AlignedBuf` and **first-touched in parallel** with the same schedule as the compute loop (NUMA).
3. **Vectorization**: hand-written AVX-512 intrinsics (`immintrin.h`) for the hot loop, guarded by `#if defined(__AVX512F__)`, with an AVX2 or scalar fallback. The build uses `-O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno` (no `-ffast-math` where it would change results; document it if used). Provide `-fopt-info-vec-missed` build notes where auto-vectorization is relied upon.
4. Where an **industry library** exists, also provide it as a second baseline (OpenSSL EVP for AES, OpenCV `filter2D` with IPP for convolution, OpenCV `resize` with IPP for video). The paper reports the best CPU number.
5. No artificial work on the CPU path that the FPGA path does not do (for example no pack/unpack into FPGA word formats).
6. Same I/O code and file formats as the host program. I/O time is reported separately.

## 5. Host program rules ("A-grade")

1. XRT **native C++ API** (`xrt::device`, `xrt::kernel`, `xrt::run`, `xrt::bo`). No OpenCL.
2. Buffers are allocated **once** and reused. Input files are read **directly into `bo.map()` memory** (no extra memcpy). Only the used byte range is synced (`bo.sync(dir, size, offset)`).
3. Several CUs/lanes are driven **concurrently** (one host thread per CU, or async `xrt::run` with overlapping H2D/D2H), with chunking for large inputs.
4. Host threads are pinned to the CPU socket that is local to the card (the suite does this with numactl; the program must not override it).
5. Set `XRT_SIM` builds via `-DXRT_SIM` only where unavoidable; the host code itself should compile unchanged against real XRT and `xrt_sim` (include `<xrt/xrt_device.h>`, `<xrt/xrt_kernel.h>`, `<xrt/xrt_bo.h>`). In the `xrt_sim` build, the test Makefile adds a small `test/sim_register.cpp` that registers the kernel top with `XRT_SIM_KERNEL(name, nargs){...}` (see `/home/USER/shim/xrt_sim/xrt/xrt_sim.h`: `a[i].as<T>()` gives the device pointer of a buffer arg, `a[i].scalar` gives a scalar).
6. Kernel names: host opens `"<top>:{<top>_<i>}"` for CU i (1-based), matching `nk=<top>:<N>:<top>_1.<top>_2...` in `link.cfg`. The CU count is detected by trying to open CUs until one fails (xrt_sim: set via `--cus N`, default taken from the build).

## 6. Sandbox verification

```
SHIM=/home/USER/shim
HLS_INC = -I$(SHIM)/include -I$(SHIM)/HLS_arbitrary_Precision_Types/include        # ap_int, hls_stream, hls_math shims
VISION_INC = -I$(SHIM)/Vitis_Libraries/vision/L1/include                         # Vitis Vision (2023.1 headers)
XRTSIM_INC = -I$(SHIM)/xrt_sim                                                    # software XRT
COMMON_INC = -I/home/USER/v2/common
C-sim build: g++ -std=c++17 -O2 -w $(HLS_INC) ... (use -w: ap_int headers are noisy)
```
- `hls_math.h` is a thin shim (maps to `<cmath>`). If you use other HLS headers (`hls_vector.h`, `ap_shift_reg.h`, ...), write the minimal shim into `test/shim/` of your project instead of touching the shared directory.
- The HLS `#pragma`s are ignored by g++, so **you must reason carefully about synthesizability** (static sizes, no pointers-to-pointers on interfaces, dataflow canonical form, no multiple readers/writers on a stream, loop bounds). Keep a "synthesis risk" list in the README with anything you could not verify.
- The sandbox has 2 cores. Use small workloads in `sim_test`.

## 7. README.md content (per project)

1. Workload definition (inputs, outputs, parameters, and the sizes used in the paper).
2. v1 diagnosis (what limited the thesis version, with numbers from its HLS/timing reports).
3. v2 FPGA micro-architecture: block diagram (ASCII), throughput equation, the expected bottleneck (compute vs PCIe vs host), a resource estimate per lane/CU, and the target clock.
4. v2 host design: threads, buffers, transfers, chunking.
5. v2 CPU design(s): vectorization, threading, library baselines.
6. Fairness statement: what is identical on both sides and what differs.
7. Build and run commands (server) and the `make sim_test` result from the sandbox.
8. Synthesis-risk list and what to check in the reports (II, Fmax, utilization), and what to change if timing or resources fail (for example "reduce LANES from 8 to 6").
9. Expected results: an honest prediction with the reasoning, including the case where the CPU wins.

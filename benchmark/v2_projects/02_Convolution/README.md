# 02_Convolution (v2): 3×3 sharpen / edge / blur, Alveo U50 vs Xeon Gold 5318Y

This project replaces the thesis (v1) kernels, hosts and CPU baselines with an FPGA design and a CPU design that are both engineered to a professional standard. Both platforms compute **bit-identical** outputs (FNV-1a checksums are printed by every binary), so the comparison is exact.

## 1. Workload

| item | definition |
|---|---|
| input | one image, H×W, C channels, 8 or 16 bits per sample. Samples are interleaved B,G,R (the OpenCV order) and 16-bit samples are little-endian in memory. |
| variants | `rgb16` (C=3, 16-bit), `rgb8` (C=3, 8-bit), `gray8` (C=1, 8-bit). v1's "BW" variant is `gray8`: 8-bit single channel, `gray_8bit_8k.png`. |
| outputs | three images of the same format: **sharpen** `[0,-1,0;-1,5,-1;0,-1,0]`, **edge** `[-1×8, 8 centre]`, **blur** `([1,2,1;2,4,2;1,2,1]) >> 4` (floor). |
| numerics | integer arithmetic, clamped to `[0, 2^bits-1]`, computed per channel. Pixels in a 1-pixel border are 0. Images smaller than 3×3 give all-zero outputs. The definition lives in `common/conv_ref.hpp` and matches the v1 kernels and CPU hosts. |
| input conversion (same code on both sides, `common/img_io.hpp`) | alpha is stripped (v1: BGRA2BGR). For colour variants, gray input is expanded to BGR. For `gray8`, colour input is converted with 0.299/0.587/0.114 (libpng fixed point; this may differ from `cv::cvtColor` by ±1, but the paper images are already gray). A PNG whose depth does not match the variant is rejected (as in v1). |
| paper sizes | the v1 8K images are **8192×4320** (35.39 MP; see the v1 logs `Input: 8192x4320`). RGB16: 212.3 MB in, 637 MB out. RGB8: 106.2 MB in, 318.5 MB out. Gray8: 35.4 MB in, 106.2 MB out. (A 7680×4320 frame would be 199/597 MB for RGB16.) Recommended sweep: 1080p, 4K and 8K for each variant, plus `--synthetic` inputs for sizes that have no file. |

## 2. v1 diagnosis (from the thesis reports and logs)

- **1 pixel per clock.** Each 512-bit word carries 10 pixels (RGB16) or 64 pixels (BW), but the `Pixel` loop (II=1) consumed one pixel per cycle. The RGB8 kernel used 256-bit words holding 10 pixels (30 of 32 bytes).
- **`%` and `/` on `int` inside the II=1 loop** (`x % PIXELS_PER_WORD`, `out_col / PPW`), plus conditional indexed writes to 3 m_axi ports. As a result, the iteration latency was **107 cycles** (RGB) and **74 cycles** (BW). All HLS modules were flagged "Timing, slack -0.00". The random-offset writes (`out[local_row*wpr + out_wx]`) are a burst-inference risk.
- **Timing closure.** The BW xclbin failed: routed WNS **-0.432 ns**, TNS -814 ns, 6894 failing endpoints. RGB8 met timing with WNS +0.045 ns.
- **Resources (HLS).** RGB16 used 83 k LUT and 183 BRAM (per-channel line buffers, 3 separate pixel planes), RGB8 45 k LUT, BW 40 k LUT.
- **Host.** Five CUs, each given a **full copy of the image** (5× the H2D volume, 113 MB each for RGB8). The input was packed per pixel on the host. OpenCL was used, and the xclbin was reloaded per variant (about 9.6 s each). v1 measured H2D 78/229/166 ms, kernel span 42/76/105 ms and D2H 47/114/87 ms for BW/RGB8/RGB16. PCIe accounted for 71–82 % of the accelerated time.
- **CPU baseline.** A naive scalar OpenMP loop (`collapse(2)` per pixel for RGB16) took 49.6/138.9/156 ms. The v1 **CPU BW run converted gray to 3-channel BGR** (log: `channels_in=1 channels_used=3`), so it did 3× the FPGA's work, which makes the comparison unfair.
- **End-to-end.** PNG decode (0.45–2.6 s) and PNG encode (3.2–12 s) dominated end-to-end time on both platforms.

## 3. v2 FPGA micro-architecture

```
             s_axilite: width, rows, in_words, out_words
                    |
                cfg_proc ──cfg──┬──────────────┬──────────┬──────────┐
                                v              v          v          v
 HBM[i]   ──m_axi──> reader ─word─> stencil ─word─> writer ─m_axi─> HBM[i+1] sharpen
 (512b bursts 256)      (FIFO 32)  (II=1)    ├word─> writer ─m_axi─> HBM[i+2] edge
                                             └word─> writer ─m_axi─> HBM[i+3] blur
 stencil:  lb_top[S], lb_mid[S]  (URAM, whole 512-bit words, S = words/row ≤ 768)
           cur word k-1 (3 rows × L samples, registers) + tail of word k-2 (3×C samples)
           + look-ahead word k (3 rows) -> L samples of sharpen/edge/blur per clock
```

**Key idea: sample-level stencil on the raw row stream (no packing, no gearbox).** Channels are interleaved, so the horizontal neighbour of sample *i* is sample *i±C*, and the vertical neighbours are at index *i* in the rows above and below. The kernel therefore takes the decoded row bytes as they are. Each row is padded to a multiple of 64 bytes, and every clock processes a whole word:

| variant | samples/word L | pixels per clock | kernel rate at 300 MHz | 8K frame | bytes in / out per s |
|---|---|---|---|---|---|
| gray8 | 64 | 64 | 19.2 Gpx/s | 1.8 ms | 19.2 / 57.6 GB/s |
| rgb8 | 64 | 21.3 | 6.4 Gpx/s | 5.5 ms | 19.2 / 57.6 GB/s |
| rgb16 | 32 | 10.7 | 3.2 Gpx/s | 11 ms | 19.2 / 57.6 GB/s |

This improves on the proposed 10/21/64 px per word for RGB16 and RGB8. With the raw row stream the host does **zero packing** (`t_pack_s = 0`), and the decoder writes straight into buffer-object memory.

**How the stencil loop works:**
- **Look-ahead.** Output word *k* is emitted when word *k+1* arrives, because its right neighbours need C samples of the next word. The left neighbours come from the registered tail of word *k-1*.
- **Flush.** One flush iteration per row emits the last word. Its look-ahead samples are provably border or padding, so they are don't-care.
- **Masking.** Border and padding samples are masked to 0 by a per-word limit `lim = clip(C·(W-1) - k·L, 0, L)`. That limit is maintained by a subtractor, so there is no multiply, `%` or `/` in the loop.
- **Fixed trip counts.** The loop runs `rows × (max(S,4)+1)` iterations; the reader runs `rows·S`, and each writer runs `(rows-2)·S`. All are computed once in `cfg_proc`. Invalid sizes set every count to 0, so the kernel does nothing and cannot hang.
- **Line-buffer safety.** The line-buffer write is delayed by one iteration (address *t-1*), so a read and a write never target the same address in the same cycle, and URAM collision semantics do not matter. Two accesses to the same address are at least 4 iterations apart (`CONV_MIN_ITERS`), which makes `DEPENDENCE inter false` safe for a read-to-write stage offset below 4.
- **Arithmetic.** It is all shift-add at minimum width: B+2-bit sums and a (B+4)-bit signed result. The clamp is a sign-bit and overflow-bit test, and blur needs no clamp. No DSPs are used in the loop.
- **One source.** `kernel/conv3_core.hpp` holds the templates and `kernel/conv3_top.inc` the top body. The tops are `conv3_rgb16`, `conv3_rgb8` and `conv3_gray8`, all linked into **one xclbin**, so there is no bitstream swap between variants.

**PCIe roofline versus compute (8K 8192×4320, Gen3 x16 at about 12 GB/s per direction):**

| variant | H2D | D2H | serial H2D+kernel+D2H | with H2D/D2H overlap (strips) | kernel (1 CU) |
|---|---|---|---|---|---|
| rgb16 | 212 MB → 18 ms | 637 MB → 53 ms | ~82 ms | ~55–65 ms | 11 ms |
| rgb8 | 106 MB → 9 ms | 319 MB → 27 ms | ~41 ms | ~28–33 ms | 5.5 ms |
| gray8 | 35 MB → 3 ms | 106 MB → 9 ms | ~14 ms | ~10–12 ms | 1.8 ms |

One CU consumes 19.2 GB/s and produces 57.6 GB/s, which is 1.6× / 4.8× the PCIe roofline. **The design is therefore PCIe-bound (mainly D2H, which is 3× the input), and more CUs cannot help the transfers.** Decision: **1 CU per variant.** The host splits the image into strips (default 64 MB of input per strip) and runs a 3-stage pipeline, so the D2H of strip *k* overlaps the H2D of strip *k+1*.

The only part of the path that is not known exactly is the U50's HBM AXI throughput per port. If `t_kernel_s` is a significant fraction of the window on the server, build the ablation `make xclbin NCU=2` (`cfg/link_2cu.cfg`, 24 HBM banks); the host detects the CUs automatically.

**Resource estimate per CU** (hand estimate, to be replaced by `make fpga_reports`):

| block | LUT | FF | BRAM36 | URAM | DSP |
|---|---|---|---|---|---|
| stencil rgb8 / gray8 (64 samples × ~150 LUT) | ~10 k | ~12 k | 0 | 4 | 0 |
| stencil rgb16 (32 samples × ~260 LUT) | ~8.5 k | ~11 k | 0 | 4 | 0 |
| 4 × m_axi adapters (512-bit, burst 256) | ~8 k | ~10 k | ~8–12 | 0 | 0 |
| 4 × 512-bit SRL FIFOs (depth 32) + cfg | ~3 k | ~2 k | 0 | 0 | ≤4 |
| **per CU** | **~20–22 k (2.5 %)** | **~25 k (1.5 %)** | **~10 (0.8 %)** | **4** | **≤4** |

For all three CUs that is about 65 k LUT (7 %), 75 k FF, about 30 BRAM, 12 URAM and at most 12 DSP, well inside the 70 % / 60 % budget. v1 used 83 k LUT for RGB16 alone. **Target clock: 300 MHz** (3.333 ns, 15 % uncertainty in HLS). The longest arithmetic path is a 20-bit three-operand add plus the clamp (about 1.5 ns), so HLS should estimate roughly 2.2–2.6 ns.

## 4. v2 host (`host/conv_fpga.cpp`)

- **API.** XRT native C++ (`xrt::device`, `xrt::kernel`, `xrt::run`, `xrt::bo`). CUs are detected by opening `conv3_<v>:{conv3_<v>_i}` until the open fails (the xrt_sim build uses `--cus`, default 2). Each CU gets one reused `xrt::run`.
- **Planning and allocation.** The PNG header is read first, then the strips are planned (`common/conv_plan.hpp`): output rows [a,b) take input rows [a-1,b+1). Each strip's BOs (1 input, 3 outputs) are allocated once, on its CU's `group_id`, with **each BO ≤ `--max-bo-mb`** (1 GiB by default). A failed allocation produces a clear error.
- **Decoding.** libpng decodes **each row directly into its input BO**, at an offset of y·stride. The two overlap rows at each strip boundary are memcpy'd and the padding is zeroed. There is no per-pixel packing.
- **Accelerated window.** The pipeline has H2D threads (`--h2d-threads`, default 1), one run thread per CU, and a pool of D2H threads (`--d2h-threads`, default 3, one per output). Syncs cover only the used size. The threads are created before `MARK start`.
  - `t_compute_s` = `t_accel_window_s` = the wall-clock window, averaged over `--repeat` runs.
  - `t_h2d_s`, `t_kernel_s` and `t_d2h_s` are per-phase sums for information; `t_serial_sum_s` is their total.
- **Outputs.** Rows are read in place from the output BO memory through row pointers; rows 0 and H-1 point to a static zero row. The three PNGs (or raw files) are written in parallel. Checksums are computed after `t_write_s` and reported as `t_cksum_s`. `--verify` compares every row with `conv_ref.hpp` (untimed).
- **Limits.** Widths above 8192 are rejected (the line-buffer depth is set at synthesis).

## 5. v2 CPU baselines (`cpu/conv_cpu.cpp`)

- **`--impl avx512`** (`cpu/conv_avx512.hpp`). Hand-written AVX-512BW that computes **all three filters in one pass per row**.
  - 8-bit data runs 32 int16 lanes: `max(x,0)` followed by `vpmovuswb` gives an exact clamp. 16-bit data runs 16 int32 lanes with `vpmovusdw`.
  - Tails shorter than one vector use scalar code. Rows run in parallel under OpenMP `schedule(static)`.
  - Output buffers are `bench::AlignedBuf`, first-touched in parallel with the same schedule. It is bit-exact with the reference; a scalar fallback is used without AVX-512BW.
- **`--impl opencv`**: `cv::filter2D` for sharpen and edge (with an integer-valued kernel, exact in float). For blur, the unnormalised sum goes to CV_16S (8-bit) or CV_32F (16-bit), then `convertTo(scale 1/16, offset -7.5/16)`. That identity reproduces `floor(sum/16)` exactly, because every intermediate is exactly representable and the rounding margin is ±0.47.
  - **Parallelism:** image bands are processed in parallel with OpenMP (`--bands-per-thread 4`). The bands are ROIs, so filter2D reads the real neighbour rows. `cv::setNumThreads(1)` is set because filter2D is single-threaded internally.
  - **Borders:** they use `BORDER_REPLICATE` (the mode IPP accelerates); border outputs are then zeroed. The result is **bit-exact** (checked in `sim_test`).
  - `cv_ipp=0/1` is reported. **Ubuntu's `libopencv-dev` is built without IPP** (the sandbox shows `useIPP=0`). For the IPP number, build OpenCV 4.x from source with `-DWITH_IPP=ON` and report `cv_ipp=1`.
- **`--impl ref`**: the scalar reference, row-parallel. With `--threads 1` it is the pure scalar number.
- **Common to all three.** They use the same libpng/raw/synthetic I/O, checksums and PNG writer as the host. There is no packing work on the CPU path. `make vec_report` writes `-fopt-info-vec-missed` notes for the scalar path.

## 6. Fairness

- **Identical on both sides:** the filter definitions, integer numerics, borders and clamps (checked by checksums); the input decoder and conversions; the PNG encoder (libpng level 1, SUB filter, three outputs written in parallel); and the timing boundaries.
  - Read, write and checksum are timed separately from compute.
  - FPGA compute = the H2D + kernel + D2H window. CPU compute = the filter loop into pre-faulted buffers.
- **What differs:** the FPGA path pays for PCIe and bank memory. The CPU path pays nothing for transfers and gets 24 cores and DDR4 memory bandwidth. v1's unfairness (a 3-channel CPU run for BW, and a naive CPU loop) is removed.
- **What to report:** the best CPU number (normally avx512).

## 7. Build and run

```bash
# server
source /tools/Xilinx/Vitis/2023.1/settings64.sh; source /opt/xilinx/xrt/setup.sh
make env_check && make all                 # cpu + host + xclbin (HLS ~10 min each, link 1-3 h)
make fpga_reports                          # HLS/timing/utilisation into build/reports
X=build/conv3.xclbin; IMG=/home/USER/Desktop/Vitis_2023_2D_Convolution_Project_Color_2
numactl -N0 -m0 build/conv_fpga --xclbin $X --variant rgb16 --in $IMG/rgb_16bit_8k.png --repeat 5 --verify
numactl -N0 -m0 build/conv_fpga --xclbin $X --variant rgb8  --in $IMG/rgb_8bit_8k.png  --repeat 5 --out-prefix out/fpga_rgb8
numactl -N0 -m0 build/conv_fpga --xclbin $X --variant gray8 --in $IMG/gray_8bit_8k.png --repeat 5
OMP_PROC_BIND=close OMP_PLACES=cores numactl -N0 -m0 taskset -c 0-23 \
  build/conv_cpu --impl avx512 --variant rgb16 --in $IMG/rgb_16bit_8k.png --repeat 5 --threads 24
#   same for --impl opencv / ref and the other variants; --synthetic 8192x4320 needs no file
# ablations: --strip-mb 0 (one strip, no overlap), --strip-mb 16, --d2h-threads 1, make xclbin NCU=2
make sim_test                              # sandbox verification (needs no Vitis/card)
```

**CLI (both binaries):** `--variant rgb16|rgb8|gray8 --in IMAGE (.png or raw with --width --height) | --synthetic WxH [--out-prefix P] [--out-format png|raw] [--png-level N] [--repeat N] [--threads N] [--verify]`.
- The host adds: `--xclbin X [--device N] [--cus N] [--strip-mb M] [--strip-rows R] [--max-bo-mb M] [--h2d-threads N] [--d2h-threads N]`.
- The CPU binary adds: `--impl avx512|opencv|ref [--bands-per-thread N]`.

**RESULT keys:**
- **Common:** `project=conv platform impl variant width height channels bits mpix repeat bytes_in bytes_out t_read_s t_compute_s t_compute_min_s t_write_s t_cksum_s [t_verify_s verify verify_bad_rows] t_total_s mpix_per_s cks_sharpen cks_edge cks_blur ok`.
- **FPGA:** `n_cu n_strips t_xclbin_s t_alloc_s t_pack_s(=0) t_h2d_s t_kernel_s t_d2h_s t_accel_window_s t_serial_sum_s mpix_per_s_kernel h2d_gbps d2h_gbps`.
- **CPU:** `isa threads t_alloc_s [cv_ipp cv_version cv_bands]`.

**`make sim_test` in the sandbox (2 cores, no Vitis): 17 test groups passed, 0 failed.**
- **Kernel C-sim.** 270 cases, bit-exact for all 3 variants. Sizes cover 3×3, 1×3, 2×5, widths 10, 11, 21, 22, 31–33 and 63–65, 37×19, 257×131, 1000×9, the maximum width of 8192 (and 8191), and 1280×1024 / 1100×1000. Inputs are random, saturated, checkerboard and synthetic, with garbage in the row padding. The cases also cover multi-strip runs with 1, 7, 13 and 48 rows per strip, back-to-back calls, and 5 invalid-parameter cases that must do nothing.
- **CPU implementations.** ref, avx512 and opencv each ran 6 sizes × 1 and 2 threads per variant, all passing `--verify` with identical checksums.
- **Host end-to-end via xrt_sim.** 6 sizes × 5 strip/CU/BO-cap configurations per variant, with `--verify` and checksums equal to the CPU. The strip plan, the width limit and the missing-xclbin error were also checked.
- **I/O.** PNG input, raw input and synthetic input give the same checksums, and the host and CPU PNG files are byte-identical. OpenCV/numpy cross-checks cover the BGR order, 16-bit endianness, BGRA alpha stripping, and an independent numpy model of sharpen and blur.
- **Real XRT.** `conv_fpga.cpp` passes the XRT 2.16 header syntax check.
- **Synthetic 8K (8192×4320), sandbox with 2 threads.** These numbers are not from the server:

| variant | avx512 | opencv (no IPP) | ref |
|---|---|---|---|
| rgb16 | 55 ms (638 Mpx/s) | 800 ms | 342 ms |
| rgb8 | 30 ms (1185 Mpx/s) | 150 ms | 304 ms |
| gray8 | 7.2 ms (4850 Mpx/s) | 50 ms | 122 ms |

  The 8K gray8 image also ran through the host plus the xrt_sim C-sim kernel (2 strips, 2 CUs) with a matching checksum. Sandbox 8K rgb16 PNG I/O took 1.0 s to decode and 6.5 s to encode the three outputs.

## 8. Synthesis risks and what to check

1. **II / schedule of `stencil`.** Check that II=1 is met and inspect the RAM read and write states. The delayed write must land fewer than `CONV_MIN_ITERS` (4) stages after the read. If it does not, raise `CONV_MIN_ITERS` in `common/conv_params.hpp` (cost: idle iterations only for rows shorter than that many words).
2. **High fan-out enables.** `rd` drives about 3·L·B sample registers and `emit` drives 1536 output bits plus 3 stream writes. If Fmax is short, try `#pragma HLS PIPELINE II=1 style=flp`, or use a `config_compile -pipeline_style` choice. Alternatively, drop the target for that CU only (`PERIOD_conv3_rgb8=3.6` plus the matching `freqHz`).
3. **Constant-index muxes.** Neighbour selection (`jl < 0 ? pt[..] : cur[..]`) must fold to wires after unrolling. Check the LUT count of `stencil`: it should be around 10 k, not around 50 k.
4. **Burst inference.** Look for "Inferred burst of variable length" on all four ports (HLS 214-xxx messages). Access is sequential with a trip count computed in `cfg_proc`.
5. **URAM mapping.** Line buffers are `ram_s2p impl=uram latency=2` on non-static local arrays. If HLS rejects this or maps to FFs, make them `static` (safe in hardware; C-sim re-entrancy only matters for xrt_sim) or use `impl=bram`, which costs about 8 BRAM36 per buffer for rgb8.
6. **512-bit SRL FIFOs** (`BIND_STORAGE type=fifo impl=srl`). This is fine in 2023.1. If it is rejected, drop the pragma and let HLS choose.
7. **cfg_proc multiplies** (32×11 bits) become DSPs or LUT multipliers; they run once per call, outside any loop.
8. **link.cfg syntax.** Check the `[clock] freqHz=300000000:<cu>.ap_clk` and `slr=` lines. If v++ rejects the clock lines, delete the `[clock]` section: the platform default kernel clock is already 300 MHz. `sp=` uses the C argument names `img_in`, `out_sharpen`, `out_edge` and `out_blur`.
9. **C++ tops without `extern "C"`** are supported in 2023.1. Confirm the kernel names with `xclbinutil --info`.
10. **HBM bank sizes are unconfirmed.** The host keeps each BO ≤ 1 GiB and splits into strips. rgb16 8K needs 212 + 3×637 MB in total, or about 53 + 3×159 MB per strip at the default strip size.
11. **Host-side unknowns.** It is not known whether XDMA scales with concurrent syncs (v1 saw 12.6 GB/s for a single transfer, but about 7.4 GB/s aggregate over 5 first-touch transfers). Tune with `--h2d-threads`, `--d2h-threads` and `--strip-mb`, and report the chosen values.
12. **Timing or resource failures.** If timing fails at 300 MHz, the most likely fixes are (in order): `ExtraNetDelay_high` placement is already set; then `Performance_ExplorePostRoutePhysOpt`; then 250 MHz on the failing CU, which still exceeds PCIe by more than 2×, so the end-to-end throughput does not change. If resources fail (not expected), drop a variant from the xclbin.

Report checklist: stencil II and iteration latency (expected 4–7), estimated clock, per-module LUT/FF/URAM, routed WNS/TNS, and kernel utilisation per CU.

## 9. Expected results (honest prediction)

- **FPGA compute window (8K):** about 55–70 ms for rgb16 (roughly 500–650 Mpx/s), 28–35 ms for rgb8 and 10–12 ms for gray8. This is set by PCIe D2H. `t_kernel_s` should be only 2–12 ms (strip overhead plus HBM AXI), compared with v1's windows of about 250–340 ms. The expected improvement over v1 is about 4–5×.
- **CPU compute (24 cores, avx512, 8K):** the sandbox measures about 640 Mpx/s per 2 threads for rgb16. Scaling is limited by DDR4 memory bandwidth (the output stream is 3× the input), so expect about 5–10 ms for rgb16, 3–5 ms for rgb8 and 1–2 ms for gray8. That is 15–30× faster than v1's naive loop.
- **Therefore, on compute only, the CPU is expected to win by about 5–10×.** The FPGA's raw kernel (1.8–11 ms) is competitive with a 24-core AVX-512 socket, but it cannot beat the PCIe copy of 3 output frames. Energy per frame can still favour the FPGA only if the card's incremental power is compared against a socket at full load; the host CPU is mostly idle during the window. The paper should present both the kernel-only and the window numbers.
- **End-to-end:** PNG decode (about 1–2.6 s) and encode (about 6–12 s for three 16-bit 8K outputs) dominate on both platforms. After the one-time xclbin load (about 10 s, reported separately as `t_xclbin_s`), end-to-end is **equal to within a few percent**. Use raw input/output (`--out-format raw`) to show the I/O-free case.
- **Where the FPGA could win:** streaming video without PCIe round trips (for example camera-in/NIC-out, or output kept on the card for a downstream kernel), larger stencils (5×5 or 7×7, where CPU cost grows with the kernel area while the FPGA stays at 1 word per clock), or a Gen4/CXL card.

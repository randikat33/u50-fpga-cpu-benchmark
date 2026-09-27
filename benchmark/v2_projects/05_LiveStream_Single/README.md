# 05 LiveStream Single (v2): 4K live stream → one output quality

The FPGA and CPU programs share one pipeline source, so the resize stage is the only difference between them. Each program can run the full live pipeline or the resize stage on its own.

## 1. Workload

| Item | Value |
|---|---|
| Input | H.264 4K video (3840×2160, 8-bit BGR after decode). The paper uses the v1 file `5.mp4`. |
| Output (`--quality`) | 0 = 432×240, 1 = 640×360, 2 = 856×480, 3 = 1280×720, 4 = 1920×1080. All widths are divisible by 8. |
| Interpolation | Bilinear: `xf::cv::resize` on the FPGA, `cv::resize(INTER_LINEAR)` on the CPU |
| Pipeline mode | decode (OpenCV/FFmpeg) → resize → frame-order restore → sink |
| Sinks | `null` (no encoder), `file` (ffmpeg libx264 ultrafast/zerolatency → .mp4), `raw`, `rtmp` (the v1 behaviour, with audio mux) |
| Resize-only mode | S decoded frames are placed in the buffers once, then the resize stage loops (`--iters`) |
| Paper sweep | 5 qualities × {pipeline with sink `file`, resize-only}, fixed frame count, 5 repetitions |

Both platforms use the same output sizes. v1 did not: its CPU cropped 240p to 426 wide.

## 2. v1 diagnosis (from the thesis build reports)

**Timing and resources**
- Timing failed on the kernel clock: WNS −2.12 ns (83k failing endpoints). The HBM clock also failed (−0.73 ns), because of routing congestion.
- Each of the 6 CUs used 47k LUT, 68 BRAM, 87 URAM and 88 DSP. Together they used 82% of all URAM and 40% of LUT, which caused the congestion.

**Kernel design**
- The input gearbox inserted 256-bit words at a *variable* bit offset, which is a 512-bit barrel shifter, and shifted a 512-bit register every cycle.
- Its m_axi reads and writes sat inside the pixel loop and were conditional, so HLS could not infer bursts.
- `MAX_DOWN_SCALE` = 16 made the library allocate 9 line-buffer copies; 4K→240p needs 9×, so 6 copies are enough.

**Host and baseline**
- With 6 CUs, output frames went to the encoder in *completion* order, not decode order, which is a correctness bug.
- The CPU baseline packed and unpacked every 4K frame into FPGA word format (~30 ms/frame), work the FPGA host no longer did. That inflated the thesis's 2.25× FPGA win.

## 3. v2 FPGA design

```
 HBM[2c] --burst rd--> [512b FIFO 512] --3 words : 8 groups--> xf::cv::Mat 8UC3 NPPC8
                                                                     |
                                                  xf::cv::resize BILINEAR (MAX_DOWN 10)
                                                                     |
 HBM[2c+1] <--burst wr-- [512b FIFO 512] <--8 groups : 3 words-- xf::cv::Mat 8UC3 NPPC8
```

**Interfaces and datapath**
- **AXI width.** 512-bit words, each carrying 64 raw BGR bytes. The host buffer is simply the decoded frame, zero-padded to a multiple of 64 bytes.
- **Gearboxes.** 3 × 512 = 8 × 192 bits, so each 192-bit NPPC8 group sits at a **fixed** offset inside a 3-word block. Each gearbox is an 8:1 mux or demux with no shifter. Output padding is always zero, so checksums are deterministic.
- **Bursts.** Unconditional, fixed-trip-count read and write loops, each in its own dataflow process, so HLS infers 256-beat bursts. `num_*_outstanding` is 16.
- **Control inputs.** All size products and shifts are computed once, before the dataflow region. Invalid sizes (width not divisible by 8, upscaling, >9× downscale, fewer than 2 rows) return without writing.

**Clock and library version**
- **Clock.** 250 MHz (HLS period 4.0 ns, `freqHz` in `cfg/link.cfg`), because the library's 48-bit fixed-point weights did not close at 300 MHz in v1.
- **Library version.** The Makefile detects whether the installed Vitis Vision `resize` template has the newer `bool USE_URAM` argument and sets `LS_RESIZE_HAS_URAM_ARG`. Both variants are C-simulated here: the 2023.1 tag, and GitHub main, which has the argument.

**Throughput (per frame, 4K → 1080p)**

| Stage | Estimate |
|---|---|
| H2D | 24.9 MB at ≈12 GB/s → ≈2.1 ms |
| Kernel | ≈1.04 M cycles (8 px/cycle) at 250 MHz → ≈4.2 ms, plus latency (the v1 HLS estimate was 3.65 ms at 300 MHz) |
| D2H | 6.2 MB → ≈0.5 ms |
| **Total per CU** | ≈7 ms, i.e. ≈140 frames/s |
| **2 CUs** | ≈280 frames/s |

This is **far above what decode or encode can supply** (≈60–120 frames/s for 4K H.264 on one socket). The pipeline is therefore decode/encode-bound, and two CUs are enough. Use `make xclbin NCU=4` for the CU-scaling ablation.

**Resource estimate per CU** (v1 measured minus the removed parts):

| Resource | Per CU | 2 CUs |
|---|---|---|
| LUT | ≈40k (5.7%) | ≈11% |
| FF | ≈12k | |
| BRAM36 | ≈45 | |
| URAM | ≈58 (6 line-buffer copies instead of 9) | ≈18% |
| DSP | ≈88 | ≈3% |

## 4. Host design (`host/ls_fpga.cpp` + `common/ls_pipeline.hpp`)

- **Buffers.** One worker thread per CU, each owning `--slots-per-worker` (default 2) input/output BO pairs on that CU's HBM banks. The BOs are allocated once and **the decoder writes each frame straight into the mapped input BO** (checked: `decode_copies=0`).
- **Per frame.** `sync(TO_DEVICE, used bytes)` → `run.start()` → `wait()` (state checked) → `sync(FROM_DEVICE, used bytes)`. The `xrt::run` objects are reused.
- **Order and back-pressure.** A reorder stage restores decode order before the sink, and the free-slot pool gives back-pressure from the encoder to the decoder.
- **CU count.** CUs are detected by probing `ls_resize:{ls_resize_N}`; `--cus` forces a count.

## 5. CPU design (`cpu/ls_cpu.cpp`)

- The same pipeline code as the host. The resize stage is `cv::resize(INTER_LINEAR)` (OpenCV universal intrinsics, AVX2/AVX-512), with `--workers` frame-parallel resize threads (default 4) and `--cv-threads` for OpenCV's pool (default: `--threads`).
- **IPP.** Ubuntu's OpenCV is built **without IPP** (`cv_ipp=0`). For the industry-best baseline, build OpenCV with IPP on the server and check that `cv_ipp=1`.
- **Tuning.** In resize-only mode, run a small sweep and report the best CPU configuration, e.g. `--workers 1/4/24` with `--cv-threads 24/6/1`.

## 6. Fairness statement

**Identical on both platforms**
- Decoder, colour format, frame order, output sizes, encoder command, sink, queues and timers: they share one source file.

**Different**
- Only the resize implementation. The FPGA's fixed-point bilinear differs from OpenCV by at most 1 grey level: PSNR ≥ 51 dB on smooth content, and bit-identical at 720p and on checkerboards.
- The FPGA path pays PCIe transfers; the CPU path pays nothing extra.

**Two measurement boundaries**
- **Pipeline:** processing-window FPS, which is what a live service delivers.
- **Resize-only:** isolates the accelerator.

## 7. Build and run

On the server:

```bash
source /tools/Xilinx/Vitis/2023.1/settings64.sh && source /opt/xilinx/xrt/setup.sh
make env_check vision_check VISION_ROOT=/home/USER/Vitis_Libraries/vision
make cpu host                     # build/ls_cpu, build/ls_fpga
make xclbin                       # build/ls_single.xclbin (HLS + v++ link, ~1-2 h)
make fpga_reports                 # timing/utilisation/HLS reports -> build/reports

# pipeline, 1080p, encoded to a file
build/ls_fpga --xclbin build/ls_single.xclbin --video 5.mp4 --quality 4 --sink file --out /tmp/o.mp4 --verify
build/ls_cpu  --video 5.mp4 --quality 4 --sink file --out /tmp/o.mp4 --threads 24 --verify
# resize stage only
build/ls_fpga --xclbin build/ls_single.xclbin --video 5.mp4 --quality 4 --resize-only --iters 600
build/ls_cpu  --video 5.mp4 --quality 4 --resize-only --iters 600 --workers 4
```

**Markers.** Every run prints `Starting pipeline …` and `Consumer finished …` (the v1 markers, so no SIGTERM is needed), plus `MARK start/end`.

**RESULT keys**

| Scope | Keys |
|---|---|
| Common | `project platform impl quality quality_name in_w in_h out_w out_h mode sink encoder cv_version cv_ipp cv_threads frames workers slots_per_worker t_window_s t_compute_s fps resize_ms_mean h2d_ms_mean kernel_ms_mean d2h_ms_mean ok t_total_s` |
| Pipeline mode | adds `video_fps video_frames t_first_frame_s t_encoder_finish_s realtime_factor decode_ms_mean sink_ms_mean resize_stage_s_sum decode_copies frames_worker<k> encoder_rc` |
| Resize-only mode | adds `t_read_s distinct_frames slots mpix_per_s out_fnv1a64` |
| FPGA | adds `xclbin n_cu t_xclbin_s t_alloc_s bo_in_bytes bo_out_bytes` |
| CPU | adds `threads isa` |
| With `--verify` | `verify_max_absdiff verify_min_psnr_db verify_slots verified` |
| With `--checksum` | `out_fnv1a64` |

**Sandbox result** (`make sim_test`, no Vitis and no card): **48 passed, 0 failed**.

- **Kernel vs library:** the kernel is bit-exact against the library driven directly, for 33 geometries including every group/word remainder, with both library versions. The invalid-size guard never touches dst.
- **Kernel vs OpenCV:** max |diff| is 1 against `cv::resize` at all 5 qualities from 4K (`FULL=1 make sim_test`).
- **CPU:**
  - Outputs are identical for 1 and 3 workers.
  - Every frame is in the correct order and matches OpenCV exactly.
  - The encoded mp4 has the right frame count.
- **Host via xrt_sim:**
  - Probing finds the 2 CUs, and both CUs are used.
  - Frames arrive in order within the tolerance.
  - Output is identical with 1 CU × 3 slots.
  - `--frames`, resize-only and 4K all work.
  - The host compiles against the real XRT 2.16 headers.
- **Sandbox speed** (2 cores, not the server):

  | CPU run, 4K→1080p | Result |
  |---|---|
  | Resize-only | 742 frames/s (2.7 ms/frame) |
  | Pipeline, sink null | 55 frames/s (decode 17.6 ms/frame) |

## 8. Synthesis risks and what to check

| Check | Where | Expected | If not |
|---|---|---|---|
| II=1 on `read_words`, `words_to_mat`, `mat_to_words`, `write_words` | `build/reports/*csynth.rpt` | II=1 | none expected: fixed muxes only |
| m_axi bursts inferred (both ports) | HLS log "Inferring … burst" messages | 2 bursts of variable length | keep the loops unconditional |
| Library latency/II inside `resizeNNBilinear` | csynth | II=1 | nothing to change (library) |
| Achieved kernel clock | `xclbinutil --info` / timing summary | 250 MHz met | set `PERIOD_ls_resize := 5.0` and `freqHz=200000000` (the pipeline is decode-bound, so FPS does not change) |
| `[clock] freqHz` accepted | v++ log | ok | delete the `[clock]` section (platform 300 MHz with auto-scaling) |
| URAM/BRAM per CU | kernel_util_routed.rpt | ~58 URAM, ~45 BRAM | fine for 2–4 CUs |
| HBM clock timing | full timing summary | met with 2 CUs | 2 CUs is already the minimum |

**Not verifiable here:**
- C/RTL co-simulation.
- Real XRT behaviour with concurrent syncs on two CUs.
- HBM bank sizes (each BO is ≤ 25 MB).
- ffmpeg/RTMP server availability on the server (`--sink rtmp` needs nginx-rtmp on port 1935, as in v1).

## 9. Expected results (honest prediction)

- **Resize-only:**
  - **FPGA:** ≈7 ms/frame per CU, so ≈280 frames/s with 2 CUs (4K→1080p). The PCIe transfer alone (≈2.6 ms) is about as long as the CPU's whole resize.
  - **CPU:** 24 AVX-512 cores should do ≈1000–2000 frames/s at 1080p, and more at lower qualities.
  - **Verdict:** the CPU is expected to win this boundary by roughly 4–7×.
- **Pipeline (what a live service sees):**
  - Both platforms are limited by H.264 decode (and libx264 encode with a file sink), so FPS should be within about ±10%.
  - The v1 FPGA win of 2.25× came mostly from the artificial pack/unpack in the v1 CPU path.
  - **Energy:** the card adds ≈20–25 W for no FPS gain, so FPGA energy per frame should be slightly **worse**.
- **Where the FPGA could win:** only when the host is saturated by other work (the CPU budget for resizing is small), or at many simultaneous streams, which is project 06.

These are exactly the "PCIe roofline" and "host-bound pipeline" findings the paper should report.

## 10. Vitis IDE (2023.1) step by step

The Makefile flow above is the reference. The IDE flow below produces the same xclbin.

1. **Start.** Run `source /tools/Xilinx/Vitis/2023.1/settings64.sh` and `source /opt/xilinx/xrt/setup.sh`, then `vitis &`. Choose a new workspace, e.g. `~/ws_v2_ls_single`.
2. **Create the project.** Go to *File → New → Application Project*.
   - **Platform:** `xilinx_u50_gen3x16_xdma_5_202210_1`.
   - **Name:** `ls_single`.
   - **Template:** *Empty Application (XRT native API)*.

   This creates `ls_single` (host), `ls_single_kernels` (HW kernel) and `ls_single_system_hw_link`.
3. **Kernel project** (`ls_single_kernels`):
   - **Add sources:** `kernel/ls_resize.cpp`, `kernel/ls_resize.h` and `common/ls_config.hpp`, via *Import Sources*.
   - **Hardware function:** in *ls_single_kernels.prj*, click *Add Hardware Function* and select `ls_resize`.
   - **Compiler settings:** under *Properties → C/C++ Build → Settings → V++ Kernel Compiler → Symbols/Includes* (or *Miscellaneous*), add:
     - `-I/home/USER/Vitis_Libraries/vision/L1/include`
     - `-I<path>/05_LiveStream_Single/common`
     - `-D__SDSVHLS__`
     - `-DLS_RESIZE_HAS_URAM_ARG=1` if your library's `xf_resize.hpp` contains `bool USE_URAM`; otherwise `=0`.
   - **HLS clock:** under *V++ Kernel Compiler → Miscellaneous*, add `--hls.clock 250000000:ls_resize`.
4. **HW link project** (`ls_single_system_hw_link`):
   - **Build target:** open `binary_container_1`, set it to **Hardware**, and rename the container to `ls_single`.
   - **CUs:** set *Compute Units* for `ls_resize` to **2**.
   - **V++ Linker Settings:**
     - Point *V++ configuration settings* to `cfg/link.cfg`. It holds `nk=`, the `sp=` HBM mapping, `freqHz` and the phys_opt properties. If you paste its contents instead, remove the IDE-generated `nk` line so the CU count is not set twice.
     - Add `--vivado.prop run.impl_1.STRATEGY=Performance_Explore` to the linker options.
5. **Host project** (`ls_single`):
   - **Sources:** import `host/ls_fpga.cpp`, `common/*.hpp` and `../common/bench_common.hpp`.
   - **Compiler:**
     - Language standard: C++17.
     - Optimisation: `-O3 -march=native`.
     - Include paths: `common`, `../common`, `/usr/include/opencv4`.
   - **Linker:** libraries `xrt_coreutil`, `pthread`, and the OpenCV libraries from `pkg-config --libs opencv4` (at least `opencv_core`, `opencv_imgproc`, `opencv_videoio`, `opencv_imgcodecs`).
6. **Build and collect.** Select the system project, set the active build configuration to **Hardware**, and build (≈1–2 h). The xclbin is in `ls_single_system_hw_link/Hardware/ls_single.xclbin`, and the host binary is in `ls_single/Hardware/`.
7. **Check the reports.** Open the timing and utilisation reports in *Vitis Analyzer* and check the items in section 8.
8. **Run.** Run from a terminal (not the IDE) with the commands in section 7, so the benchmark suite's CPU pinning applies.

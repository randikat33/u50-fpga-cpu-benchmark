# 06 LiveStream Multi (v2): 4K live stream → 5-rung ABR ladder

One kernel produces all five outputs of a frame from a single input transfer. The FPGA and CPU programs share one pipeline source (decode, ladder stage, five encoders), so the ladder stage is the only difference between them.

## 1. Workload

| Item | Value |
|---|---|
| Input | H.264 4K video (3840×2160 BGR after decode). The paper uses the v1 file. Any input from 1920×1080 to 3840×2160 with a width divisible by 8 works. |
| Ladder | 240p, 360p, 480p, 720p and 1080p, all bilinear. The kernel sizes are 432/640/856/1280/1920 wide; the sink centre-crops 432→426 and 856→854, identically on both platforms, as v1 did. |
| Encoders | 5 × ffmpeg libx264 `ultrafast -tune zerolatency`, with the v1 bitrates, GOP and per-rung thread counts (2/2/4/4/6) |
| Sinks | `null`, `raw` (5 raw files), `file` (5 mp4), `hls` (the v1 behaviour: `/tmp/hls/<rung>/stream.m3u8`) |
| Modes | `pipeline` (decode → ladder → 5 encoders) and `--resize-only` (pre-decoded frames, ladder stage only) |
| Paper sweep | pipeline (sink `file` or `hls`) + resize-only; batch 1 plus the `--batch 2/4` ablation; 5 repetitions |

## 2. v1 diagnosis (from the thesis build reports)

**Kernel structure and timing**
- 6 kernels: a broadcast kernel plus 5 stream-input resizers.
- Kernel clock WNS −1.21 ns, HBM clock −0.72 ns.
- 207k LUT and **405 URAM (64%)**, because each resizer was built with `MAX_DOWN_SCALE=16` (9 line-buffer copies).

**Host path**
- **Packing:** the host packed and unpacked every 4K frame into 256-bit words with 8 pixels per word, as CPU work in the FPGA path.
- **Per-frame I/O:** 1 input + 5 output buffers and 6 enqueues per frame.
- **CPU baseline:** 24 frame-workers, each running 5 sequential resizes.

## 3. v2 FPGA design

```
                 +-> resize 240p (MD10) -> gearbox -> burst wr o0 -+
 HBM[0] burst rd |-> resize 360p (MD7)  -> gearbox -> burst wr o1 -|
 -> gearbox ---->|-> resize 480p (MD6)  -> gearbox -> burst wr o2 -|--> HBM[1] (one BO)
   (fan-out)     |-> resize 720p (MD4)  -> gearbox -> burst wr o3 -|
                 +-> resize 1080p (MD3) -> gearbox -> burst wr o4 -+
```

**Kernel structure**
- **One kernel `lm_ladder`, one dataflow region per frame.** The input gearbox writes each 192-bit NPPC8 group to all five resizer inputs, which replaces v1's broadcast kernel.
- **Gearboxes and bursts.** 512-bit words with fixed-offset gearboxes (3 words = 8 groups) and unconditional burst loops, as in project 05.
- **Line buffers sized per rung.** Each resizer gets its own `MAX_DOWN_SCALE` = ratio + 1: 10, 7, 6, 4 and 3. That is 19 line-buffer copies in total instead of 45. The C-sim proves the output is bit-identical to `MAX_DOWN_SCALE=16`.

**Memory layout and batching**
- **One output buffer.** The five write masters all map to HBM[1]. The host passes the **same BO** to all five arguments, and the kernel writes rung *k* of frame *f* at a fixed word offset.
- **Batching.** `--batch B` (1..8) frames per kernel start: the frame loop sits around the dataflow region, and offsets advance by adders, not multipliers.
- **Transfers.** One job costs 1 H2D (B × 24.9 MB), 1 start and 1 D2H (B × 11.2 MB). v1 needed 6 enqueues per frame.

**Clock, library version and resources**
- **Clock.** 250 MHz (HLS 4.0 ns). The `USE_URAM` library variant is detected by the Makefile, and both library versions are C-simulated.
- **Resources.** Each resizer is ≈40k LUT and 90 DSP (v1 measured).

  | Resource | One CU (5 resizers) |
  |---|---|
  | LUT | ≈205k (≈24%) |
  | DSP | ≈450 (8%) |
  | URAM | ≈170 (≈27%) |
  | BRAM | ≈30 |

  `make xclbin NCU=2` builds the 2-CU ablation (≈48% LUT and ≈54% URAM).

**Throughput per frame (4K)**

| Stage | Estimate |
|---|---|
| H2D | 24.9 MB → ≈2.1 ms |
| Kernel | the input stream (1.04 M cycles at 250 MHz) → ≈4.2 ms, plus latency |
| D2H | 11.2 MB → ≈0.9 ms |
| **Total, 1 CU** | ≈7.5 ms, i.e. ≈130 frames/s |

That is more than a 4K H.264 decoder feeds and more than five libx264 encoders consume, so the pipeline is host-bound.

## 4. Host design (`host/lm_fpga.cpp`, `common/lm_pipeline.hpp`)

- **Buffers.** One worker per CU, each with `--slots-per-worker` (default 3, v1's "3-deep") pairs of input and output BOs, allocated once. The decoder writes frames straight into the mapped input BO.
- **Order and back-pressure.** Jobs are reordered before the five encoder threads, one per rung. A slot goes back to the pool only after all five encoders have consumed it.
- **CU count.** Detected by probing; `--cus` forces a count.

## 5. CPU design (`cpu/lm_cpu.cpp`)

- The same pipeline code as the host.
- **Ladder stage.** `cv::resize(INTER_LINEAR)` for the 5 × B (frame, rung) pairs of a job, run concurrently on OpenCV's pool (`--rung-parallel 1`), with `--workers` jobs at a time (default 4) and `--cv-threads`.
- **IPP.** The Ubuntu OpenCV build has no IPP (`cv_ipp=0`). Build OpenCV with IPP for the industry-best number.
- **Tuning.** Sweep `--workers` and `--cv-threads` in resize-only mode and report the best configuration.

## 6. Fairness statement

**Identical on both platforms**
- Decoder, frame order, ladder sizes, crop, encoder commands, sinks, queues and timers (one source file).

**Different**
- Only the ladder stage. The FPGA bilinear differs from OpenCV by at most 1 grey level (PSNR ≥ 51 dB; the 720p rung is bit-identical).

**Also reported**
- The encoders dominate CPU load in pipeline mode. The paper should report CPU utilisation from the suite's power and CPU logs alongside frames per second.

## 7. Build and run

```bash
source /tools/Xilinx/Vitis/2023.1/settings64.sh && source /opt/xilinx/xrt/setup.sh
make env_check vision_check VISION_ROOT=/home/USER/Vitis_Libraries/vision
make cpu host        # build/lm_cpu, build/lm_fpga
make xclbin          # build/ls_multi.xclbin   (NCU=2 -> build/ls_multi_2cu.xclbin)
make fpga_reports

build/lm_fpga --xclbin build/ls_multi.xclbin --video 5.mp4 --sink hls --verify
build/lm_cpu  --video 5.mp4 --sink hls --threads 24 --verify
build/lm_fpga --xclbin build/ls_multi.xclbin --video 5.mp4 --resize-only --iters 600 --batch 1
build/lm_cpu  --video 5.mp4 --resize-only --iters 600 --workers 4
```

**Markers.** The runs print the v1 markers, so the suite's patterns still match:
- FPGA: `Starting 3-deep pipelined processing …` / `Shutdown signal received. …`
- CPU: `Starting parallel processing of all 5 resolutions …` / `Processing Complete …`

Both also print `MARK start/end`.

**RESULT keys**

| Scope | Keys |
|---|---|
| Common | `project platform impl in_w in_h rungs mode sink encoder batch cv_version cv_ipp cv_threads frames jobs workers slots_per_worker t_window_s t_compute_s fps ladder_ms_per_frame h2d_ms_per_frame kernel_ms_per_frame d2h_ms_per_frame ok t_total_s` |
| Pipeline mode | adds `video_fps video_frames t_encoder_finish_s realtime_factor decode_ms_mean sink_ms_per_frame sink_ms_per_frame_<rung> jobs_worker<k>` |
| Resize-only mode | adds `t_read_s distinct_frames slots mpix_in_per_s out_fnv1a64` |
| FPGA | adds `xclbin n_cu t_xclbin_s t_alloc_s bo_in_bytes bo_out_bytes` |
| CPU | adds `threads isa rung_parallel` |
| With `--checksum` | `out_fnv1a64_<rung>` |
| With `--verify` | `verify_max_absdiff verify_min_psnr_db verified` |

**Sandbox result** (`make sim_test`, no Vitis and no card): **41 passed, 0 failed**, about 12 min.

- **Kernel vs library:** every rung of every frame is bit-exact against the library run with `MAX_DOWN_SCALE=16`. Covered: 1920×1080, 2048×1090 ×2, 2560×1440 and 3840×2160, with both library versions. The invalid-parameter guard holds.
- **Kernel vs OpenCV:** max |diff| is 1 at 4K on every rung (720p is identical).
- **CPU:**
  - Outputs are identical across workers 1/3, batch 1/3/4 and serial or parallel rungs.
  - Every frame is in order and exact against OpenCV.
  - The 5 mp4 files have the right frame count, and the HLS sink writes 5 playlists.
- **Host via xrt_sim:**
  - 1 CU with batch 2 handles a partial last batch.
  - 2 CUs are probed and both used.
  - Output is identical across CU counts and batch sizes, and frames are in order (tolerance 2).
  - The v1 markers are printed, and the host compiles against the real XRT 2.16 headers.
- **Sandbox speed** (2 cores): 4K ladder resize-only 221 frames/s (8.8 ms/frame); 4K pipeline with sink null 41 frames/s.

## 8. Synthesis risks and what to check

| Check | Expected | If not |
|---|---|---|
| II=1 on all gearbox and burst loops | yes | none expected |
| Five write bursts inferred, variable offset `base + i` | yes | if HLS reports "offset not constant", pass per-rung base pointers through 5 scalar args (already scalars; check the log) |
| Five masters on one HBM bank accepted by v++ | yes | map o0..o4 to HBM[1..5] and allocate 5 output BOs (host change: one BO per rung) |
| Dataflow with 5 library resizers | II=1 inside each | nothing (library) |
| URAM ≈170, LUT ≈205k | fits in one CU | none |
| 250 MHz met | yes | `PERIOD_lm_ladder := 5.0`, `freqHz=200000000` |
| `freqHz` accepted | yes | delete `[clock]` |

**Not verifiable here:**
- C/RTL co-simulation.
- Real XRT behaviour when the same BO is passed to 5 arguments. It is legal in XRT as long as all 5 ports are connected to the BO's memory bank.
- HBM bank sizes: the largest BO is 8 × 24.9 MB = 200 MB.

## 9. Expected results (honest prediction)

- **Resize-only (ladder stage):**
  - **CPU:** in the sandbox, 2 cores ran the whole 4K ladder in 8.8 ms per frame (221 frames/s). Scaled to 24 AVX-512 cores that is ≈600–1200 frames/s.
  - **FPGA:** ≈130 frames/s per CU, bounded by the 4K input stream at 8 px/cycle plus PCIe.
  - **Verdict:** the **CPU is expected to win by ≈5–9×**. Batching helps the FPGA only marginally, because the fixed cost per start is small next to the transfers.
- **Pipeline:**
  - Decode plus five libx264 encoders consume most of the 24 cores. Offloading the ladder frees ≈1–3 cores for the encoders, so the FPGA can **tie or win slightly** when the CPU is saturated. This is the "mixed" result of v1, now measured fairly.
  - Report FPS, CPU utilisation and system energy together.
- **Where the FPGA would clearly win:** many simultaneous input streams on one host, or when this ladder is combined with other hardware stages (encoding on the FPGA is outside this study).

## 10. Vitis IDE (2023.1) step by step

1. **Start.** Source the Vitis and XRT settings, run `vitis &`, and create a new workspace.
2. **Create the project.** Go to *File → New → Application Project*.
   - **Platform:** `xilinx_u50_gen3x16_xdma_5_202210_1`.
   - **Name:** `ls_multi`.
   - **Template:** *Empty Application (XRT native API)*.
3. **Kernel project** (`ls_multi_kernels`):
   - **Sources:** import `kernel/lm_ladder.cpp`, `kernel/lm_ladder.h` and `common/lm_config.hpp`.
   - **Hardware function:** click *Add Hardware Function* and select `lm_ladder`.
   - **Kernel compiler flags:**
     - `-I/home/USER/Vitis_Libraries/vision/L1/include`
     - `-I<path>/06_LiveStream_Multi/common`
     - `-D__SDSVHLS__`
     - `-DLS_RESIZE_HAS_URAM_ARG=1` if `imgproc/xf_resize.hpp` contains `bool USE_URAM`; otherwise `=0`.
     - `--hls.clock 250000000:lm_ladder`
4. **HW link project:**
   - **Build target:** Hardware. Name the container `ls_multi`. `lm_ladder` compute units = **1**.
   - **V++ configuration file:** `cfg/link.cfg`. It maps `src` to HBM[0] and `o0`..`o4` all to HBM[1], and sets 250 MHz and phys_opt. If the IDE also writes an `nk=` line, keep only one.
   - **Extra linker option:** `--vivado.prop run.impl_1.STRATEGY=Performance_Explore`.
5. **Host project:**
   - **Sources:** import `host/lm_fpga.cpp`, `common/*.hpp` and `../common/bench_common.hpp`.
   - **Compiler:** C++17, `-O3 -march=native`, include paths `common`, `../common` and `/usr/include/opencv4`.
   - **Linker:** libraries `xrt_coreutil`, `pthread`, and the OpenCV core/imgproc/videoio/imgcodecs libraries.
6. **Build and collect.** Build the system project in the **Hardware** configuration. Take `ls_multi.xclbin` from `ls_multi_system_hw_link/Hardware/`.
7. **Check and run.** Check the section 8 items in Vitis Analyzer, then run from a terminal (section 7).

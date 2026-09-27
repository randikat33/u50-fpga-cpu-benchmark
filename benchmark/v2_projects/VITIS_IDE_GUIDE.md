# Vitis 2023.1 settings for the v2 projects (IDE or command line)

**Recommended route: the command line.** Every project builds with `make xclbin`, or `./build_v2.sh --xclbin` for all six. The Makefiles pass exactly the settings listed below, and the build is reproducible (the paper can cite the commands).

**Alternative: the Vitis IDE (classic application flow).** The same settings, entered by hand as described here, produce the same hardware.

**Tuned lane counts.** `tune_v2.sh` may choose a different lane count for 03 and 04 than the defaults below; the choice is recorded in `v2_projects/TUNED.conf`. If you build in the IDE, use that value for `-DMC_LANES` / `-DPF_LANES`. For 04, use it in the kernel, the host **and** the CPU flags.

## 0. One-time machine setup

```bash
source /tools/Xilinx/Vitis/2023.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
sudo apt install build-essential libopencv-dev libssl-dev libpng-dev ffmpeg numactl
ls /opt/xilinx/platforms/          # must contain xilinx_u50_gen3x16_xdma_5_202210_1
xbutil examine                     # card visible, shell loaded
```

**Board settings** (check once, same for all runs):
- BIOS: performance power profile; C-states and SMT as in the thesis.
- CPU governor: `performance` (`run_all.sh` sets it).
- The U50 in a CPU-socket-0 PCIe slot; `run_all.sh --check` prints its NUMA node.

## 1. Settings used by every project

| Setting | Value | Where in the IDE |
|---|---|---|
| Platform | `xilinx_u50_gen3x16_xdma_5_202210_1` | New Application Project → Platform |
| Template | Empty Application (**XRT native API**) | New Application Project → Templates |
| Build configuration | **Hardware** (not Emulation) | top toolbar → Active build configuration |
| HLS flow target | Vitis kernel flow | automatic in the kernel project |
| HLS part | `xcu50-fsvh2104-2-e` | automatic from the platform |
| HLS clock | see the table below (`--hls.clock <Hz>:<top>`) | kernel project → C/C++ Build Settings → V++ Kernel Compiler → Miscellaneous |
| HLS interface defaults | `config_interface -m_axi_alignment_byte_size 64 -m_axi_max_widen_bitwidth 512` | kernel compiler Miscellaneous: `--hls.pre_tcl <file>`, with a file containing that line (the Makefile flow does this automatically) |
| Clock uncertainty | 15% | same pre_tcl file: `set_clock_uncertainty 15%` |
| v++ link config | the project's `cfg/link.cfg` (connectivity, HBM banks, clock, phys_opt) | hw_link project → binary container → V++ Linker → V++ configuration settings (`--config <path>/cfg/link.cfg`) |
| Implementation strategy | `--vivado.prop run.impl_1.STRATEGY=Performance_Explore` | V++ Linker → Miscellaneous |
| Parallel jobs / threads | `--vivado.synth.jobs 32 --vivado.impl.jobs 32 --vivado.param general.maxThreads=32` (use your core count if below 32; 32 is Vivado's maximum) | V++ Linker → Miscellaneous |
| Host language | C++17 | host project → C/C++ Build Settings → GCC C++ Compiler → Dialect |
| Host optimisation | `-O3 -march=native` (Portfolio host: `-O2 -fopenmp -ffp-contract=off`) | GCC C++ Compiler → Optimization / Miscellaneous |
| Host includes | `<project>/common`, `v2_projects/common`, `$XILINX_XRT/include` | GCC C++ Compiler → Includes |
| Host libraries | `xrt_coreutil`, `uuid`, `pthread`, plus the project-specific ones below (add `uuid` explicitly, or newer linkers fail with "libuuid.so.1: DSO missing from command line") | GCC C++ Linker → Libraries |

**Link configuration.** When you give the IDE a `link.cfg`, do not also set the "Compute Units" count in the GUI. Otherwise `nk=` is defined twice. The link.cfg is the single source of truth.

**Profiling.** Do not enable profiling in the timed build. For the separate profiled xclbin, use `make xclbin PROFILE=1`, which is the same as ticking *Data transfer / Execute profile modules* in the IDE.

## 2. Per-project settings

| Project | Kernel top(s) and source | HLS clock | Extra kernel flags | CUs (in link.cfg) | Host sources and libraries |
|---|---|---|---|---|---|
| **01 AES** | `aes256ctr` in `kernel/aes256ctr.cpp` | 300 MHz | none | 2 | `host/aes_fpga.cpp`; libs: `xrt_coreutil pthread` |
| **02 Conv** | `conv3_rgb16`, `conv3_rgb8` and `conv3_gray8` in `kernel/conv3_*.cpp` (they include `conv3_core.hpp`, `conv3_top.inc`) | 300 MHz | none | 1 of each (3 kernels, one xclbin) | `host/conv_fpga.cpp`; libs: `xrt_coreutil png pthread` |
| **03 MC Heston** | `mc_heston_v2` in `kernel/mc_heston_v2.cpp` | 300 MHz | `-DMC_LANES=6` | 1 | `host/mc_fpga.cpp`, flags `-ffp-contract=off`; libs: `xrt_coreutil pthread` |
| **04 Portfolio** | `pf_kernel` in `kernel/pf_kernel.cpp` | 300 MHz | `-DPF_LANES=8 -DPF_LOG_IL=7 -DPF_IL_PRAGMA=128` (**the host and CPU must be built with the same three defines**) | 4 | `host/pf_fpga.cpp`, flags `-fopenmp -ffp-contract=off` + the same defines; libs: `xrt_coreutil pthread gomp` |
| **05 Live single** | `ls_resize` in `kernel/ls_resize.cpp` | **250 MHz** | `-I<Vitis_Libraries>/vision/L1/include -D__SDSVHLS__ -DLS_RESIZE_HAS_URAM_ARG=0/1` (1 if `imgproc/xf_resize.hpp` contains `bool USE_URAM`) | 2 | `host/ls_fpga.cpp`; libs: `xrt_coreutil pthread` + `pkg-config --libs opencv4` |
| **06 Live multi** | `lm_ladder` in `kernel/lm_ladder.cpp` | **250 MHz** | same as 05 | 1 | `host/lm_fpga.cpp`; libs: as 05 |

The CPU baselines are not part of the Vitis project. Build them with `make cpu`, or `./build_v2.sh`.

## 3. After every build: what to check

Check these in Vitis Analyzer or in `build/reports` (`make fpga_reports`):

1. **HLS synthesis report** (`*_csynth.rpt`):
   - every loop marked II=1 in the project README shows **II=1**;
   - the estimated clock is below the target;
   - there are no "unable to schedule" or "burst inference failed" warnings on the m_axi ports.
2. **Timing summary** (`*timing_summary_routed.rpt`): WNS ≥ 0 on `clk_kernel_*` and on `hbm_aclk`.
3. **Achieved clock:** `xclbinutil --info --input build/<name>.xclbin | grep -i -A3 clock`. Vitis silently lowers the kernel clock when timing fails. **Report the achieved value in the paper.**
4. **Utilisation** (`*kernel_util_routed.rpt`): compare against the estimate in the project README, section 3.
5. **First run on the card:** run each host once with `--verify`. The result must print `RESULT ok=1`.

If timing or resources fail, section 8 of each project README gives the specific fallback (fewer lanes, 250 MHz, and so on). Rebuild, then check again.

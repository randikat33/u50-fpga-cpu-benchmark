#!/usr/bin/env bash
# =============================================================================
#  config.sh  -  ONE place for every path and setting used by the suite.
#  Paths below are the ones your original run_benchmark.sh scripts used.
#  If a binary lives somewhere else, fix it here (./run_all.sh --check tells you).
# =============================================================================

# ---------------- experiment settings ----------------------------------------
REPS=${REPS:-5}                 # timed repetitions per configuration
PROFILED_REPS=${PROFILED_REPS:-1}   # extra FPGA run with full XRT tracing (NOT used for timing)
CPU_CORES="0-23"                # 24 physical cores of socket 0 (SMT siblings 48-71 unused)
NUMA_NODE=0
POWER_INTERVAL=0.1              # seconds (10 Hz)
IDLE_BASELINE_S=3               # idle power recorded before/after every run

# Cool-down (adaptive): wait at least MIN, then until temps are back near idle, at most MAX
COOLDOWN_REP_MIN=${COOLDOWN_REP_MIN:-20};          COOLDOWN_REP_MAX=${COOLDOWN_REP_MAX:-180}
COOLDOWN_PROJECT_MIN=${COOLDOWN_PROJECT_MIN:-120};  COOLDOWN_PROJECT_MAX=${COOLDOWN_PROJECT_MAX:-600}
COOLDOWN_FPGA_DELTA_C=3         # FPGA die within +3 C of idle baseline
COOLDOWN_CPU_DELTA_C=5          # CPU package within +5 C of idle baseline
COLD_PROBES=${COLD_PROBES:-3}   # extra FPGA runs made right after loading a DIFFERENT bitstream

# ---------------- 01 AES-256 CTR ---------------------------------------------
AES_ROOT="/home/USER/Desktop/Vitis_2023_Encryption_and_Decription"
AES_FPGA_BIN="$AES_ROOT/FPGA_Host/build/FPGA_Host"
AES_ENC_XCLBIN="$AES_ROOT/FPGA_system_project_encryption/build/hw/hw_link/binary_container_1.xclbin"
AES_DEC_XCLBIN="$AES_ROOT/FPGA_system_project_decrypt/build/hw/hw_link/binary_container_1.xclbin"
AES_CPU_BIN="$AES_ROOT/cpu_only/cpu_enc_dec"
AES_KEY="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
AES_SIZES_MB=(1 5 10 50 100 500 1024 2048)
AES_DATA_DIR="$AES_ROOT/thesis_test_data"      # regenerated with exact sizes
AES_OPENSSL_BASELINE=1          # also measure OpenSSL (AES-NI on/off) as extra CPU baselines
AES_TMPFS_SIZES_MB=(100 500 1024)  # extra "no-disk" run on /dev/shm (skipped if not enough RAM)
AES_COLD_SIZES_MB=(1 100 2048)      # sizes that also get cold (bitstream-swap) probe runs

# ---------------- 02 2D Convolution ------------------------------------------
CONV_ROOT="/home/USER/Desktop/Vitis_2023_2D_Convolution_Project_Color_2"
CONV_VARIANTS=(bw 8bit 16bit)
declare -A CONV_FPGA_BIN=( [bw]="$CONV_ROOT/FPGA_host_bw/build/FPGA_host"
                           [8bit]="$CONV_ROOT/FPGA_host/build/FPGA_host"
                           [16bit]="$CONV_ROOT/FPGA_host_2/build/FPGA_host" )
declare -A CONV_XCLBIN=(   [bw]="$CONV_ROOT/FPGA_system_project_bw/build/hw/hw_link/binary_container_1.xclbin"
                           [8bit]="$CONV_ROOT/FPGA_system_project_8_bit/build/hw/hw_link/binary_container_1.xclbin"
                           [16bit]="$CONV_ROOT/FPGA_system_project/build/hw/hw_link/binary_container_1.xclbin" )
declare -A CONV_CPU_BIN=(  [bw]="$CONV_ROOT/cpu_host_bw/cpu_u8_bw"
                           [8bit]="$CONV_ROOT/CPU_host/cpu_u8"
                           [16bit]="$CONV_ROOT/CPU_host_2/cpu_u16" )

# ---------------- 03 Monte-Carlo Heston (1 CU) --------------------------------
MC_ROOT="/home/USER/Desktop/Financial_Modeling"
MC_FPGA_BIN="$MC_ROOT/host_heston_mc_p6/build/host_heston_mc_p6"
MC_XCLBIN="$MC_ROOT/host_heston_mc_p6/build/binary_container_1.xclbin"
MC_CPU_BIN="$MC_ROOT/cpu_only/cpu_heston_mc"
MC_FPGA_INPROC_RUNS=1           # host --runs (1 warm-up is always added by the host)
# "paths:steps" configurations (the 9 distinct thesis configurations)
MC_CONFIGS=(131072:32 262144:32 524288:32 1048576:32 2097152:32 524288:64 524288:128 524288:256 4194304:64)

# ---------------- 04 Two-stage Portfolio (6 CU) -------------------------------
PF_ROOT="/home/USER/Desktop/Financial_Modeling_2"
PF_FPGA_BIN="$PF_ROOT/host_heston_mc_p6/build/host_heston_mc_p6"
PF_XCLBIN="$PF_ROOT/FPGA_system_project_p6/build/hw/hw_link/binary_container_1.xclbin"
PF_CPU_BIN="$PF_ROOT/cpu_only/cpu_heston_twostage"
PF_GEN_BIN="$PF_ROOT/gen_portfolio/gen_portfolio"
PF_PORTFOLIO="$PF_ROOT/gen_portfolio/portfolio.bin"

# ---------------- 05 Live stream, single output (6 CU) -----------------------
LS_ROOT="/home/USER/Desktop/Live_Stream_Single_Kernal"
LS_FPGA_DIR="$LS_ROOT/Live_Stream_Host/build"
LS_FPGA_BIN="$LS_FPGA_DIR/Live_Stream_Host"
LS_XCLBIN="$LS_FPGA_DIR/binary_container_1.xclbin"
LS_CPU_DIR="$LS_ROOT/cpu_only"
LS_CPU_BIN="$LS_CPU_DIR/Live_Stream_CPU"
LS_VIDEO="$LS_FPGA_DIR/5.mp4"
LS_QUALITIES=(0 1 2 3 4)        # 240p 360p 480p 720p 1080p
LS_CPU_DIRECT=1                 # also run the fair "no pack/unpack" CPU build if it exists
LS_CPU_BENCH_BIN="$SUITE_DIR/patches/bin/Live_Stream_CPU_bench"   # built by patches/build_patched.sh
LS_TIMEOUT_S=300

# ---------------- 06 Live stream, multi output (5+1 kernels) -----------------
LM_ROOT="/home/USER/Desktop/Live_Stream_Multi_Kernal"
LM_FPGA_DIR="$LM_ROOT/Live_Stream_Host/build"
LM_FPGA_BIN="$LM_FPGA_DIR/Live_Stream_Host"
LM_XCLBIN="$LM_FPGA_DIR/binary_container_1.xclbin"
LM_CPU_DIR="$LM_ROOT/cpu_only"
LM_CPU_BIN="$LM_CPU_DIR/Live_Stream_CPU"
LM_VIDEO="$LM_FPGA_DIR/5.mp4"
LM_TIMEOUT_S=300

# ---------------- tool setup (used by campaign.sh / tune_v2.sh, also after a reboot) --
VITIS_SETTINGS="/tools/Xilinx/Vitis/2023.1/settings64.sh"
XRT_SETUP="/opt/xilinx/xrt/setup.sh"
VISION_ROOT="${VISION_ROOT:-/home/USER/Vitis_Libraries/vision}"

# ---------------- automatic design-space tuning (tune_v2.sh) ------------------------
# lanes per CU: "start min max". The search starts at `start`, goes up while the design
# still builds, passes --verify on the card and gets >1% faster; goes down if `start` fails.
TUNE_MC_LANES="6 3 12"
TUNE_PF_LANES="8 4 16"
TUNE_FREQS_HZ="275000000 250000000 225000000"   # fallback kernel clocks when Vivado cannot close
                                                # timing at a project's default (300 / 250 MHz)
TUNE_MIN_GAIN_PCT=1
TUNE_KEEP_VIVADO_DIRS=0        # 1 = keep every variant's full Vivado run (5-15 GB each)

# =============================================================================
#  v2 ("A-grade") programs. Source code and builds live inside this folder:
#  v2_projects/<NN_Name>/build/...  (run ./build_v2.sh first, see README)
# =============================================================================
V2_ROOT="$SUITE_DIR/v2_projects"
DATA_DIR="$SUITE_DIR/data"                 # scratch outputs of the v2 runs (SSD)

# ---- 01 AES v2: one kernel for enc+dec, CPU = VAES / AES-NI / OpenSSL / T-table
AES2_FPGA_BIN="$V2_ROOT/01_AES/build/aes_fpga"
AES2_CPU_BIN="$V2_ROOT/01_AES/build/aes_cpu"
AES2_XCLBIN="$V2_ROOT/01_AES/build/aes.xclbin"
AES2_CPU_IMPLS=(vaes aesni openssl ttable)
AES2_SIZES_MB=(1 5 10 50 100 500 1024 2048)   # encrypt sweep (files on SSD, same files as v1)
AES2_DEC_SIZES_MB=(100 1024 2048)             # decrypt = same operation in CTR; spot checks
AES2_NOIO_SIZES_MB=(256 1024 4096)            # compute-only (no file I/O) throughput
AES2_TMPFS_SIZES_MB=(100 1024)                # input/output on /dev/shm
AES2_COLD_SIZE_MB=100                         # cold (bitstream-swap) probes
AES2_IV="000102030405060708090a0b0c0d0e0f"

# ---- 02 Convolution v2: one xclbin with 3 kernels (rgb16, rgb8, gray8)
CONV2_FPGA_BIN="$V2_ROOT/02_Convolution/build/conv_fpga"
CONV2_CPU_BIN="$V2_ROOT/02_Convolution/build/conv_cpu"
CONV2_XCLBIN="$V2_ROOT/02_Convolution/build/conv3.xclbin"
CONV2_VARIANTS=(rgb16 rgb8 gray8)
CONV2_SYNTH=(1920x1080 3840x2160 7680x4320 8192x8192 8192x16384)   # size sweep (compute only)
CONV2_CPU_IMPLS=(avx512 opencv)
CONV2_REPEAT=3                                # in-process repeats per synthetic run (image runs: 1)
CONV2_COLD=rgb8:7680x4320                     # cold probes (variant:size)

# ---- 03 MC Heston v2
MC2_FPGA_BIN="$V2_ROOT/03_MC_Heston/build/mc_fpga"
MC2_CPU_BIN="$V2_ROOT/03_MC_Heston/build/mc_cpu"
MC2_XCLBIN="$V2_ROOT/03_MC_Heston/build/mc_heston.xclbin"
MC2_EXTRA_CONFIGS=(16777216:64 67108864:128)  # added to v1's 9 configurations (MC2_CONFIGS)
MC2_CPU_IMPLS=(avx512 scalar)
MC2_SCALAR_MAX_STEPS=600000000                # skip the scalar CPU above paths*steps
MC2_COLD_CONFIG=131072:32                     # cold probes

# ---- 04 Portfolio v2
PF2_FPGA_BIN="$V2_ROOT/04_Portfolio/build/pf_fpga"
PF2_CPU_BIN="$V2_ROOT/04_Portfolio/build/pf_cpu"
PF2_GEN_BIN="$V2_ROOT/04_Portfolio/build/pf_gen"
PF2_XCLBIN="$V2_ROOT/04_Portfolio/build/pf.xclbin"
PF2_PATHS2=(32768 65536 131072 262144)        # stage-2 paths sweep (131072 = thesis workload)
PF2_CPU_IMPLS=(avx512 scalar)
PF2_SCALAR_PATHS2=(131072)                    # scalar CPU only at the thesis point
PF2_COLD_PATHS2=131072                        # cold probes

# ---- 05 Live stream single v2
LS2_FPGA_BIN="$V2_ROOT/05_LiveStream_Single/build/ls_fpga"
LS2_CPU_BIN="$V2_ROOT/05_LiveStream_Single/build/ls_cpu"
LS2_XCLBIN="$V2_ROOT/05_LiveStream_Single/build/ls_single.xclbin"
LS2_XCLBIN_ABLATION="$V2_ROOT/05_LiveStream_Single/build_4cu/ls_single_4cu.xclbin"   # optional
LS2_QUALITIES=(0 1 2 3 4)
LS2_SINK=file                                 # file | rtmp (needs a server on :1935) | null
LS2_FRAMES=0                                  # 0 = whole video
LS2_RO_ITERS=600                              # resize-only frames per run
LS2_CPU_TUNE=("1:24" "4:24" "24:1")           # resize-only CPU configurations workers:cv_threads
LS2_TIMEOUT_S=600

# ---- 06 Live stream multi v2
LM2_FPGA_BIN="$V2_ROOT/06_LiveStream_Multi/build/lm_fpga"
LM2_CPU_BIN="$V2_ROOT/06_LiveStream_Multi/build/lm_cpu"
LM2_XCLBIN="$V2_ROOT/06_LiveStream_Multi/build/ls_multi.xclbin"
LM2_XCLBIN_ABLATION="$V2_ROOT/06_LiveStream_Multi/build_2cu/ls_multi_2cu.xclbin"      # optional
LM2_SINK=hls                                  # hls (v1 behaviour) | file | null
LM2_FRAMES=0
LM2_RO_ITERS=600
LM2_BATCHES=(1 2 4)                           # FPGA frames per kernel call (resize-only)
LM2_CPU_TUNE=("1:24" "4:24" "24:1")
LM2_TIMEOUT_S=900

# ---------------- v1 key points (option B, see the v1 subset block at the end) --
V1S_AES_SIZES_MB=(100 2048)
V1S_MC_CONFIGS=(524288:32 4194304:64)
V1S_LS_QUALITIES=(0 4)

# ---------------- optional local overrides ------------------------------------
# Vivado parallelism for the xclbin builds (empty = automatic: JOBS=min(8,RAM/8GB),
# VIVADO_THREADS=min(16,cores/2)). Lower them if the server becomes unresponsive during builds.
[ -n "${JOBS:-}" ] && export JOBS
[ -n "${VIVADO_THREADS:-}" ] && export VIVADO_THREADS

# Put any changed paths/settings in config.local.sh (same folder) instead of editing above.
[ -f "$SUITE_DIR/config.local.sh" ] && source "$SUITE_DIR/config.local.sh"
[ -n "${JOBS:-}" ] && export JOBS
[ -n "${VIVADO_THREADS:-}" ] && export VIVADO_THREADS

# ---------------- values derived from the settings above -----------------------
# (after config.local.sh, so that overriding e.g. CONV_ROOT or LS_VIDEO also moves these)
: "${AES2_KEY:=$AES_KEY}"
: "${PF2_PORTFOLIO:=$PF_PORTFOLIO}"                       # the v1 files (pf_gen creates them if missing)
: "${PF2_MARKET:=$(dirname "$PF2_PORTFOLIO")/market.bin}"
: "${LS2_VIDEO:=$LS_VIDEO}"
: "${LM2_VIDEO:=$LM_VIDEO}"
[ -n "${MC2_CONFIGS+x}" ] || MC2_CONFIGS=("${MC_CONFIGS[@]}" "${MC2_EXTRA_CONFIGS[@]}")
if ! declare -p CONV2_IMAGE >/dev/null 2>&1; then
    declare -A CONV2_IMAGE=( [rgb16]="$CONV_ROOT/rgb_16bit_8k.png" [rgb8]="$CONV_ROOT/rgb_8bit_8k.png"
                             [gray8]="$CONV_ROOT/gray_8bit_8k.png" )
fi

# =============================================================================
#  v1 subset (option B): a few key points per project for the v1 -> v2 comparison.
#  Active when the v1 scripts are started by run_all.sh (V1_SUBSET=1).
# =============================================================================
if [ "${V1_SUBSET:-0}" = 1 ]; then
    AES_SIZES_MB=("${V1S_AES_SIZES_MB[@]}")
    AES_TMPFS_SIZES_MB=()
    AES_COLD_SIZES_MB=()
    AES_OPENSSL_BASELINE=0          # OpenSSL is measured in v2 already
    MC_CONFIGS=("${V1S_MC_CONFIGS[@]}")
    LS_QUALITIES=("${V1S_LS_QUALITIES[@]}")
    COLD_PROBES=0                   # cold starts are measured in v2
fi

# Smoke test (./run_all.sh --quick): fewer sweep points, everything else identical
if [ "${QUICK:-0}" = 1 ]; then
    AES2_SIZES_MB=(1 100 1024); AES2_DEC_SIZES_MB=(100); AES2_NOIO_SIZES_MB=(1024); AES2_TMPFS_SIZES_MB=(100)
    CONV2_SYNTH=(1920x1080 7680x4320); CONV2_REPEAT=1
    MC2_CONFIGS=(131072:32 4194304:64)
    PF2_PATHS2=(131072)
    LS2_QUALITIES=(0 4); LS2_FRAMES=300; LS2_RO_ITERS=120; LS2_CPU_TUNE=("4:24")
    LM2_FRAMES=300; LM2_RO_ITERS=120; LM2_BATCHES=(1 4); LM2_CPU_TUNE=("4:24")
fi


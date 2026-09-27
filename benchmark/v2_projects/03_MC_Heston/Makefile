# 03_MC_Heston - v2 Monte Carlo Heston basket option (Alveo U50 vs Xeon Gold 5318Y)
#
#   make sim_test            sandbox verification (C-sim, CPU, xrt_sim host)      exit 0 = pass
#   make cpu                 build/mc_cpu (AVX-512 + scalar, OpenMP)
#   make host                build/mc_fpga against real XRT ($XILINX_XRT)
#   make csynth|xo|xclbin|fpga_reports    Vitis flow (../common/fpga_flow.mk)
#   make all                 cpu host xclbin
# Variables: LANES (interleaved lanes per CU, default 6), NCU (1 -> cfg/link.cfg, 2 -> cfg/link_2cu.cfg),
#            PERIOD (HLS clock, 3.333 ns), SHIM (sandbox shims)
.DEFAULT_GOAL := all

NAME      := mc_heston
KERNELS   := mc_heston_v2
KSRC_mc_heston_v2 := kernel/mc_heston_v2.cpp
LANES     ?= 6
NCU       ?= 1
LINK_CFG  := $(CURDIR)/cfg/$(if $(filter 1,$(NCU)),link.cfg,link_$(NCU)cu.cfg)
HLS_EXTRA_CFLAGS := -DMC_LANES=$(LANES)
V2_ROOT   := $(abspath $(CURDIR)/..)
SHIM      ?= /home/USER/shim
XILINX_XRT ?= /opt/xilinx/xrt

CXX       ?= g++
CPU_FLAGS := -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off \
             -Wall -Wno-unknown-pragmas -I$(V2_ROOT)/common -Icommon -Icpu
HOST_FLAGS := -std=c++17 -O2 -march=native -ffp-contract=off -Wall -I$(V2_ROOT)/common -Icommon -Ikernel

# 300 MHz missed timing by 12 ps on this part -> build at 275 MHz
FREQ_HZ   ?= 300000000

include ../common/fpga_flow.mk

.PHONY: all cpu host sim_test tables clean
all: cpu host xclbin

cpu: build/mc_cpu
build/mc_cpu: cpu/mc_cpu.cpp cpu/mc_avx512.hpp common/mc_model.hpp common/mc_cli.hpp common/mc_tables.h
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@

host: build/mc_fpga
build/mc_fpga: host/mc_fpga.cpp common/mc_model.hpp common/mc_cli.hpp
	@mkdir -p build
	$(CXX) $(HOST_FLAGS) -I$(XILINX_XRT)/include $< -o $@ -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread

# regenerate the ICDF / exp ROM header (deterministic; sim_test checks it is up to date)
tables:
	@mkdir -p build
	$(CXX) -O2 -std=c++17 scripts/gen_tables.cpp -o build/gen_tables
	build/gen_tables > common/mc_tables.h

sim_test:
	SHIM=$(SHIM) V2_ROOT=$(V2_ROOT) LANES=$(LANES) bash test/run_sim_tests.sh

clean:
	rm -rf build/sim build/mc_cpu build/mc_fpga build/gen_tables

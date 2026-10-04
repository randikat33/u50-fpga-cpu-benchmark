# 09_MC_Asian_Sweep - the 08_MC_Asian kernel with 16 lanes and a run-time number of active lanes
# (host --active-lanes A): near-break-even test of the time-energy model (review round 7).
# Reuses 03_MC_Heston/common (RNG, ICDF, exp tables).
#
#   make sim_test            sandbox verification (C-sim, CPU, xrt_sim host)      exit 0 = pass
#   make cpu                 build/asian_cpu (AVX-512 + scalar, OpenMP)
#   make host                build/asian_fpga against real XRT ($XILINX_XRT)
#   make csynth|xo|xclbin|fpga_reports    Vitis flow (../common/fpga_flow.mk)
#   make all                 cpu host xclbin
# Variables: LANES (interleaved lanes per CU, default 16), PERIOD (HLS clock, 3.333 ns), FREQ_HZ, SHIM
.DEFAULT_GOAL := all

NAME      := mc_asian
KERNELS   := mc_asian
KSRC_mc_asian := kernel/mc_asian.cpp
KDEPS_mc_asian := kernel/mc_asian.h ../03_MC_Heston/common/mc_tables.h
LANES     ?= 16
LINK_CFG  := $(CURDIR)/cfg/link.cfg
V2_ROOT   := $(abspath $(CURDIR)/..)
MC_COMMON := $(V2_ROOT)/03_MC_Heston/common
MC_CPU    := $(V2_ROOT)/03_MC_Heston/cpu
HLS_EXTRA_CFLAGS := -DAS_LANES=$(LANES) -I$(MC_COMMON)
SHIM      ?= /home/USER/shim
XILINX_XRT ?= /opt/xilinx/xrt

CXX       ?= g++
INC       := -I$(V2_ROOT)/common -I$(MC_COMMON) -I$(MC_CPU) -Icommon -Icpu
CPU_FLAGS := -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off \
             -Wall -Wno-unknown-pragmas $(INC)
HOST_FLAGS := -std=c++17 -O2 -march=native -ffp-contract=off -Wall $(INC) -Ikernel

include ../common/fpga_flow.mk

.PHONY: all cpu host sim_test clean
all: cpu host xclbin

cpu: build/asian_cpu
build/asian_cpu: cpu/asian_cpu.cpp cpu/asian_avx512.hpp common/asian_model.hpp common/asian_cli.hpp
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@

host: build/asian_fpga
build/asian_fpga: host/asian_fpga.cpp common/asian_model.hpp common/asian_cli.hpp
	@mkdir -p build
	$(CXX) $(HOST_FLAGS) -I$(XILINX_XRT)/include $< -o $@ -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread

sim_test:
	SHIM=$(SHIM) V2_ROOT=$(V2_ROOT) LANES=$(LANES) bash test/run_sim_tests.sh

clean:
	rm -rf build/sim build/asian_cpu build/asian_fpga

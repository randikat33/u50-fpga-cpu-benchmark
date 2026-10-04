# 07_FIR - int16 FIR filter (32 taps), out-of-sample streaming workload of the time-energy model
# (Alveo U50 vs Xeon Gold 5318Y).
#
#   make sim_test            sandbox verification (C-sim, CPU, xrt_sim host)      exit 0 = pass
#   make cpu                 build/fir_cpu (AVX-512 VNNI/BW + scalar, OpenMP)
#   make host                build/fir_fpga against real XRT ($XILINX_XRT)
#   make csynth|xo|xclbin|fpga_reports    Vitis flow (../common/fpga_flow.mk)
#   make all                 cpu host xclbin
# Variables: TAPS (default 32; kernel, host and CPU default), NCU (2 -> cfg/link.cfg, 1 -> cfg/link_1cu.cfg), SHIM
.DEFAULT_GOAL := all

NAME      := fir
KERNELS   := fir_v2
KSRC_fir_v2 := kernel/fir_v2.cpp
KDEPS_fir_v2 := kernel/fir_v2.h common/fir_params.h
TAPS      ?= 32
NCU       ?= 2
LINK_CFG  := $(CURDIR)/cfg/$(if $(filter 2,$(NCU)),link.cfg,link_$(NCU)cu.cfg)
HLS_EXTRA_CFLAGS := -DFIR_TAPS=$(TAPS)
V2_ROOT   := $(abspath $(CURDIR)/..)
SHIM      ?= /home/USER/shim
XILINX_XRT ?= /opt/xilinx/xrt

CXX       ?= g++
INC       := -I$(V2_ROOT)/common -Icommon -Icpu -DFIR_TAPS=$(TAPS)
CPU_FLAGS := -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -ffp-contract=off \
             -Wall -Wno-unknown-pragmas $(INC)
HOST_FLAGS := -std=c++17 -O2 -march=native -Wall $(INC) -Ikernel

include ../common/fpga_flow.mk

.PHONY: all cpu host sim_test clean
all: cpu host xclbin

cpu: build/fir_cpu
build/fir_cpu: cpu/fir_cpu.cpp cpu/fir_avx512.hpp common/fir_ref.hpp common/fir_params.h
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@

host: build/fir_fpga
build/fir_fpga: host/fir_fpga.cpp common/fir_ref.hpp common/fir_params.h
	@mkdir -p build
	$(CXX) $(HOST_FLAGS) -I$(XILINX_XRT)/include $< -o $@ -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread

sim_test:
	SHIM=$(SHIM) V2_ROOT=$(V2_ROOT) TAPS=$(TAPS) bash test/run_sim_tests.sh

clean:
	rm -rf build/sim build/fir_cpu build/fir_fpga

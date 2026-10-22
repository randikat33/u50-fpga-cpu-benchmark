# 10_Heston_Accuracy - paper 2: energy to a target accuracy, single-asset Heston call, FT and QE schemes.
# Reuses 03_MC_Heston/common (RNG, inverse CDF, exp table) and 03_MC_Heston/cpu (vector RNG/ICDF).
#
#   make cpu                        build/hq_cpu  (scalar float model, AVX-512, double, diff, coupled)
#   make native                     build/hq_native (CPU-native baseline: Box-Muller, FMA, vector math)
#   make test                       phase-2 unit and program tests
#   make sim_test                   phase-3 sandbox tests: kernel C-sim == model == AVX-512, host via xrt_sim
#   make host                       build/hq_fpga against real XRT ($XILINX_XRT)
#   make csynth SCHEME=ft|qe        HLS only (reports in build/<scheme>/hls/), minutes
#   make xclbin SCHEME=ft|qe        full Vitis build -> build/<scheme>/hq_<scheme>.xclbin (8-20 h)
#   make fpga_reports SCHEME=ft|qe  timing / utilisation / power reports
# Variables: LANES per CU (default FT 14, QE 7; two CUs, one per SLR), PERIOD (HLS clock, 3.333 ns), FREQ_HZ, SHIM
.DEFAULT_GOAL := cpu
V2_ROOT   := $(abspath $(CURDIR)/..)
MC_COMMON := $(V2_ROOT)/03_MC_Heston/common
MC_CPU    := $(V2_ROOT)/03_MC_Heston/cpu
CXX       ?= g++
INC       := -I$(V2_ROOT)/common -I$(MC_COMMON) -I$(MC_CPU) -Icommon -Icpu
CPU_FLAGS := -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off -Wall -Wno-unknown-pragmas $(INC)
HDRS      := common/hq_model.hpp common/hq_double.hpp cpu/hq_avx512.hpp common/hq_log_tables.h $(MC_COMMON)/mc_model.hpp
HS_HDRS   := $(HDRS) common/hb_model.hpp common/hb_double.hpp common/ha_model.hpp common/ha_double.hpp cpu/hb_avx512.hpp cpu/ha_avx512.hpp
SHIM      ?= /home/claude/shim
XILINX_XRT ?= /opt/xilinx/xrt

# ---- FPGA (one xclbin per scheme). Main cases: ft, qe (kernel/hq_kernel.cpp). Secondary cases (protocol
# v0.5): bft, bqe (Case B basket, kernel/hb_kernel.cpp: the same lanes, new settle) and asian (Case A,
# kernel/ha_kernel.cpp).
SCHEME    ?= qe
ifeq ($(SCHEME),ft)
  SCHEME_ID := 0
  LANES     ?= 14
  NAME      := hq_ft
  KSRC      := kernel/hq_kernel.cpp
else ifeq ($(SCHEME),qe)
  SCHEME_ID := 1
  LANES     ?= 7
  NAME      := hq_qe
  KSRC      := kernel/hq_kernel.cpp
else ifeq ($(SCHEME),bft)
  SCHEME_ID := 0
  LANES     ?= 14
  NAME      := hb_ft
  KSRC      := kernel/hb_kernel.cpp
else ifeq ($(SCHEME),bqe)
  SCHEME_ID := 1
  LANES     ?= 7
  NAME      := hb_qe
  KSRC      := kernel/hb_kernel.cpp
else ifeq ($(SCHEME),asian)
  SCHEME_ID := 0
  LANES     ?= 14
  NAME      := ha_asian
  KSRC      := kernel/ha_kernel.cpp
else
  $(error SCHEME must be ft, qe, bft, bqe or asian)
endif
KERNELS   := $(NAME)
KSRC_$(NAME) := $(KSRC)
KHDR_ft := kernel/hq_kernel.h
KHDR_qe := kernel/hq_kernel.h
KHDR_bft := kernel/hb_kernel.h
KHDR_bqe := kernel/hb_kernel.h
KHDR_asian := kernel/ha_kernel.h
KDEPS_$(NAME) := $(KHDR_$(SCHEME)) common/hq_log_tables.h $(MC_COMMON)/mc_tables.h
LINK_CFG  := $(CURDIR)/cfg/link_$(SCHEME).cfg
BUILD     := build/$(SCHEME)
HLS_EXTRA_CFLAGS := -DHQ_SCHEME=$(SCHEME_ID) -DHQ_LANES=$(LANES) -DHA_LANES=$(LANES) -I$(MC_COMMON)
include ../common/fpga_flow.mk

.PHONY: cpu native test sim_test host tables clean scpu snative shost s_sim_test qem serve
cpu: build/hq_cpu
# secondary cases (protocol v0.5): build/hs_cpu, build/hs_native, build/hs_fpga
scpu: build/hs_cpu
snative: build/hs_native
shost: build/hs_fpga
build/hs_cpu: cpu/hs_cpu.cpp $(HS_HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@
build/hs_native: cpu/hs_native.cpp $(HS_HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@
build/hs_fpga: host/hs_fpga.cpp host/wait_mode.hpp $(HS_HDRS)
	@mkdir -p build
	$(CXX) -std=c++17 -O2 -march=native -ffp-contract=off -Wall $(INC) -I$(XILINX_XRT)/include $< -o $@ \
	    -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread
s_sim_test: build/hs_cpu build/hs_native
	SHIM=$(SHIM) V2_ROOT=$(V2_ROOT) bash test/run_sim_tests_s.sh
native: build/hq_native
# QE and QE-M in double precision (addendum v0.6.1, Section 24 sensitivity; calibration only)
# co-execution (addendum v0.7): serve modes of the CPU-native programs (the originals included unchanged)
serve: build/hq_native_serve build/hs_native_serve
build/hq_native_serve: cpu/native_serve.cpp cpu/hq_native.cpp $(HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) -DNATIVE_SRC='"hq_native.cpp"' $< -o $@
build/hs_native_serve: cpu/native_serve.cpp cpu/hs_native.cpp $(HS_HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) -DNATIVE_SRC='"hs_native.cpp"' $< -o $@
qem: build/hq_qem
build/hq_qem: cpu/hq_qem.cpp $(HS_HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@
build/hq_native: cpu/hq_native.cpp $(HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@

build/hq_cpu: cpu/hq_cpu.cpp $(HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@

build/test_units: test/test_units.cpp $(HDRS)
	@mkdir -p build
	$(CXX) $(CPU_FLAGS) $< -o $@

test: build/hq_cpu build/hq_native build/test_units
	bash test/run_tests.sh

sim_test: build/hq_cpu
	SHIM=$(SHIM) V2_ROOT=$(V2_ROOT) bash test/run_sim_tests.sh

host: build/hq_fpga
build/hq_fpga: host/hq_fpga.cpp host/wait_mode.hpp $(HDRS)
	@mkdir -p build
	$(CXX) -std=c++17 -O2 -march=native -ffp-contract=off -Wall $(INC) -I$(XILINX_XRT)/include $< -o $@ \
	    -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread

tables:
	python3 scripts/gen_log_tables.py

clean:
	rm -rf build/hq_cpu build/hq_native build/test_units build/hq_fpga build/hs_cpu build/hs_native build/hs_fpga build/sim

# AES-256-CTR v2 - see README.md
NAME     := aes
KERNELS  := aes256ctr
KSRC_aes256ctr := kernel/aes256ctr.cpp
LINK_CFG := cfg/link.cfg
include ../common/fpga_flow.mk

# Work-around: the shared HLS rule only depends on run_hls.tcl, so kernel edits would not
# re-run HLS. Add the sources as extra prerequisites of the pattern rule's target.
$(BUILD)/hls/aes256ctr/done: $(KSRC_aes256ctr) kernel/aes256ctr.h

SHIM       ?= /home/USER/shim
XILINX_XRT ?= /opt/xilinx/xrt
CXX        ?= g++
V2_COMMON  := $(V2_ROOT)/common
INC        := -I$(V2_COMMON) -Icommon -Icpu -Ikernel
# Some GCC/VM combinations resolve -march=native without VAES although the CPU has it
# (sandbox: CPUID hides VAES, native -> cascadelake). Added when /proc/cpuinfo shows it, or
# pass CPU_ISA="-mvaes -mvpclmulqdq" (test/run_sim_tests.sh does so after a probe run).
# The binary checks CPUID at run time before using VAES (override: AES_FORCE_VAES=1).
CPU_ISA    ?= $(shell grep -qw vaes /proc/cpuinfo 2>/dev/null && echo -mvaes -mvpclmulqdq)
CPU_FLAGS  := -std=c++17 -O3 -march=native $(CPU_ISA) -mprefer-vector-width=512 -fopenmp -fno-math-errno -Wall -Wextra
HOST_FLAGS := -std=c++17 -O3 -march=native -Wall -Wextra -pthread
COMMON_HDR := $(wildcard common/*.hpp) $(V2_COMMON)/bench_common.hpp

.PHONY: all cpu host sim_test cpu_bench clean
all: cpu host xclbin

cpu: $(BUILD)/aes_cpu
$(BUILD)/aes_cpu: cpu/aes_cpu.cpp cpu/aes_x86.hpp $(COMMON_HDR)
	@mkdir -p $(BUILD)
	$(CXX) $(CPU_FLAGS) $(INC) $< -o $@ -lcrypto

host: $(BUILD)/aes_fpga
$(BUILD)/aes_fpga: host/aes_fpga.cpp $(COMMON_HDR)
	@mkdir -p $(BUILD)
	$(CXX) $(HOST_FLAGS) $(INC) -I$(XILINX_XRT)/include $< -o $@ -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread

# Sandbox verification (no Vitis, no card): C-sim, CPU baselines, host via xrt_sim.
sim_test:
	SHIM=$(SHIM) BUILD=$(BUILD) CXX=$(CXX) bash test/run_sim_tests.sh

# Quick throughput run of every CPU implementation (--no-io)
cpu_bench: cpu
	bash scripts/run_all_cpu.sh --no-io --size-mb $(or $(SIZE_MB),256) --mode enc

clean:
	rm -rf $(BUILD)

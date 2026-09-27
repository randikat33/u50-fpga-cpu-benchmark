# Makefile - v2 portfolio (two-stage Heston MC) : FPGA kernel, FPGA host, CPU baseline, sandbox tests
NAME      := pf
KERNELS   := pf_kernel
KSRC_pf_kernel  := kernel/pf_kernel.cpp
PERIOD_pf_kernel := 3.333
LINK_CFG  := cfg/link.cfg

# lanes per CU and interleave depth (IL = 2^LOG_IL); keep host/CPU/kernel builds consistent
LANES     ?= 8
LOG_IL    ?= 7
IL        := $(shell echo $$((1 << $(LOG_IL))))
PF_DEFS   := -DPF_LANES=$(LANES) -DPF_LOG_IL=$(LOG_IL) -DPF_IL_PRAGMA=$(IL)
HLS_EXTRA_CFLAGS := $(PF_DEFS)

SHIM      ?= /home/USER/shim
BUILD     ?= build
CXX       ?= g++
INC       := -Icommon -Ikernel -Icpu -I../common
# -ffp-contract=off everywhere: no FMA contraction, so CPU and FPGA float results are bit-identical
CPU_FLAGS := -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -ffp-contract=off -Wall $(PF_DEFS)
HOST_FLAGS:= -std=c++17 -O2 -fopenmp -pthread -ffp-contract=off -Wall $(PF_DEFS)
SIM_FLAGS := -std=c++17 -O2 -w -ffp-contract=off $(PF_DEFS)
HLS_INC   := -I$(SHIM)/include -I$(SHIM)/HLS_arbitrary_Precision_Types/include
XRT_INC   := -I$(XILINX_XRT)/include
XRT_LIB   := -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread

COMMON_H  := $(wildcard common/*.hpp common/*.h ../common/bench_common.hpp)

.PHONY: all cpu host sim_build sim_test xrt_syntax tables icdf_exhaustive bench_sandbox clean
all: cpu host xclbin

include ../common/fpga_flow.mk

cpu: $(BUILD)/pf_cpu $(BUILD)/pf_gen

$(BUILD)/pf_cpu: cpu/pf_cpu.cpp cpu/pf_engine.hpp $(COMMON_H)
	@mkdir -p $(BUILD)
	$(CXX) $(CPU_FLAGS) $(INC) $< -o $@

$(BUILD)/pf_gen: scripts/pf_gen.cpp $(COMMON_H)
	@mkdir -p $(BUILD)
	$(CXX) -std=c++17 -O2 $(INC) $< -o $@

host: $(BUILD)/pf_fpga

$(BUILD)/pf_fpga: host/pf_fpga.cpp kernel/pf_kernel.h $(COMMON_H)
	@test -n "$(XILINX_XRT)" || { echo "XILINX_XRT not set: source /opt/xilinx/xrt/setup.sh"; exit 1; }
	@mkdir -p $(BUILD)
	$(CXX) $(HOST_FLAGS) $(INC) $(XRT_INC) $< -o $@ $(XRT_LIB)

# ---------------- sandbox (C-simulation) builds
$(BUILD)/sim/pf_kernel.o: kernel/pf_kernel.cpp kernel/pf_kernel.h $(COMMON_H)
	@mkdir -p $(BUILD)/sim
	$(CXX) $(SIM_FLAGS) $(HLS_INC) $(INC) -c $< -o $@

$(BUILD)/sim/tb_kernel: test/tb_kernel.cpp $(BUILD)/sim/pf_kernel.o cpu/pf_engine.hpp $(COMMON_H)
	$(CXX) $(SIM_FLAGS) -march=native $(INC) $< $(BUILD)/sim/pf_kernel.o -o $@

# odd configuration: 3 lanes, IL=16 (C-sim only, exercises block/lane edge cases)
$(BUILD)/sim/tb_kernel_l3: test/tb_kernel.cpp kernel/pf_kernel.cpp cpu/pf_engine.hpp $(COMMON_H)
	$(CXX) $(filter-out $(PF_DEFS),$(SIM_FLAGS)) -DPF_LANES=3 -DPF_LOG_IL=4 -DPF_IL_PRAGMA=16 $(HLS_INC) $(INC) -c kernel/pf_kernel.cpp -o $(BUILD)/sim/pf_kernel_l3.o
	$(CXX) $(filter-out $(PF_DEFS),$(SIM_FLAGS)) -DPF_LANES=3 -DPF_LOG_IL=4 -DPF_IL_PRAGMA=16 -march=native $(INC) $< $(BUILD)/sim/pf_kernel_l3.o -o $@

$(BUILD)/sim/pf_fpga_sim: host/pf_fpga.cpp test/sim_register.cpp $(BUILD)/sim/pf_kernel.o $(COMMON_H)
	$(CXX) $(HOST_FLAGS) -w -DXRT_SIM -DPF_SIM_CUS=2 $(INC) -I$(SHIM)/xrt_sim host/pf_fpga.cpp \
	    test/sim_register.cpp $(BUILD)/sim/pf_kernel.o $(HLS_INC) -o $@

$(BUILD)/sim/test_rng_icdf: test/test_rng_icdf.cpp cpu/pf_engine.hpp $(COMMON_H)
	$(CXX) $(CPU_FLAGS) $(INC) $< -o $@

$(BUILD)/sim/ref_bm: test/ref_bm.cpp cpu/pf_engine.hpp $(COMMON_H)
	$(CXX) $(CPU_FLAGS) $(INC) $< -o $@

sim_build: cpu $(BUILD)/sim/tb_kernel $(BUILD)/sim/tb_kernel_l3 $(BUILD)/sim/pf_fpga_sim \
           $(BUILD)/sim/test_rng_icdf $(BUILD)/sim/ref_bm

sim_test: sim_build
	SHIM=$(SHIM) BUILD=$(BUILD) bash test/run_sim_tests.sh

# real-XRT syntax check of the host (sandbox)
xrt_syntax:
	$(CXX) -std=c++17 -fsyntax-only -Wall -fopenmp $(PF_DEFS) $(INC) -I$(SHIM)/XRT/src/runtime_src/core/include -I$(SHIM)/xrtver host/pf_fpga.cpp

# regenerate the ICDF / exp tables (checked in)
tables:
	@mkdir -p $(BUILD)
	$(CXX) -std=c++17 -O2 scripts/gen_tables.cpp -o $(BUILD)/gen_tables
	$(BUILD)/gen_tables > common/pf_tables.h

icdf_exhaustive: $(BUILD)/sim/test_rng_icdf
	$(BUILD)/sim/test_rng_icdf --exhaustive

# CPU throughput on a mid-size portfolio (sandbox: 2 threads)
bench_sandbox: cpu
	$(BUILD)/pf_gen --trades 20000 --portfolio $(BUILD)/p20k.bin --market $(BUILD)/m.bin >/dev/null
	$(BUILD)/pf_cpu --portfolio $(BUILD)/p20k.bin --market $(BUILD)/m.bin --topk 100 --paths2 16384 --impl avx512 | grep -E "impl=|msteps|t_compute"
	$(BUILD)/pf_cpu --portfolio $(BUILD)/p20k.bin --market $(BUILD)/m.bin --topk 100 --paths2 16384 --impl scalar | grep -E "impl=|msteps|t_compute"

clean:
	rm -rf $(BUILD)

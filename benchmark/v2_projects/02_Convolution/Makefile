# v2 3x3 convolution (sharpen / edge / blur) - Alveo U50 vs Xeon Gold 5318Y
#   make sim_test      sandbox verification (C-sim, CPU impls, host via xrt_sim, XRT syntax, 8K throughput)
#   make cpu           build/conv_cpu            (production flags)
#   make host          build/conv_fpga           (real XRT: source /opt/xilinx/xrt/setup.sh)
#   make csynth | xo | xclbin | fpga_reports      (Vitis 2023.1, ../common/fpga_flow.mk)
#   make all           cpu host xclbin
V2_ROOT  := $(abspath ..)
NAME     := conv3
KERNELS  := conv3_rgb16 conv3_rgb8 conv3_gray8
KDEPS    := kernel/conv3_core.hpp kernel/conv3_top.inc common/conv_params.hpp
KSRC_conv3_rgb16 := kernel/conv3_rgb16.cpp
KSRC_conv3_rgb8  := kernel/conv3_rgb8.cpp
KSRC_conv3_gray8 := kernel/conv3_gray8.cpp
NCU      ?= 1
# absolute: fpga_flow.mk runs v++ from build/link
LINK_CFG := $(abspath $(if $(filter 2,$(NCU)),cfg/link_2cu.cfg,cfg/link.cfg))
ifeq ($(NCU),2)
NAME     := conv3_2cu
endif

SHIM     ?= /home/USER/shim
BUILD    ?= build
CXX      ?= g++
HLS_INC   = -I$(SHIM)/include -I$(SHIM)/HLS_arbitrary_Precision_Types/include
XRTSIM_INC= -I$(SHIM)/xrt_sim
INC       = -I$(V2_ROOT)/common -Icommon -Ikernel -Icpu -Itest
CPU_FLAGS = -std=c++17 -O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno -Wall -Wextra -Wno-unused-parameter
HOST_FLAGS= -std=c++17 -O3 -march=native -Wall -Wextra -Wno-unused-parameter -pthread
OPENCV    = $(shell pkg-config --cflags --libs opencv4 2>/dev/null || echo -lopencv_imgproc -lopencv_core)
XILINX_XRT ?= /opt/xilinx/xrt

.PHONY: all cpu host sim sim_test vec_report clean
all: cpu host xclbin

cpu: $(BUILD)/conv_cpu
$(BUILD)/conv_cpu: cpu/conv_cpu.cpp cpu/conv_avx512.hpp common/conv_ref.hpp common/img_io.hpp
	@mkdir -p $(BUILD)
	$(CXX) $(CPU_FLAGS) $(INC) $< -o $@ $(OPENCV) -lpng

host: $(BUILD)/conv_fpga
$(BUILD)/conv_fpga: host/conv_fpga.cpp common/conv_plan.hpp common/img_io.hpp common/conv_ref.hpp
	@mkdir -p $(BUILD)
	$(CXX) $(HOST_FLAGS) $(INC) -I$(XILINX_XRT)/include $< -o $@ -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread -lpng -pthread

# ---- sandbox (C-simulation) builds ------------------------------------------------
KSRCS = kernel/conv3_rgb16.cpp kernel/conv3_rgb8.cpp kernel/conv3_gray8.cpp
sim: $(BUILD)/sim/tb_kernel $(BUILD)/sim/conv_fpga_sim $(BUILD)/sim/mkimg $(BUILD)/conv_cpu
$(BUILD)/sim/tb_kernel: test/tb_kernel.cpp test/word_io.hpp $(KSRCS) $(KDEPS) common/conv_ref.hpp common/conv_plan.hpp
	@mkdir -p $(dir $@)
	$(CXX) -std=c++17 -O2 -w $(HLS_INC) $(INC) test/tb_kernel.cpp $(KSRCS) -lpng -o $@
$(BUILD)/sim/conv_fpga_sim: host/conv_fpga.cpp test/sim_register.cpp test/word_io.hpp $(KSRCS) $(KDEPS) common/img_io.hpp common/conv_plan.hpp
	@mkdir -p $(dir $@)
	$(CXX) -std=c++17 -O2 -w -DXRT_SIM -DSIM_CUS=2 $(HLS_INC) $(XRTSIM_INC) $(INC) host/conv_fpga.cpp test/sim_register.cpp $(KSRCS) -lpng -pthread -o $@
$(BUILD)/sim/mkimg: test/mkimg.cpp common/img_io.hpp
	@mkdir -p $(dir $@)
	$(CXX) -std=c++17 -O2 -w $(INC) $< -lpng -o $@

sim_test: sim
	SHIM=$(SHIM) BUILD=$(BUILD) V2_ROOT=$(V2_ROOT) bash test/run_sim_tests.sh

# auto-vectorisation notes for the scalar reference/fallback path (the hot path uses intrinsics)
vec_report:
	@mkdir -p $(BUILD)
	$(CXX) $(CPU_FLAGS) -fopt-info-vec-missed $(INC) cpu/conv_cpu.cpp -o /dev/null $(OPENCV) -lpng 2> $(BUILD)/vec_notes.txt || true
	@echo "notes in $(BUILD)/vec_notes.txt"

clean:
	rm -rf $(BUILD)/sim $(BUILD)/conv_cpu $(BUILD)/conv_fpga $(BUILD)/simtest

# ---- FPGA flow ----------------------------------------------------------------------
include $(V2_ROOT)/common/fpga_flow.mk
# the shared pattern rule only depends on run_hls.tcl: re-run HLS when kernel sources change
$(BUILD)/hls/conv3_rgb16/done: $(KSRC_conv3_rgb16) $(KDEPS)
$(BUILD)/hls/conv3_rgb8/done: $(KSRC_conv3_rgb8) $(KDEPS)
$(BUILD)/hls/conv3_gray8/done: $(KSRC_conv3_gray8) $(KDEPS)

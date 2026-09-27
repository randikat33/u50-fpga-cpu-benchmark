# 05 LiveStream Single (v2) - see README.md
NAME     := ls_single
NCU      ?= 2
KERNELS  := ls_resize
KSRC_ls_resize  := kernel/ls_resize.cpp
KDEPS_ls_resize := kernel/ls_resize.h common/ls_config.hpp
PERIOD_ls_resize := 4.0
LINK_CFG := $(if $(filter 4,$(NCU)),cfg/link_4cu.cfg,cfg/link.cfg)
ifneq ($(NCU),2)
  NAME := ls_single_$(NCU)cu
endif

# Vitis Vision library (the v1 build used /home/USER/Vitis_Libraries). Newer library
# releases add a `USE_URAM` template argument to xf::cv::resize; detect which one it is.
VISION_ROOT ?= /home/USER/Vitis_Libraries/vision
VISION_INC  := $(VISION_ROOT)/L1/include
URAM_ARG    := $(shell grep -q "bool USE_URAM" $(VISION_INC)/imgproc/xf_resize.hpp 2>/dev/null && echo 1 || echo 0)
HLS_EXTRA_CFLAGS := -I$(VISION_INC) -D__SDSVHLS__ -DLS_RESIZE_HAS_URAM_ARG=$(URAM_ARG)

# this kernel is built for 250 MHz (Vitis Vision resize; see README section 3)
FREQ_HZ   ?= 250000000

include ../common/fpga_flow.mk

SHIM       ?= /home/USER/shim
XILINX_XRT ?= /opt/xilinx/xrt
CXX        ?= g++
V2_COMMON  := $(V2_ROOT)/common
INC        := -I$(V2_COMMON) -Icommon
OPENCV     := $(shell pkg-config --cflags --libs opencv4)
CPU_FLAGS  := -std=c++17 -O3 -march=native -Wall -Wextra -pthread
HOST_FLAGS := -std=c++17 -O3 -march=native -Wall -Wextra -pthread
HDRS       := $(wildcard common/*.hpp) $(V2_COMMON)/bench_common.hpp

.PHONY: all cpu host sim_test vision_check clean
all: cpu host xclbin

vision_check:
	@test -f $(VISION_INC)/imgproc/xf_resize.hpp || { echo "Vitis Vision not found: make VISION_ROOT=/path/to/Vitis_Libraries/vision"; exit 1; }
	@echo "Vitis Vision: $(VISION_ROOT)  (resize has USE_URAM argument: $(URAM_ARG))"

$(BUILD)/hls/ls_resize/done: | vision_check

cpu: $(BUILD)/ls_cpu
$(BUILD)/ls_cpu: cpu/ls_cpu.cpp $(HDRS)
	@mkdir -p $(BUILD)
	$(CXX) $(CPU_FLAGS) $(INC) $< -o $@ $(OPENCV)

host: $(BUILD)/ls_fpga
$(BUILD)/ls_fpga: host/ls_fpga.cpp $(HDRS)
	@mkdir -p $(BUILD)
	$(CXX) $(HOST_FLAGS) $(INC) -I$(XILINX_XRT)/include $< -o $@ -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread $(OPENCV)

# Sandbox verification (no Vitis, no card): kernel C-sim, CPU pipeline, host via xrt_sim.
sim_test:
	SHIM=$(SHIM) BUILD=$(BUILD) CXX=$(CXX) bash test/run_sim_tests.sh

clean:
	rm -rf $(BUILD)

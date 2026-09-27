# 06 LiveStream Multi (v2) - see README.md
NAME     := ls_multi
NCU      ?= 1
KERNELS  := lm_ladder
KSRC_lm_ladder   := kernel/lm_ladder.cpp
KDEPS_lm_ladder  := kernel/lm_ladder.h common/lm_config.hpp
PERIOD_lm_ladder := 4.0
LINK_CFG := $(if $(filter 2,$(NCU)),cfg/link_2cu.cfg,cfg/link.cfg)
ifneq ($(NCU),1)
  NAME := ls_multi_$(NCU)cu
endif

# Vitis Vision library (v1 used /home/USER/Vitis_Libraries). Newer releases add a
# `bool USE_URAM` template argument to xf::cv::resize: detected here.
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
FLAGS      := -std=c++17 -O3 -march=native -Wall -Wextra -pthread
HDRS       := $(wildcard common/*.hpp) $(V2_COMMON)/bench_common.hpp

.PHONY: all cpu host sim_test vision_check clean
all: cpu host xclbin

vision_check:
	@test -f $(VISION_INC)/imgproc/xf_resize.hpp || { echo "Vitis Vision not found: make VISION_ROOT=/path/to/Vitis_Libraries/vision"; exit 1; }
	@echo "Vitis Vision: $(VISION_ROOT)  (resize has USE_URAM argument: $(URAM_ARG))"

$(BUILD)/hls/lm_ladder/done: | vision_check

cpu: $(BUILD)/lm_cpu
$(BUILD)/lm_cpu: cpu/lm_cpu.cpp $(HDRS)
	@mkdir -p $(BUILD)
	$(CXX) $(FLAGS) $(INC) $< -o $@ $(OPENCV)

host: $(BUILD)/lm_fpga
$(BUILD)/lm_fpga: host/lm_fpga.cpp $(HDRS)
	@mkdir -p $(BUILD)
	$(CXX) $(FLAGS) $(INC) -I$(XILINX_XRT)/include $< -o $@ -L$(XILINX_XRT)/lib -lxrt_coreutil -l:libuuid.so.1 -pthread $(OPENCV)

sim_test:
	SHIM=$(SHIM) BUILD=$(BUILD) CXX=$(CXX) bash test/run_sim_tests.sh

clean:
	rm -rf $(BUILD)

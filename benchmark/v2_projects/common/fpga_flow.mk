# fpga_flow.mk - common command-line build flow for every v2 project (Vitis 2023.1, Alveo U50)
#
# A project Makefile sets, then includes this file:
#   NAME      := short project name (xclbin = build/$(NAME).xclbin)
#   KERNELS   := list of HLS top functions (one .xo each)
#   KSRC_<k>  := kernel source files for top <k>
#   PERIOD_<k>:= HLS clock period in ns for top <k> (default $(PERIOD))
#   LINK_CFG  := cfg/link.cfg (connectivity: nk=, sp=, clock)
#   HOST_SRC, CPU_SRC... are handled by the project Makefile itself.
#
# Main targets provided here:
#   make csynth        run vitis_hls C synthesis for every kernel (reports in build/hls/<k>/)
#   make xo            export .xo files
#   make xclbin        v++ link (Vivado synth+impl) -> build/$(NAME).xclbin   (1-4 h)
#   make fpga_reports  copy timing/utilisation/HLS reports into build/reports/
#   make env_check     print tool versions and the platform that will be used
#
# Everything runs from the shell: no IDE project is needed. See ../VITIS_IDE_GUIDE.md
# for the equivalent Vitis IDE settings if you prefer the GUI.
#
# Optional per kernel:  KDEPS_<k> := extra files whose change must re-run HLS (headers).

SHELL        := /bin/bash
.SHELLFLAGS  := -o pipefail -c
V2_ROOT      := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
ifeq ($(.DEFAULT_GOAL),)
.DEFAULT_GOAL := all
endif
PERIOD       ?= 3.333
# kernel clock requested from v++ (the [clock] section is no longer in the cfg files, so that
# tune_v2.sh can retry a design at a lower clock when Vivado cannot close timing at 300 MHz)
FREQ_HZ      ?= 300000000
PART         ?= xcu50-fsvh2104-2-e
PLATFORM     ?= $(firstword $(wildcard /opt/xilinx/platforms/xilinx_u50_gen3x16_xdma_5_202210_1/*.xpfm) \
                            $(wildcard /opt/xilinx/platforms/xilinx_u50_gen3x16_xdma*/*.xpfm))
# parallelism of the Vivado part of v++ link (the long part):
#   JOBS            parallel synthesis/implementation runs (each needs several GB of RAM)
#   VIVADO_THREADS  threads inside one run (placer, router, phys_opt); Vivado's maximum is 32
# Defaults keep the machine responsive: JOBS = min(8, RAM_GB/8), VIVADO_THREADS = min(16, cores/2).
# Override e.g. in config.local.sh:  export JOBS=4 VIVADO_THREADS=8
JOBS         ?= $(shell r=$$(awk '/MemTotal/{print int($$2/1048576/8)}' /proc/meminfo 2>/dev/null); r=$${r:-4}; \
                  [ $$r -lt 1 ] && r=1; [ $$r -gt 8 ] && r=8; echo $$r)
VIVADO_THREADS ?= $(shell n=$$(( $$(nproc 2>/dev/null || echo 8) / 2 )); [ $$n -lt 1 ] && n=1; [ $$n -gt 16 ] && n=16; echo $$n)
STRATEGY     ?= Performance_Explore
# extra v++ flags for a single build (post-route phys-opt etc.); empty by default
VPP_EXTRA    ?=
PROFILE      ?= 0
BUILD        ?= build
HLS_CFLAGS   ?= -I$(V2_ROOT)/common -I$(CURDIR)/kernel -I$(CURDIR)/common

LINK_CFG_ABS = $(abspath $(LINK_CFG))
VPP_LINK_FLAGS = -l -t hw --platform $(PLATFORM) --config $(LINK_CFG_ABS) \
    --vivado.synth.jobs $(JOBS) --vivado.impl.jobs $(JOBS) \
    --vivado.param general.maxThreads=$(VIVADO_THREADS) \
    --vivado.prop run.impl_1.STRATEGY=$(STRATEGY) \
    --report_level 2 --save-temp $(VPP_EXTRA)
ifeq ($(PROFILE),1)
  # adds AXI performance monitors (used only for the separate "profiled" run)
  VPP_LINK_FLAGS += --profile.data all:all:all
  NAME_SUFFIX := _prof
endif

XCLBIN := $(BUILD)/$(NAME)$(NAME_SUFFIX).xclbin
XOS    := $(foreach k,$(KERNELS),$(BUILD)/xo/$(k).xo)

.PHONY: csynth xo xclbin fpga_reports env_check

# print-<VAR>: used by tune_v2.sh to read a project's defaults (e.g. make -s print-FREQ_HZ)
print-%:
	@echo $($*)
env_check:
	@echo "vitis_hls : $$(command -v vitis_hls || echo MISSING - run: source /tools/Xilinx/Vitis/2023.1/settings64.sh)"
	@echo "v++       : $$(command -v v++ || echo MISSING)"
	@echo "XRT       : $${XILINX_XRT:-MISSING - run: source /opt/xilinx/xrt/setup.sh}"
	@echo "PLATFORM  : $(if $(PLATFORM),$(PLATFORM),MISSING - set PLATFORM=/path/to/xilinx_u50_*.xpfm)"
	@echo "PART      : $(PART)"
	@echo "JOBS      : $(JOBS)   STRATEGY: $(STRATEGY)"

# ---- HLS: one tcl run per kernel -------------------------------------------------
.SECONDEXPANSION:
$(BUILD)/hls/%/done: $(V2_ROOT)/common/run_hls.tcl $$(KSRC_$$*) $$(KDEPS_$$*)
	@mkdir -p $(BUILD)/hls/$* $(BUILD)/xo
	@# arguments go through the environment: vitis_hls treats any "-I..." argument as its own option
	cd $(BUILD)/hls/$* && HLS_TOP="$*" HLS_SRCS="$(abspath $(KSRC_$*))" \
	    HLS_CFLAGS_ALL="$(HLS_CFLAGS) $(HLS_EXTRA_CFLAGS)" HLS_PERIOD="$(or $(PERIOD_$*),$(PERIOD))" \
	    HLS_PART="$(PART)" HLS_XO="$(abspath $(BUILD))/xo/$*.xo" \
	    vitis_hls -f $(V2_ROOT)/common/run_hls.tcl 2>&1 | tee hls.log
	@if grep -q "^ERROR" $(BUILD)/hls/$*/hls.log; then echo "HLS failed for $*"; exit 1; fi
	@touch $@

$(BUILD)/xo/%.xo: $(BUILD)/hls/%/done
	@test -f $@ || { echo "missing $@"; exit 1; }

csynth xo: $(foreach k,$(KERNELS),$(BUILD)/hls/$(k)/done)

$(XCLBIN): $(XOS) $(LINK_CFG)
	@test -n "$(PLATFORM)" || { echo "PLATFORM not found. make PLATFORM=/opt/xilinx/platforms/<u50>/<u50>.xpfm"; exit 1; }
	@mkdir -p $(BUILD)/link
	@# clock.cfg: name the kernel clock PER COMPUTE UNIT (the CU list comes from the nk= lines of
	@# LINK_CFG). --clock.defaultFreqHz is NOT honoured for these platforms: the CU then silently
	@# keeps the platform default (300 MHz), which is why a "lower clock" retry had no effect.
	@{ echo "[clock]"; awk '/^nk=/{n=$$0; sub(/^nk=/,"",n); split(n,a,":"); split(a[3],c,"."); \
	    for (i in c) printf "freqHz=$(FREQ_HZ):%s.ap_clk\n", c[i]}' $(LINK_CFG_ABS); } > $(BUILD)/link/clock.cfg
	@echo "  kernel clock requested: $(FREQ_HZ) Hz"; cat $(BUILD)/link/clock.cfg | sed 's/^/    /'
	@rm -f $(abspath $(BUILD))/partial_$(notdir $@)
	cd $(BUILD)/link && v++ $(VPP_LINK_FLAGS) --config clock.cfg -o $(abspath $(BUILD))/partial_$(notdir $@) $(abspath $(XOS)) 2>&1 | tee link.log
	@test -f $(abspath $(BUILD))/partial_$(notdir $@)
	@mv -f $(abspath $(BUILD))/partial_$(notdir $@) $@   # an interrupted link never leaves a usable-looking xclbin

xclbin: $(XCLBIN)

fpga_reports:
	@mkdir -p $(BUILD)/reports
	@for k in $(KERNELS); do \
	    d=$$(find $(BUILD)/hls/$$k -path "*syn/report*" -name csynth.rpt | head -1); \
	    [ -n "$$d" ] && cp "$$d" $(BUILD)/reports/$${k}_csynth.rpt && cp "$${d%.rpt}.xml" $(BUILD)/reports/$${k}_csynth.xml 2>/dev/null; \
	    cp $(BUILD)/hls/$$k/hls.log $(BUILD)/reports/$${k}_hls.log 2>/dev/null; \
	done; true
	-@find $(BUILD)/link -name "*timing_summary_routed.rpt" -exec cp {} $(BUILD)/reports/ \;
	-@find $(BUILD)/link -name "*util_routed.rpt" -exec cp {} $(BUILD)/reports/ \;
	-@find $(BUILD)/link -name "*power_routed.rpt" -exec cp {} $(BUILD)/reports/ \;
	-@find $(BUILD)/link -name "*.link_summary" -exec cp {} $(BUILD)/reports/ \;
	-@find $(BUILD)/link -name "system_estimate*.xtxt" -exec cp {} $(BUILD)/reports/ \;
	-@cp $(BUILD)/link/link.log $(BUILD)/reports/v++_link.log 2>/dev/null; true
	-@xclbinutil --info --input $(XCLBIN) > $(BUILD)/reports/xclbin_info.txt 2>&1
	@echo "reports in $(BUILD)/reports:"; ls $(BUILD)/reports

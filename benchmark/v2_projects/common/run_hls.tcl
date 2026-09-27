# run_hls.tcl - vitis_hls 2023.1 batch script used by fpga_flow.mk
# inputs (environment): HLS_TOP HLS_SRCS HLS_CFLAGS_ALL HLS_PERIOD HLS_PART HLS_XO
# (vitis_hls 2023.1 rejects -tclargs values that start with "-", e.g. "-I...")
set top    $::env(HLS_TOP)
set srcs   $::env(HLS_SRCS)
set cflags $::env(HLS_CFLAGS_ALL)
set period $::env(HLS_PERIOD)
set part   $::env(HLS_PART)
set xo     $::env(HLS_XO)
puts "run_hls: top=$top period=$period part=$part"
puts "run_hls: srcs=$srcs"
puts "run_hls: cflags=$cflags"

open_project -reset prj
set_top $top
foreach f $srcs { add_files $f -cflags "$cflags" -csimflags "$cflags" }
open_solution -reset sol -flow_target vitis
set_part $part
create_clock -period $period -name default
set_clock_uncertainty 15%
# Vitis-flow interface defaults (widened, 64-byte aligned bursts; aggressive but safe)
config_interface -m_axi_alignment_byte_size 64 -m_axi_max_widen_bitwidth 512
csynth_design
export_design -rtl verilog -format xo -output $xo
exit

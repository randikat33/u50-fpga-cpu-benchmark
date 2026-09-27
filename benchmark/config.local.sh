JOBS=6
VIVADO_THREADS=16
TUNE_FREQS_HZ="275000000 250000000 225000000 200000000"

# 8192x16384 (134 Mpix) cannot be allocated on the U50 for the FPGA path: input plus
# output for a strip exceed the 256 MB capacity of one HBM pseudo-channel.  The point is
# dropped from the sweep and reported as a device limit (see REPORT/paper).
CONV2_SYNTH=(1920x1080 3840x2160 7680x4320 8192x8192)

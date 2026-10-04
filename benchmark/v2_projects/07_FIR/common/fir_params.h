// fir_params.h - compile-time parameters shared by the kernel, the host, the CPU baseline and the tests.
#pragma once
#ifndef FIR_TAPS
#define FIR_TAPS 32          // taps of the FPGA kernel (compile-time); the CPU takes any even T <= 256
#endif
#define FIR_L 16             // samples per 256-bit word (one word per clock cycle per CU)
#define FIR_HW ((FIR_TAPS - 1 + FIR_L - 1) / FIR_L)   // history words in front of every input chunk
#define FIR_TMAX 256

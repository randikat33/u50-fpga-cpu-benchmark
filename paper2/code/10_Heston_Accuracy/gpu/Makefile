# gpu/Makefile - GPU-native code of paper 2 (protocol v0.6, Section 22). Run from 10_Heston_Accuracy:  make -f gpu/Makefile
# Fixed build flags (published with the results): CUDA 12.8, sm_70 (V100), -O3, --use_fast_math.
NVCC    ?= nvcc
NVFLAGS := -O3 -arch=sm_70 --use_fast_math -std=c++17 -lineinfo -Igpu
all: build/hs_gpu build/gpu_ubench build/host_emul_test
build/hs_gpu: gpu/hs_gpu.cu gpu/hs_gpu_core.cuh gpu/hs_gpu_host.hpp
	@mkdir -p build
	$(NVCC) $(NVFLAGS) -Xptxas -v gpu/hs_gpu.cu -o $@ 2> build/hs_gpu_ptxas.log || (cat build/hs_gpu_ptxas.log; false)
	@grep -E "registers|spill" build/hs_gpu_ptxas.log | head -4
build/gpu_ubench: gpu/gpu_ubench.cu gpu/hs_gpu_host.hpp gpu/hs_gpu_core.cuh
	@mkdir -p build
	$(NVCC) $(NVFLAGS) gpu/gpu_ubench.cu -o $@
build/host_emul_test: gpu/host_emul_test.cpp gpu/hs_gpu_core.cuh gpu/hs_gpu_host.hpp
	@mkdir -p build
	$(CXX) -O2 -fopenmp -std=c++17 -x c++ -Igpu gpu/host_emul_test.cpp -o $@
# co-execution serve mode (addendum v0.7); build/hs_gpu itself is not rebuilt
build/hs_coexec: gpu/hs_coexec.cu gpu/hs_gpu.cu gpu/hs_gpu_core.cuh gpu/hs_gpu_host.hpp
	@mkdir -p build
	$(NVCC) $(NVFLAGS) gpu/hs_coexec.cu -o $@
clean:
	rm -f build/hs_gpu build/gpu_ubench build/host_emul_test
.PHONY: all clean

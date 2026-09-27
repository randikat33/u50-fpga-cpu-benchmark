#!/usr/bin/env bash
# Builds the fair CPU live-stream baseline (Live_Stream_CPU_bench) into patches/bin/.
# Your original binaries are NOT touched.
set -e
cd "$(dirname "$0")"
mkdir -p bin
command -v pkg-config >/dev/null || { echo "pkg-config missing"; exit 1; }
pkg-config --exists opencv4 || { echo "OpenCV 4 dev files not found (pkg-config opencv4)"; exit 1; }
FLAGS="-O3 -march=native -fopenmp -std=c++17"
echo "g++ $FLAGS Live_Stream_CPU_bench.cpp ..."
g++ $FLAGS -o bin/Live_Stream_CPU_bench Live_Stream_CPU_bench.cpp \
    $(pkg-config --cflags --libs opencv4) -lmicrohttpd -lpthread
echo "$FLAGS" > bin/BUILD_FLAGS.txt
g++ --version | head -1 >> bin/BUILD_FLAGS.txt
echo "OK -> $(pwd)/bin/Live_Stream_CPU_bench"

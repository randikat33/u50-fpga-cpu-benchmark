// conv_plan.hpp - horizontal strip plan for the FPGA host (and the multi-strip tests).
// Output rows [1, H-1) are split into strips [a, b); strip k's kernel input is the rows
// [a-1, b+1) (2 rows of overlap between neighbouring strips), its kernel output is b-a rows.
#pragma once
#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <vector>

namespace conv {

struct Strip {
    int out_y0 = 0, n_out = 0;   // image rows written by this strip
    int in_y0 = 0, n_in = 0;     // image rows read (= out_y0-1, n_out+2)
    int cu = 0;                  // compute unit that runs it
};

// stride_bytes: padded bytes per row; max_bo: per-BO cap; target_in: preferred input bytes
// per strip (0 = no chunking); force_rows: output rows per strip (0 = auto).
inline std::vector<Strip> plan_strips(int H, size_t stride_bytes, int n_cu, size_t max_bo,
                                      size_t target_in, int force_rows) {
    std::vector<Strip> v;
    if (H < 3 || stride_bytes == 0) return v;
    const long R = H - 2;
    long cap_rows = (long)(max_bo / stride_bytes) - 2;   // input BO holds rows+2
    if (cap_rows < 1) throw std::runtime_error("one image row does not fit in --max-bo-mb");
    long rows;
    if (force_rows > 0) rows = force_rows;
    else {
        long k = std::max<long>(1, n_cu);
        if (target_in) k = std::max<long>(k, (long)((R * stride_bytes + target_in - 1) / target_in));
        rows = (R + k - 1) / k;
    }
    rows = std::max(1L, std::min(rows, cap_rows));
    int i = 0;
    for (long a = 1; a < H - 1; a += rows, ++i) {
        Strip s;
        s.out_y0 = (int)a; s.n_out = (int)std::min(rows, (long)(H - 1) - a);
        s.in_y0 = s.out_y0 - 1; s.n_in = s.n_out + 2;
        s.cu = i % std::max(1, n_cu);
        v.push_back(s);
    }
    return v;
}

}  // namespace conv

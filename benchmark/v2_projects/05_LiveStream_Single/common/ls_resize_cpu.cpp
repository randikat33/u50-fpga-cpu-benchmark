// ls_resize_cpu.cpp - see ls_resize_cpu.hpp
#include "ls_resize_cpu.hpp"
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <cstring>
#include <stdexcept>
#include <vector>
#if defined(__AVX512F__) && defined(__AVX512BW__)
#include <immintrin.h>
#define LS_HAVE_AVX512 1
#else
#define LS_HAVE_AVX512 0
#endif
#ifdef _OPENMP
#include <omp.h>
#endif

namespace ls {

CpuImpl parse_impl(const std::string& s) {
    if (s == "opencv") return CpuImpl::OpenCV;
    if (s == "avx512") return CpuImpl::Avx512;
    if (s == "ref") return CpuImpl::Ref;
    throw std::runtime_error("unknown --resize-impl " + s + " (opencv|avx512|ref)");
}
const char* impl_name(CpuImpl i) {
    switch (i) {
        case CpuImpl::OpenCV: return "opencv_resize_linear";
        case CpuImpl::Avx512: return LS_HAVE_AVX512 ? "avx512_xfexact" : "scalar_xfexact(no_avx512)";
        default: return "ref_xfexact";
    }
}
const char* isa_name() { return LS_HAVE_AVX512 ? "avx512" : "scalar"; }

void XfPlan::build(int sw_, int sh_, int dw_, int dh_, int npc_) {
    sw = sw_; sh = sh_; dw = dw_; dh = dh_; npc = npc_;
    ax = lsref::build_axis(sw, dw, npc);
    ay = lsref::build_axis(sh, dh, 1);
    // pad the x tables to a multiple of 16 with a safe index/weight (masked writes skip them)
    size_t n = (static_cast<size_t>(dw) + 15) / 16 * 16;
    ax.i0.resize(n, 0); ax.i1.resize(n, 0); ax.w.resize(n, 0);
}

void resize_plane_opencv(const uint8_t* src, int sw, int sh, int sstride, uint8_t* dst, int dw, int dh, int dstride) {
    const cv::Mat s(sh, sw, CV_8UC1, const_cast<uint8_t*>(src), static_cast<size_t>(sstride));
    cv::Mat d(dh, dw, CV_8UC1, dst, static_cast<size_t>(dstride));
    cv::resize(s, d, cv::Size(dw, dh), 0, 0, cv::INTER_LINEAR);   // writes into d (size/type match)
}

static void row_scalar(const XfPlan& p, const uint8_t* r0, const uint8_t* r1, uint8_t* o, int wy) {
    const int32_t* x0 = p.ax.i0.data(); const int32_t* x1 = p.ax.i1.data(); const int32_t* wx = p.ax.w.data();
    for (int c = 0; c < p.dw; ++c) o[c] = lsref::interp(r0[x0[c]], r0[x1[c]], r1[x0[c]], r1[x1[c]], wx[c], wy);
}

#if LS_HAVE_AVX512
// Exact integer form (see README 5):  num = H0*1024 + T*wy - D*e,  out = num >> 20
//   H0 = A0*1024 + dA*wx,  T = (B0-A0)*1024 + D*wx,  D = dB - dA,  e = (wx*wy) & 1023
// Both source pixels of a row are fetched with ONE 32-bit gather at x0 (bytes x0, x0+1); where
// x0 is the last column the second byte is outside the row but its weight is exactly 0.
static void row_avx512(const XfPlan& p, const uint8_t* r0, const uint8_t* r1, uint8_t* o, int wy) {
    const __m512i m8 = _mm512_set1_epi32(0xff), m10 = _mm512_set1_epi32(1023);
    const __m512i vwy = _mm512_set1_epi32(wy);
    const int32_t* xi = p.ax.i0.data(); const int32_t* xw = p.ax.w.data();
    for (int c = 0; c < p.dw; c += 16) {
        __m512i idx = _mm512_loadu_si512(xi + c);
        __m512i wx = _mm512_loadu_si512(xw + c);
        __m512i g0 = _mm512_i32gather_epi32(idx, r0, 1);
        __m512i g1 = _mm512_i32gather_epi32(idx, r1, 1);
        __m512i a0 = _mm512_and_si512(g0, m8), a1 = _mm512_and_si512(_mm512_srli_epi32(g0, 8), m8);
        __m512i b0 = _mm512_and_si512(g1, m8), b1 = _mm512_and_si512(_mm512_srli_epi32(g1, 8), m8);
        __m512i dA = _mm512_sub_epi32(a1, a0), dB = _mm512_sub_epi32(b1, b0);
        __m512i D = _mm512_sub_epi32(dB, dA);
        __m512i H0 = _mm512_add_epi32(_mm512_slli_epi32(a0, 10), _mm512_mullo_epi32(dA, wx));
        __m512i T = _mm512_add_epi32(_mm512_slli_epi32(_mm512_sub_epi32(b0, a0), 10), _mm512_mullo_epi32(D, wx));
        __m512i e = _mm512_and_si512(_mm512_mullo_epi32(wx, vwy), m10);
        __m512i num = _mm512_sub_epi32(_mm512_add_epi32(_mm512_slli_epi32(H0, 10), _mm512_mullo_epi32(T, vwy)),
                                       _mm512_mullo_epi32(D, e));
        __m128i r = _mm512_cvtepi32_epi8(_mm512_srli_epi32(num, 20));
        int left = p.dw - c;
        __mmask16 k = left >= 16 ? static_cast<__mmask16>(0xffff) : static_cast<__mmask16>((1u << left) - 1);
        _mm_mask_storeu_epi8(o + c, k, r);
    }
}
#endif

void resize_plane_xf(const XfPlan& p, const uint8_t* src, int sstride, uint8_t* dst, int dstride, bool simd, int threads) {
    // The last source row gets a padded private copy so the 4-byte gather never reads past the plane.
    const int last = p.sh - 1;
    std::vector<uint8_t> lastrow(static_cast<size_t>(sstride) + 64, 0);
    std::memcpy(lastrow.data(), src + static_cast<size_t>(last) * sstride, static_cast<size_t>(p.sw));
#if !LS_HAVE_AVX512
    simd = false;
#endif
    (void)threads;
#ifdef _OPENMP
    #pragma omp parallel for schedule(static) num_threads(std::max(1, threads)) if (threads > 1)
#endif
    for (int r = 0; r < p.dh; ++r) {
        int y0 = p.ay.i0[r], y1 = p.ay.i1[r];
        const uint8_t* r0 = y0 == last ? lastrow.data() : src + static_cast<size_t>(y0) * sstride;
        const uint8_t* r1 = y1 == last ? lastrow.data() : src + static_cast<size_t>(y1) * sstride;
        uint8_t* o = dst + static_cast<size_t>(r) * dstride;
#if LS_HAVE_AVX512
        if (simd) { row_avx512(p, r0, r1, o, p.ay.w[r]); continue; }
#endif
        row_scalar(p, r0, r1, o, p.ay.w[r]);
    }
}
}  // namespace ls

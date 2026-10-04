// tb_fir.cpp - C-simulation testbench: fir_v2 kernel == reference model (bit-identical), for
// several lengths (1 word, non-multiples of the history, long), random and extreme signals
// (full-scale +-32768 inputs and coefficients: wrap-around and saturation), and chunked streams
// (history words taken from the previous chunk == one long call).
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "fir_v2.h"
#include "fir_ref.hpp"

static void pack(const int16_t* s, size_t nsamp, std::vector<fir_word_t>& w) {
    w.assign((nsamp + FIR_L - 1) / FIR_L, 0);
    for (size_t i = 0; i < nsamp; ++i) w[i / FIR_L](16 * (i % FIR_L) + 15, 16 * (i % FIR_L)) = ap_uint<16>((uint16_t)s[i]);
}
// run the kernel on x[0..n) with history x[-HS..-1] (x must have HS readable samples in front)
static std::vector<int16_t> run(const int16_t* x, size_t n, const std::vector<int16_t>& c) {
    const size_t HS = size_t(FIR_HW) * FIR_L;
    std::vector<fir_word_t> in, out((n + FIR_L - 1) / FIR_L), cw;
    std::vector<int16_t> buf(x - HS, x + n);
    buf.resize(HS + out.size() * FIR_L, 0);
    pack(buf.data(), buf.size(), in);
    std::vector<int16_t> cc(c);
    cc.resize(FIR_CWORDS * FIR_L, 0);
    pack(cc.data(), cc.size(), cw);
    fir_v2(in.data(), out.data(), cw.data(), fir_cnt_t((uint32_t)out.size()));
    std::vector<int16_t> y(n);
    for (size_t i = 0; i < n; ++i) y[i] = (int16_t)(uint16_t)out[i / FIR_L](16 * (i % FIR_L) + 15, 16 * (i % FIR_L)).to_uint();
    return y;
}

int main() {
    int fails = 0;
    const size_t PAD = 64;
    auto check = [&](const char* name, const std::vector<int16_t>& sig, const std::vector<int16_t>& c) {
        size_t n = sig.size();
        std::vector<int16_t> xb(PAD, 0);
        xb.insert(xb.end(), sig.begin(), sig.end());
        std::vector<int16_t> ref(n);
        fir::filter_ref(xb.data() + PAD, ref.data(), n, c.data(), FIR_TAPS);
        std::vector<int16_t> y = run(xb.data() + PAD, n, c);
        bool ok = y == ref;
        std::printf("%s %-40s n=%zu hash=%016llx\n", ok ? "PASS" : "FAIL", name, n,
                    (unsigned long long)fir::fnv1a64(ref.data(), n * 2));
        fails += !ok;
    };
    const std::vector<int16_t> c = fir::lowpass(FIR_TAPS);
    long s = 0;
    for (auto v : c) s += std::labs(v);
    std::printf("taps=%d  sum|c|=%ld: %s\n", FIR_TAPS, s,
                s * 32768L < 2147483648L ? "sum|c| x 2^15 < 2^31, the int32 sum never wraps with these coefficients"
                                         : "the int32 sum can wrap (still bit-identical)");
    for (size_t n : {16ul, 48ul, 1000ul, 4096ul, 70001ul}) {
        std::vector<int16_t> x(n);
        fir::gen_block(1, 0, x.data(), n);
        char nm[64];
        std::snprintf(nm, sizeof nm, "generated signal, n=%zu", n);
        check(nm, x, c);
    }
    {   // extreme: full-scale inputs and coefficients -> wrap-around of the int32 sum and saturation
        std::vector<int16_t> x(5000), cx(FIR_TAPS);
        for (size_t i = 0; i < x.size(); ++i) x[i] = (i / 7) % 2 ? -32768 : 32767;
        for (int k = 0; k < FIR_TAPS; ++k) cx[k] = k % 3 ? -32768 : 32767;
        check("full-scale signal and coefficients (wrap)", x, cx);
        std::vector<int16_t> r(5000);
        uint64_t z = 7;
        for (auto& v : r) { z = fir::mix64(z); v = (int16_t)(z >> 48); }
        for (auto& v : cx) { z = fir::mix64(z); v = (int16_t)(z >> 48); }
        check("random signal and random coefficients", r, cx);
    }
    {   // chunked stream: chunks with their real history == one call over the whole signal
        const size_t n = 12345 * 16 + 5, C = 1024 * 16;
        std::vector<int16_t> xb(PAD + n, 0);
        fir::gen_block(3, 0, xb.data() + PAD, n);
        std::vector<int16_t> whole = run(xb.data() + PAD, n, c), pieces;
        for (size_t off = 0; off < n; off += C) {
            std::vector<int16_t> y = run(xb.data() + PAD + off, std::min(C, n - off), c);
            pieces.insert(pieces.end(), y.begin(), y.end());
        }
        bool ok = pieces == whole;
        std::printf("%s chunked stream (history from the previous chunk) == one call\n", ok ? "PASS" : "FAIL");
        fails += !ok;
    }
    std::printf("tb_fir: taps=%d L=%d HW=%d  %s\n", FIR_TAPS, FIR_L, FIR_HW, fails ? "FAILED" : "ALL PASS");
    return fails ? 1 : 0;
}

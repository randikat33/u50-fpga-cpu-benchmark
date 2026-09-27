// bench_common.hpp - shared helpers for all v2 host (FPGA) and CPU benchmark programs.
// Header-only, C++17. Every program prints its measurements in the same machine-readable
// form so the benchmark suite parses all projects with one parser:
//
//   RESULT key=value            (one per line, value has no spaces)
//   RESULT_JSON {...}           (one line, same content as JSON; printed at exit)
//
// Mandatory keys (seconds unless the key says otherwise):
//   project, platform (fpga|cpu), impl, t_total_s, t_compute_s, ok (1|0)
// Recommended: t_setup_s, t_read_s, t_h2d_s, t_kernel_s, t_d2h_s, t_write_s, t_xclbin_s,
//   bytes_in, bytes_out, throughput_*, threads, n_cu, and workload parameters.
#pragma once
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/mman.h>
#include <unistd.h>

namespace bench {

using clk = std::chrono::steady_clock;
inline double now_s() { return std::chrono::duration<double>(clk::now().time_since_epoch()).count(); }

// ---------- result reporting ---------------------------------------------------
class Report {
    std::vector<std::pair<std::string, std::string>> kv_;
    std::mutex m_;
    static std::string esc(const std::string& s) {
        std::string o; for (char c : s) { if (c == '"' || c == '\\') o += '\\'; o += c; } return o;
    }
public:
    void set(const std::string& k, const std::string& v) {
        std::lock_guard<std::mutex> g(m_);
        std::string vv = v; std::replace(vv.begin(), vv.end(), ' ', '_');
        for (auto& p : kv_) if (p.first == k) { p.second = vv; return; }
        kv_.emplace_back(k, vv);
    }
    void set(const std::string& k, const char* v) { set(k, std::string(v)); }
    void set(const std::string& k, double v) { char b[64]; std::snprintf(b, sizeof b, "%.9g", v); set(k, std::string(b)); }
    void set(const std::string& k, int v) { set(k, std::to_string(v)); }
    void set(const std::string& k, long v) { set(k, std::to_string(v)); }
    void set(const std::string& k, long long v) { set(k, std::to_string(v)); }
    void set(const std::string& k, unsigned v) { set(k, std::to_string(v)); }
    void set(const std::string& k, unsigned long v) { set(k, std::to_string(v)); }
    void set(const std::string& k, unsigned long long v) { set(k, std::to_string(v)); }
    void add(const std::string& k, double v) {   // accumulate a timer
        double cur = 0; { std::lock_guard<std::mutex> g(m_); for (auto& p : kv_) if (p.first == k) cur = std::atof(p.second.c_str()); }
        set(k, cur + v);
    }
    void print(FILE* f = stdout) {
        std::lock_guard<std::mutex> g(m_);
        for (auto& p : kv_) std::fprintf(f, "RESULT %s=%s\n", p.first.c_str(), p.second.c_str());
        std::string j = "{";
        for (size_t i = 0; i < kv_.size(); ++i) {
            const std::string& v = kv_[i].second; char* end = nullptr; std::strtod(v.c_str(), &end);
            bool num = !v.empty() && end && *end == '\0' && v != "nan" && v != "inf";
            j += (i ? "," : "") + std::string("\"") + esc(kv_[i].first) + "\":" + (num ? v : "\"" + esc(v) + "\"");
        }
        j += "}";
        std::fprintf(f, "RESULT_JSON %s\n", j.c_str());
        std::fflush(f);
    }
};

// Scoped timer: adds elapsed seconds to report key on destruction.
class Timer {
    Report* r_; std::string k_; double t0_;
public:
    Timer(Report& r, std::string k) : r_(&r), k_(std::move(k)), t0_(now_s()) {}
    ~Timer() { r_->add(k_, now_s() - t0_); }
};

// Progress markers for the suite (processing-window detection in logs)
inline void mark(const char* what) { std::printf("MARK %s %.6f\n", what, now_s()); std::fflush(stdout); }

// ---------- command line -------------------------------------------------------
// Accepts --key value, --key=value and bare --flag (value "1").
class Args {
    std::map<std::string, std::string> m_; std::vector<std::string> pos_;
public:
    Args(int argc, char** argv) {
        for (int i = 1; i < argc; ++i) {
            std::string a = argv[i];
            if (a.rfind("--", 0) == 0) {
                auto eq = a.find('=');
                if (eq != std::string::npos) m_[a.substr(2, eq - 2)] = a.substr(eq + 1);
                else if (i + 1 < argc && std::string(argv[i + 1]).rfind("--", 0) != 0) m_[a.substr(2)] = argv[++i];
                else m_[a.substr(2)] = "1";
            } else pos_.push_back(a);
        }
    }
    bool has(const std::string& k) const { return m_.count(k) > 0; }
    std::string str(const std::string& k, const std::string& d = "") const { auto it = m_.find(k); return it == m_.end() ? d : it->second; }
    long long i64(const std::string& k, long long d) const { auto it = m_.find(k); return it == m_.end() ? d : std::stoll(it->second, nullptr, 0); }
    double f64(const std::string& k, double d) const { auto it = m_.find(k); return it == m_.end() ? d : std::stod(it->second); }
    const std::vector<std::string>& pos() const { return pos_; }
};

// ---------- memory ---------------------------------------------------------------
// Page-aligned allocation (2 MB-aligned when large) for zero-copy XRT user-ptr BOs and
// for CPU buffers (first-touch placement is done by the caller in parallel).
template <typename T>
struct AlignedBuf {
    T* p = nullptr; size_t n = 0;
    AlignedBuf() = default;
    explicit AlignedBuf(size_t count) { alloc(count); }
    void alloc(size_t count) {
        free_(); n = count; size_t bytes = std::max<size_t>(1, count * sizeof(T));
        size_t al = bytes >= (64u << 20) ? (2u << 20) : 4096u;
        bytes = (bytes + al - 1) / al * al;
        if (posix_memalign(reinterpret_cast<void**>(&p), al, bytes)) throw std::bad_alloc();
        if (al == (2u << 20)) madvise(p, bytes, MADV_HUGEPAGE);
    }
    void free_() { std::free(p); p = nullptr; n = 0; }
    ~AlignedBuf() { free_(); }
    AlignedBuf(const AlignedBuf&) = delete; AlignedBuf& operator=(const AlignedBuf&) = delete;
    AlignedBuf(AlignedBuf&& o) noexcept : p(o.p), n(o.n) { o.p = nullptr; o.n = 0; }
    T& operator[](size_t i) { return p[i]; }
    const T& operator[](size_t i) const { return p[i]; }
    T* data() { return p; }
    size_t size() const { return n; }
};

// ---------- file I/O ---------------------------------------------------------------
inline size_t file_size(const std::string& path) {
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) throw std::runtime_error("cannot open " + path);
    return static_cast<size_t>(f.tellg());
}
inline void read_into(const std::string& path, void* dst, size_t n, size_t off = 0) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) throw std::runtime_error("cannot open " + path);
    if (off) std::fseek(f, static_cast<long>(off), SEEK_SET);
    size_t got = std::fread(dst, 1, n, f); std::fclose(f);
    if (got != n) throw std::runtime_error("short read on " + path);
}
inline void write_from(const std::string& path, const void* src, size_t n, bool append = false) {
    FILE* f = std::fopen(path.c_str(), append ? "ab" : "wb");
    if (!f) throw std::runtime_error("cannot create " + path);
    size_t put = std::fwrite(src, 1, n, f);
    if (std::fclose(f) != 0 || put != n) throw std::runtime_error("short write on " + path);
}

// FNV-1a 64 checksum used for output equivalence checks between CPU and FPGA runs.
inline uint64_t fnv1a64(const uint8_t* p, size_t n, uint64_t h = 1469598103934665603ull) {
    for (size_t i = 0; i < n; ++i) { h ^= p[i]; h *= 1099511628211ull; }
    return h;
}
inline std::string hex64(uint64_t v) { char b[20]; std::snprintf(b, sizeof b, "%016llx", (unsigned long long)v); return b; }

inline int env_int(const char* n, int d) { const char* v = std::getenv(n); return v ? std::atoi(v) : d; }

}  // namespace bench

// aes_io.hpp - file I/O used identically by the FPGA host and the CPU baselines.
// Positional pread/pwrite in chunks (no stdio buffering, no extra copies): the host reads
// straight into mapped BO memory, the CPU reads straight into its first-touched buffer.
#pragma once
#include <cerrno>
#include <cstdint>
#include <algorithm>
#include <cstring>
#include <stdexcept>
#include <string>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace aesio {

struct Fd {
    int fd = -1;
    Fd() = default;
    explicit Fd(int f) : fd(f) {}
    Fd(const Fd&) = delete; Fd& operator=(const Fd&) = delete;
    Fd(Fd&& o) noexcept : fd(o.fd) { o.fd = -1; }
    Fd& operator=(Fd&& o) noexcept { if (this != &o) { close_(); fd = o.fd; o.fd = -1; } return *this; }
    ~Fd() { close_(); }
    void close_() { if (fd >= 0) ::close(fd); fd = -1; }
};

inline Fd open_in(const std::string& p) {
    int f = ::open(p.c_str(), O_RDONLY);
    if (f < 0) throw std::runtime_error("cannot open " + p + ": " + std::strerror(errno));
    return Fd(f);
}
inline uint64_t fd_size(const Fd& f) {
    struct stat st;
    if (fstat(f.fd, &st) != 0) throw std::runtime_error("fstat failed");
    return static_cast<uint64_t>(st.st_size);
}
// Creates/truncates the output and sets its final size (so chunks can be written in any order).
inline Fd open_out(const std::string& p, uint64_t size) {
    int f = ::open(p.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (f < 0) throw std::runtime_error("cannot create " + p + ": " + std::strerror(errno));
    if (ftruncate(f, static_cast<off_t>(size)) != 0) { ::close(f); throw std::runtime_error("ftruncate failed on " + p); }
    return Fd(f);
}
inline void pread_full(const Fd& f, void* dst, size_t n, uint64_t off) {
    auto* p = static_cast<char*>(dst);
    while (n) {
        ssize_t r = ::pread(f.fd, p, n, static_cast<off_t>(off));
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) throw std::runtime_error("short read");
        p += r; n -= size_t(r); off += uint64_t(r);
    }
}
inline void pwrite_full(const Fd& f, const void* src, size_t n, uint64_t off) {
    auto* p = static_cast<const char*>(src);
    while (n) {
        ssize_t r = ::pwrite(f.fd, p, n, static_cast<off_t>(off));
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) throw std::runtime_error("short write");
        p += r; n -= size_t(r); off += uint64_t(r);
    }
}
inline void fsync_fd(const Fd& f) { if (::fsync(f.fd) != 0) throw std::runtime_error("fsync failed"); }

// Deterministic pseudo-random data for --no-io runs (splitmix64, random access).
// Byte at relative offset o = byte (o % 8) of mix(seed + (o/8 + 1) * gamma). Every chunk of
// a --no-io run holds pattern(0 .. chunk_len), identically on the CPU and the FPGA host.
inline uint64_t pattern_word(uint64_t seed, uint64_t w) {
    uint64_t z = seed + (w + 1) * 0x9e3779b97f4a7c15ull;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull; z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}
inline void fill_pattern(uint8_t* p, size_t n, uint64_t seed, uint64_t rel_off = 0) {
    size_t i = 0;
    while (i < n) {
        uint64_t o = rel_off + i, w = o / 8;
        unsigned k = unsigned(o % 8);
        uint64_t z = pattern_word(seed, w);
        uint8_t b[8];
        std::memcpy(b, &z, 8);
        size_t m = std::min<size_t>(8 - k, n - i);
        std::memcpy(p + i, b + k, m);
        i += m;
    }
}
constexpr uint64_t NOIO_SEED = 0x5eed;

}  // namespace aesio

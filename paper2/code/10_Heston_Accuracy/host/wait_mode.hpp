// wait_mode.hpp - host wait modes of the card programs (protocol v0.6, Section 23 item 2).
//   --wait xrt       (default; the registered Part A host): xrt::run::wait() after the starts. In XRT 2023.1 this
//                    blocks in poll() on the device file (core/common/api/hw_queue.cpp, kds_device::exec_wait ->
//                    shim::xclExecWait); the xocl CU thread polls the CU for about 300 loops after each start
//                    and then switches to the CU interrupt (core/common/drv/xrt_cu.c, CU_DEFAULT_POLL_THRESHOLD).
//   --wait callback  : the host thread never calls into XRT to wait. A completion callback registered once per
//                    run object (xrt::run::add_callback, ERT_CMD_STATE_COMPLETED) counts finished CUs, and the
//                    host thread sleeps on a condition variable (futex) until all CUs of the pricing run are done.
// Both modes give the same moments; only the waiting differs. host_cpu_s() is the CPU time (user + system) of
// all threads of the host process, reported over the timed window.
#pragma once
#include <xrt/xrt_kernel.h>

#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/resource.h>

namespace hw {
enum Mode { XRT_WAIT = 0, CALLBACK = 1 };
inline Mode g_mode = XRT_WAIT;
inline std::mutex g_m;
inline std::condition_variable g_cv;
inline unsigned g_done = 0;

inline void set_mode(const std::string& s) {
    if (s == "xrt") g_mode = XRT_WAIT;
    else if (s == "callback") g_mode = CALLBACK;
    else throw std::runtime_error("--wait must be xrt or callback");
}
inline const char* mode_name() { return g_mode == CALLBACK ? "callback" : "xrt"; }
inline void on_done(const void*, ert_cmd_state, void*) {
    { std::lock_guard<std::mutex> l(g_m); ++g_done; }
    g_cv.notify_all();
}
// once per run object, before its first start
inline void attach(xrt::run& r) {
    if (g_mode == CALLBACK) r.add_callback(ERT_CMD_STATE_COMPLETED, on_done, nullptr);
}
// before the starts of one pricing run
inline void arm() {
    if (g_mode == CALLBACK) { std::lock_guard<std::mutex> l(g_m); g_done = 0; }
}
// after the starts: wait for all CUs of the pricing run
template <class CuVec> inline void wait_all(CuVec& cus) {
    if (g_mode == XRT_WAIT) {
        for (auto& c : cus) c.run.wait();
        return;
    }
    std::unique_lock<std::mutex> l(g_m);
    const unsigned n = (unsigned)cus.size();
    g_cv.wait(l, [n] { return g_done >= n; });
}
inline double host_cpu_s() {
    rusage u{};
    getrusage(RUSAGE_SELF, &u);
    return (double)u.ru_utime.tv_sec + 1e-6 * u.ru_utime.tv_usec + (double)u.ru_stime.tv_sec + 1e-6 * u.ru_stime.tv_usec;
}
}  // namespace hw

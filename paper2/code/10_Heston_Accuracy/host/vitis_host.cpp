// vitis_host.cpp - host for the AMD Vitis Libraries 2023.1 Monte Carlo engines (protocol v0.6 Section 23 item 5,
// descriptive). It runs the library's own kernels, unchanged (MCEuropeanHestonEngine: kernel_mc_0, QE, double, one MC
// module; MCAsianAPEngine: kernel_MCAsianAP_0, arithmetic Asian under Black-Scholes, double, two MC modules), at our
// settings, under our steady-clock protocol, and prints the same RESULT lines as our programs.
//
// The library kernels price one strike per call, with their own fixed seeds (the same draws on every call) and no
// antithetic pairs; we give them samples = 2 x our pairs, so one call simulates as many paths as our run of all three
// strikes. A strike priced from its put (puts=1) is converted to the call by parity, as in our programs.
//
// Usage: vitis_host --xclbin F --engine heston|asian --S0 x --r x --T x --K a,b,c --puts p,q,s --steps M --samples N
//                   heston: --v0 x --theta x --kappa x --xi x --rho x      asian: --sigma x
//                   [--warmup-s 3] [--window-s 10]
// Output: MARK start/end (wall time), RESULT runs, t_per_run_s, path_steps (per run, all three calls), price0..2, ok
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "xcl2.hpp"

typedef double DT;

static std::vector<double> list(const std::string& s) {
    std::vector<double> v;
    std::stringstream ss(s);
    std::string x;
    while (std::getline(ss, x, ',')) v.push_back(std::stod(x));
    return v;
}

static double wall() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}

int main(int argc, char** argv) {
    std::map<std::string, std::string> a;
    for (int i = 1; i + 1 < argc; i += 2) a[std::string(argv[i]).substr(2)] = argv[i + 1];
    auto need = [&](const char* k) {
        if (!a.count(k)) { std::fprintf(stderr, "missing --%s\n", k); std::exit(1); }
        return a[k];
    };
    const std::string xclbin = need("xclbin"), engine = need("engine");
    const bool heston = engine == "heston";
    const DT S0 = std::stod(need("S0")), r = std::stod(need("r")), T = std::stod(need("T"));
    const std::vector<double> K = list(need("K")), puts = list(need("puts"));
    const unsigned steps = (unsigned)std::stoul(need("steps")), samples = (unsigned)std::stoul(need("samples"));
    const double warm_s = a.count("warmup-s") ? std::stod(a["warmup-s"]) : 3.0;
    const double win_s = a.count("window-s") ? std::stod(a["window-s"]) : 10.0;
    DT v0 = 0, theta = 0, kappa = 0, xi = 0, rho = 0, sigma = 0;
    if (heston) {
        v0 = std::stod(need("v0")); theta = std::stod(need("theta")); kappa = std::stod(need("kappa"));
        xi = std::stod(need("xi")); rho = std::stod(need("rho"));
    } else {
        sigma = std::stod(need("sigma"));
    }

    cl_int err;
    std::vector<cl::Device> devices = xcl::get_xil_devices();
    cl::Device device = devices[0];
    cl::Context context(device, NULL, NULL, NULL, &err);
    cl::CommandQueue q(context, device, CL_QUEUE_PROFILING_ENABLE, &err);
    cl::Program::Binaries bins = xcl::import_binary_file(xclbin);
    devices.resize(1);
    cl::Program program(context, devices, bins, NULL, &err);
    if (err != CL_SUCCESS) { std::fprintf(stderr, "program failed %d\n", err); return 1; }
    cl::Kernel k(program, heston ? "kernel_mc_0" : "kernel_MCAsianAP_0", &err);
    DT* out = (DT*)aligned_alloc(4096, 4096);
    cl_mem_ext_ptr_t ext = {heston ? 16u : 7u, out, k()};
    cl::Buffer ob(context, CL_MEM_EXT_PTR_XILINX | CL_MEM_USE_HOST_PTR | CL_MEM_READ_WRITE, sizeof(DT), &ext);
    std::vector<cl::Memory> ov{ob};

    const DT q0 = 0.0, tol = 0.02;
    const unsigned maxs = 0, loop_nm = 1;
    auto price = [&](int s) {
        const bool put = puts[s] != 0;
        if (heston) {
            int j = 0;
            k.setArg(j++, loop_nm); k.setArg(j++, S0); k.setArg(j++, r); k.setArg(j++, xi); k.setArg(j++, v0);
            k.setArg(j++, theta); k.setArg(j++, kappa); k.setArg(j++, rho); k.setArg(j++, q0); k.setArg(j++, put);
            k.setArg(j++, (DT)K[s]); k.setArg(j++, T); k.setArg(j++, steps); k.setArg(j++, samples); k.setArg(j++, maxs);
            k.setArg(j++, tol); k.setArg(j++, ob);
        } else {
            int j = 0;
            k.setArg(j++, S0); k.setArg(j++, sigma); k.setArg(j++, q0); k.setArg(j++, r); k.setArg(j++, T);
            k.setArg(j++, (DT)K[s]); k.setArg(j++, (int)(put ? 1 : 0)); k.setArg(j++, ob); k.setArg(j++, tol);
            k.setArg(j++, samples); k.setArg(j++, steps); k.setArg(j++, maxs);
        }
        q.enqueueTask(k);
        q.enqueueMigrateMemObjects(ov, CL_MIGRATE_MEM_OBJECT_HOST);
        q.finish();
        double p = out[0];
        if (put) p = p + S0 - K[s] * std::exp(-r * T);   // parity, no dividends
        return p;
    };
    std::vector<double> pr(3);
    auto run = [&]() { for (int s = 0; s < 3; ++s) pr[s] = price(s); };

    const double t0 = wall();
    do run(); while (wall() - t0 < warm_s);
    const double m0 = wall();
    std::printf("MARK start %.6f\n", m0); std::fflush(stdout);
    long runs = 0;
    do { run(); ++runs; } while (wall() - m0 < win_s);
    const double m1 = wall();
    std::printf("MARK end %.6f\n", m1); std::fflush(stdout);
    bool ok = true;
    for (int s = 0; s < 3; ++s) ok = ok && std::isfinite(pr[s]);
    std::printf("RESULT engine=%s\nRESULT runs=%ld\nRESULT t_window_s=%.6f\nRESULT t_per_run_s=%.9f\n", engine.c_str(), runs, m1 - m0, (m1 - m0) / runs);
    std::printf("RESULT steps=%u\nRESULT samples=%u\nRESULT path_steps=%.1f\n", steps, samples, 3.0 * samples * steps);
    for (int s = 0; s < 3; ++s) std::printf("RESULT price%d=%.10f\nRESULT se%d=nan\n", s, pr[s], s);
    std::printf("RESULT ok=%d\n", ok ? 1 : 0);
    return ok ? 0 : 2;
}

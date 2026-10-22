// native_serve.cpp - "serve" mode of the CPU-native programs for co-execution (addendum v0.7). Compiled with
// -DNATIVE_SRC="hq_native.cpp" (build/hq_native_serve) or -DNATIVE_SRC="hs_native.cpp" (build/hs_native_serve).
// The original program is included unchanged, its main renamed; each command calls it once for one pair range,
// so the numerics are exactly those of build/hq_native and build/hs_native.
//
// Usage: hq_native_serve <the fixed arguments of hq_native: --case, --scheme, --steps, --seed, --threads, ...>
// stdin : "R <id> <a0> <a1>"  one pricing part over pairs [a0, a1);  "Q" quit
// stdout: "READY" once, then per command "BEGIN <id>", the program's RESULT lines, "END <id> <wall seconds>"
#include <chrono>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

#define main native_main
#include NATIVE_SRC
#undef main

int main(int argc, char** argv) {
    std::vector<std::string> base(argv + 1, argv + argc);
    std::printf("READY\n");
    std::fflush(stdout);
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        if (line[0] == 'Q') break;
        unsigned long long id = 0, a0 = 0, a1 = 0;
        if (std::sscanf(line.c_str(), "R %llu %llu %llu", &id, &a0, &a1) != 3) {
            std::printf("ERROR bad command\n");
            std::fflush(stdout);
            continue;
        }
        std::vector<std::string> args = base;
        for (const char* x : {"--a0", "--a1", "--runs", "--warmup", "--warmup-s", "--window-s"}) args.push_back(x), args.push_back("");
        size_t n = base.size();
        args[n + 1] = std::to_string(a0); args[n + 3] = std::to_string(a1); args[n + 5] = "1"; args[n + 7] = "0";
        args[n + 9] = "0"; args[n + 11] = "0";
        std::vector<char*> av{argv[0]};
        for (auto& s : args) av.push_back(&s[0]);
        av.push_back(nullptr);
        std::printf("BEGIN %llu\n", id);
        std::fflush(stdout);
        const auto t0 = std::chrono::steady_clock::now();
        native_main((int)av.size() - 1, av.data());
        const double dt = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        std::printf("END %llu %.6f\n", id, dt);
        std::fflush(stdout);
    }
    return 0;
}

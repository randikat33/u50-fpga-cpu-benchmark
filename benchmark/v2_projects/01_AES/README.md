# 01_AES — AES-256-CTR, v2 (Alveo U50 vs Xeon Gold 5318Y)

One HLS kernel (`aes256ctr`) does both encryption and decryption, and all CUs live in one xclbin.
On the CPU side there are four baselines (hand-written AVX-512 VAES, hand-written AES-NI, a portable T-table
implementation and OpenSSL 3 EVP). Every implementation, including the kernel C-sim, has been checked to be
bit-exact against the textbook reference, the NIST SP 800-38A F.5.5/F.5.6 vectors and the `openssl enc` CLI.

## 1. Workload

| item | value |
|---|---|
| algorithm | AES-256 in CTR mode (NIST SP 800-38A). The full 128-bit big-endian counter block is incremented once per block, and the carry crosses the 64-bit boundary (same as OpenSSL). |
| input / output | a raw file of any size. The output has the same size and **no header** (v1 wrote a header with the IV; in v2 the IV is given on the CLI). |
| parameters | `--key` 64 hex digits and `--iv` 32 hex digits. Defaults are the NIST F.5.5 key and IV. `--mode enc\|dec` runs the identical operation and is only recorded. |
| paper sizes | 1, 5, 10, 50, 100, 500, 1024 and 2048 MiB; encrypt and decrypt; input and output files on SSD and on tmpfs; plus `--no-io` compute-only runs |
| chunking rule | the chunk (or segment) that starts at byte `B` uses counter `IV + B/16`. `B` is always a multiple of 64. |

## 2. v1 diagnosis (thesis version, from the reports in `3_hls_reports`, `4_timing_reports` and `5_utilization_reports`)

* **Two xclbins with the same logic.** `aes256CtrKernel_512` and `aes256CtrDecryptKernel_512` differ only in their
  names and comments, yet switching between encrypt and decrypt meant a bitstream swap of about 9 s. CTR decryption
  *is* encryption.
* **Too few register stages.** HLS reports an iteration latency of **15** for the 14-round core (about one register per
  round, with 4 S-box lookups, MixColumns and ARK chained in each stage). The HLS slack was −0.00 ns. The routed kernel
  clock met 300 MHz with **WNS = +0.019 ns**, so the design had no margin.
* **Area.** The routed kernel utilisation shows **6 instances of about 55.3 k LUT, 20.6 k FF, 37 BRAM and 16 URAM each**
  (332 k LUT = 46.9 % of the user budget) in *each* of the two xclbins. The host used `NUM_CUS = 3` with 2 buffer slots
  per CU. (The brief says "3 CUs of about 110 k LUT"; the routed reports show 6 × 55 k per xclbin, which is the same total.)
  The FIFOs were bound to URAM (16 URAM per CU) for no benefit. The HLS estimate for the core was 56.6 k LUT.
* **Throughput was PCIe/host bound, not compute bound.** With 512 bits per cycle at 300 MHz, one CU gives about 19 GB/s,
  which is already above PCIe Gen3 x16. The extra CUs mainly cost area.
* **32-bit size argument.** `int nbytes_total` limits a call to 2 GiB. The counter was `iv + (wi*4+b)` with an `int` index.
* **CPU baseline.** A textbook byte-wise AES that **re-expanded the key for every block**, with no AES-NI and no
  T-tables. A reviewer would reject it as the only baseline.

## 3. v2 FPGA micro-architecture

```
             +-----------------------------  aes256ctr (one CU)  ------------------------------+
 HBM[src] -->| rd_proc --s_data(64, LUTRAM)------------------------------------+               |
  512-bit    |   |  \--n_ks--> ks_proc: counter x4 -> ARK0 -> 14 x [SB | SR+MC+ARK]            |
  bursts 256 |   |              (4 lanes, 30 register stages, II=1) --s_ks(64)--> wr_proc XOR -->| HBM[dst]
             |   \----n_wr---------------------------------------------------------->          |
 HBM[prm] -->| ks_proc reads 4 words once: 15 round keys + IV;  s_axilite: blk_off(64), n_words(32)
             +--------------------------------------------------------------------------------+
```

* **One kernel, both modes, one xclbin, 2 CUs** (`cfg/link.cfg`: `nk=aes256ctr:2:aes256ctr_1.aes256ctr_2`,
  with `src/dst/prm` → `HBM[0..2]` and `HBM[3..5]`). The arguments are named `src`/`dst` because HLS renames `in` to `in_r`,
  which would break `sp=`.
* **512-bit word = 4 AES blocks.** Block `b` is bytes `16b..16b+15` of the word, and byte `j` is bits `8j+7:8j`
  (ap_uint<512> is 64 raw little-endian bytes, so the host sees the plain byte stream).
* **Hand-retimed keystream pipeline.** Every register is an explicit C++ variable that is updated in reverse stage order
  (the loop body *is* the next-state function), so register placement is fixed and the C-sim is cycle-true:
  * The counter uses 4 lanes. Lane `b` counts `c0+b` in steps of +4. It is a **pipelined 128-bit counter**: the low 64 bits
    get +4 and the carry-out (the AND of 62 bits) is registered. The high 64 bits add that carry one cycle later, and the
    low half is delayed by one register, so `(H, Ld)` is always consistent. The longest counter path is a 64-bit carry chain.
    `c0 = IV + blk_off` is computed once, before the loop.
  * `S[0] = ctr ^ rk0`. Round r then has **two register stages**: `A[r] = SubBytes(S[r-1])` and
    `S[r] = ShiftRows/MixColumns(A[r]) ^ rk[r]` (no MixColumns when r = 14).
    Depth = 1 + 1 + 28 = **30** (`AES_KS_DEPTH`). The loop runs `n + 30` trips, and a 30-bit valid shift register
    selects the words to write.
* **S-box = logic.** Output bit k is bit `x[5:0]` of one of 4 literal 64-bit constants, selected by `x[7:6]`.
  That gives 32 constants in a complete-partitioned static array, so there is no ROM and no port contention between
  the 896 parallel lookups. Vivado maps each bit to one 8-input function (4×LUT6 + MUXF7/F8, about 0.4 ns).
  This option was chosen over BRAM T-boxes, which would need 448 BRAM18 per CU for 14 rounds × 4 lanes × 16 lookups
  with 2 ports each, and whose placement is constrained by the BRAM columns. It was also chosen over composite-field
  S-boxes, which are 4 to 6 LUT levels deep and give lower Fmax for similar LUT count. The C-sim uses the table,
  and the testbench proves the bit-sliced form is equal for all 256 inputs.
* **Tail handling.** The host pads the last word with zeros, the kernel processes whole words, and the host writes only
  `len` bytes, so the extra keystream is discarded.
* **Dataflow.** The top level is canonical: 3 processes and 4 point-to-point streams. Each scalar and each pointer has
  exactly one consumer, and the word count reaches ks/wr through one-shot streams. The data FIFO holds the ~30 words that
  wait for their keystream (depth 64 ≥ 32, so the reader never stalls in steady state). m_axi uses 256-beat bursts,
  16 outstanding transactions, `latency=0` and 512-bit data. The `prm` port is a minimal 4-word read-only adapter.

**Throughput equation (per CU):** 4 blocks × 128 bit × Fmax = 512 bit × 300 MHz = 153.6 Gbit/s = **19.2 GB/s**
(≈ 17.9 GiB/s). PCIe Gen3 x16 gives about 12–13 GB/s *per direction*, shared by all CUs, and each byte crosses it twice
(H2D and D2H) on a full-duplex link. **The system is PCIe/host bound**: one CU can compute a 256 MiB chunk in about 14 ms,
while moving the chunk each way takes about 22 ms. A second CU does not add compute; it lets chunk k+1 upload while chunk k
computes or downloads. More CUs would only add area, so the design uses **2 CUs**.

**Resource estimate** (hand estimate for UltraScale+; the HLS estimate will be higher because it prices the constant
muxes as generic muxes):

| unit | LUT | FF | BRAM36 | DSP |
|---|---|---|---|---|
| S-box (8-in function ×8 bits) | 32 | – | – | – |
| one round, one lane (16 S-box + MixColumns/ARK ≈ 1.5 LUT/bit + 2×128 FF) | ≈ 700 | 256 | – | – |
| **one block core** (13 full rounds + last round + ARK0 + pipelined counter) | **≈ 10.2 k** | **≈ 3.9 k** | 0 | 0 |
| 4 cores | ≈ 41 k | ≈ 15.5 k | 0 | 0 |
| 2 × 64-deep 512-bit LUTRAM FIFOs | ≈ 1.2 k | ≈ 0.1 k | 0 | 0 |
| 3 m_axi adapters + s_axilite + control | ≈ 4–5 k | ≈ 5 k | ≈ 6 | 0 |
| **per CU** | **≈ 46 k (6.5 %)** | **≈ 21 k (1.4 %)** | **≈ 6 (0.5 %)** | 0 |
| **2 CUs** | ≈ 92 k (13 %) | ≈ 42 k | ≈ 12 | 0 |

**Clock:** 300 MHz (platform clock 0). Every stage is 2–3 LUT levels deep, which should leave a clear positive WNS,
unlike v1's +0.019 ns. The expected limiters are routing congestion and the fan-out of the round-key registers (4 loads
per bit, plus phys_opt replication in `link.cfg`).

## 4. v2 host (`host/aes_fpga.cpp`, XRT native API)

* `xrt::device` → `load_xclbin`. CUs are detected by opening `aes256ctr:{aes256ctr_i}` for i = 1, 2, … until one fails
  (under xrt_sim, `--cus`, default 2). The host uses `min(CUs, chunks)` CUs.
* **Buffers are allocated once per CU**: `src` and `dst` BOs of `min(chunk, size)` bytes (≤ 1 GiB, enforced by
  `--chunk-mb ≤ 1024`), plus a 256-byte `prm` BO, each from the CU's own memory group. If allocation fails, the host
  exits with a message that says to reduce `--chunk-mb` or `--cus`. One `xrt::run` object per CU is reused.
* **Self-test:** every CU encrypts NIST F.5.5 (exactly one word) before timing starts (`kat_ok`). Then the user's key
  schedule and IV are loaded into `prm`.
* **Pipeline:** there is one worker thread per CU, and the workers pull chunk indices from an atomic counter. Each chunk goes
  `pread` **directly into `bo.map()`** → pad the last word → `sync(H2D, words×64)` → `set_arg(blk_off = off/16, n_words)`,
  `start`, `wait2` → `sync(D2H, words×64)` → `pwrite(off)` from `bo.map()`. The output file is pre-sized, so chunks can
  land in any order and the CUs overlap freely. Only the used byte range is synced.
* **Timers.** `t_xclbin_s`, `t_alloc_s`, `t_setup_s` (KAT and params), and `t_read/h2d/kernel/d2h/write_s` are phase sums
  over the worker threads. `t_accel_window_s` is the wall-clock window between `MARK start` and `MARK end`.
  `t_compute_s` is defined as follows:
  * `--no-io`: `t_compute_s` = the window (`compute_def=window`).
  * File mode: reads and writes are interleaved in the window, so `t_compute_s = window × (h2d+kernel+d2h)/(h2d+kernel+d2h+read+write)`
    (`compute_def=window_x_accel_share`). **Use `--no-io` runs for the compute number in the paper.**
  * `throughput_gbps = bytes/t_compute_s`, `e2e_gbps = bytes/(window + fsync)`. `kernel_gbps_per_cu`, `h2d_gbps_per_stream`
    and `d2h_gbps_per_stream` are diagnostics.
* `--verify`: in file mode, the host re-reads the files after the window and compares them with the multi-threaded T-table
  reference (untimed). In `--no-io` mode, each chunk is compared after D2H inside the window (`verify_in_window=1`, so do
  not use those timings). `--checksum` gives the FNV-1a of the output file.
* `--no-io`: every chunk carries the same generated pattern (the BO is filled once, untimed, `t_gen_s`). Each chunk is still
  transferred both ways. The CPU `--no-io` input is defined the same way, byte for byte.

## 5. v2 CPU (`cpu/aes_cpu.cpp`, `cpu/aes_x86.hpp`, `common/aes_ttable.hpp`)

| impl | kernel | isa key |
|---|---|---|
| `vaes` | 4 zmm (16 blocks) per iteration, 13×`_mm512_aesenc_epi128` + last round. Counters are built with AVX-512: the low counters live in two 8-qword vectors (+16 per iteration), are byte-swapped with `vpshufb` and interleaved with the byte-swapped high half via `unpacklo/hi`. A scalar check routes groups that cross the 64-bit boundary to a carry-safe builder. The tail is padded into a scratch buffer. | `avx512-vaes` |
| `aesni` | 8 × xmm per iteration (Intel pipelined CTR); a carry-safe counter build when needed | `aesni-sse` |
| `ttable` | rijndael-alg-fst: 4 × 256 32-bit tables, 16 lookups + 4 XOR per round. No AES or SIMD intrinsics; `sim_test` confirms with objdump that the path has no AES instructions. This is the "no crypto extensions" architectural comparison. | `scalar-ttable` |
| `openssl` | `EVP_aes_256_ctr`, one context per OpenMP thread. The key schedule is set once, and each segment re-inits only the IV (`IV + off/16`). OpenSSL 3 uses AES-NI for CTR (it has no VAES CTR path). | `openssl-evp` |

All four implementations run on **block-aligned segments** (`--seg-kb`, default 4 MiB, a multiple of 256 bytes) under
`omp parallel for schedule(static)`. The input and output buffers (`bench::AlignedBuf`, 2 MiB-aligned with THP) are
**first-touched in parallel with the same schedule**. Other details:

* I/O uses the same `pread`/`pwrite` code and `--chunk-mb` as the host (sequential on the CPU).
* A NIST known-answer test runs through the full parallel driver before timing, and one warm-up segment runs untimed.
* `--verify` compares against the T-table reference, or against the textbook reference when the impl is `ttable`.
* Flags: `-O3 -march=native -mprefer-vector-width=512 -fopenmp -fno-math-errno` (no fast-math; integer code only).
  There are no auto-vectorisation dependencies; the hot loops are intrinsics or table code.
* Run all four with `scripts/run_all_cpu.sh <args>` (this is the `--impl all` equivalent; each run prints its own
  RESULT block).

## 6. Fairness

* **Identical on both sides:** the algorithm and counter semantics (bit-exact, cross-checked against each other and against
  the OpenSSL CLI), the key and IV, the data (files, or the same `--no-io` pattern), the file I/O code and chunk size,
  the untimed self-tests and verification, and the report keys.
* **What differs:**
  * The FPGA pays two PCIe transfers per byte, and its window includes them; the CPU works in place in DRAM.
  * The FPGA host does two concurrent `pread`/`pwrite` streams, while the CPU does one sequential stream.
  * The CPU uses 24 cores; the FPGA host uses 2 threads plus XRT threads.
* **Energy:** report card power (xbutil) **and** socket power (RAPL), because the host socket is not idle during FPGA runs.

## 7. Build and run

```bash
source /tools/Xilinx/Vitis/2023.1/settings64.sh; source /opt/xilinx/xrt/setup.sh
make env_check
make cpu host                   # build/aes_cpu, build/aes_fpga
make csynth                     # HLS only (~10 min); check II/latency in build/hls/aes256ctr
make xclbin                     # build/aes.xclbin (1-4 h), then: make fpga_reports
export OMP_PROC_BIND=close OMP_PLACES=cores
numactl -N0 -m0 taskset -c 0-23 build/aes_cpu --impl vaes --mode enc --in /data/f_1024.bin --out /tmp/o.bin --threads 24
numactl -N0 -m0 taskset -c 0-23 build/aes_cpu --impl ttable --mode enc --no-io --size-mb 2048
numactl -N0 -m0 build/aes_fpga --xclbin build/aes.xclbin --mode dec --in /data/f_1024.enc --out /tmp/o.bin --chunk-mb 256
numactl -N0 -m0 build/aes_fpga --xclbin build/aes.xclbin --mode enc --no-io --size-mb 2048 --verify
scripts/run_all_cpu.sh --mode enc --no-io --size-mb 1024
```

CLI:
* `aes_cpu --impl vaes|aesni|ttable|openssl --mode enc|dec (--in F [--out F] | --no-io --size-mb N) [--key HEX64] [--iv HEX32] [--threads N] [--chunk-mb 256] [--seg-kb 4096] [--verify] [--checksum] [--fsync]`
* `aes_fpga --xclbin X --mode enc|dec (--in F [--out F] | --no-io --size-mb N) [--key HEX64] [--iv HEX32] [--chunk-mb 256] [--device 0] [--cus N] [--verify] [--checksum] [--fsync]`

**Sandbox `make sim_test`** (2 cores, no card):

```
tb_aes: 172 passed, 0 failed          (S-box 256/256, NIST F.5.5/F.5.6 x6 impls, 22 size/offset/carry
                                        cases x 6 impls, enc/dec round trips, 5-chunk split, n_words=0)
PASS: cpu 4 impls == openssl CLI, --verify ok   (sizes 1, 63, 64, 65, 4097, 3 MiB+7, 2.5 MiB+3; normal and carry IV)
PASS: host(xrt_sim) == cpu, --verify ok, KAT ok (2 CUs, 1 MiB chunks: 3-4 chunks + tail; carry IV)
PASS: host --no-io 5 MiB (5 chunks, 2 CUs) verified;  host compiles against real XRT 2.16 headers
SUMMARY: 36 passed, 0 failed
THROUGHPUT (sandbox, --no-io 256 MiB, 2 threads): vaes 10.7, aesni 10.1, openssl 9.2, ttable 0.40 GB/s
                        (1 thread:                vaes 5.8,  aesni 5.0,  openssl 4.5,  ttable 0.20 GB/s)
```

The sandbox hypervisor hides the VAES CPUID bit even though the instructions execute. `sim_test` probes for VAES,
builds with `CPU_ISA="-mvaes -mvpclmulqdq"` and sets `AES_FORCE_VAES=1`. On SERVER, `-march=native` enables VAES directly.

## 8. Synthesis risks and what to check

1. **S-box constant select** (`C[k][hi] >> lo` on a complete-partitioned static const array): check that csynth reports
   no memory for `C` in `ks_proc` and that the loop is II=1. If HLS infers a ROM or misses II, replace `sbox_logic` with a
   256-case `switch` (sparsemux), or with `BIND_STORAGE type=rom_1p impl=lutram` plus one ROM per lookup.
2. **Hand-retimed loop:** HLS must keep `S`, `A`, `H`, `L`, `Ld`, `cy` and `v` as loop-carried registers with iteration
   latency ≤ 2 and II=1. If it reports II=2 because of the reverse-order updates, check for "unable to schedule" on `S`
   and put the round body into an `INLINE` helper per stage. Expect `ks_proc` pipeline depth 1–2 (the depth is in the
   registers, not in HLS stages).
3. **Estimated clock** in csynth should be ≤ 2.8 ns. After routing, check the WNS of `clk_kernel_00` in
   `*timing_summary_routed.rpt`. If timing fails, first raise phys_opt, then add a register after the S-box select (3 stages
   per round, `AES_KS_DEPTH` 44), or lower the clock to 250 MHz with `[clock] freqHz=` (throughput is PCIe-bound anyway).
4. **Burst inference:** `rd_proc` and `wr_proc` must report "Inferred burst of length variable" (see `m_axi` in the
   csynth log). `wr_proc` reads two streams inside the burst loop; if the burst is rejected, merge the XOR into
   `ks_proc`'s output and keep `wr_proc` as a pure `out[i] = s.read()` loop.
5. **Unrolled setup loops:** the 240 constant slices of `pw` must not become 512-bit barrel shifters. Check the LUT count
   of `ks_proc`'s setup block (it should be small).
6. **`max_write_burst_length=2 / num_*_outstanding=1`** on the unused channels are there to shrink the adapters. If a tool
   version rejects them, remove them.
7. **`sp=aes256ctr_1.src:HBM[0]`**: the argument names in `kernel.xml` must be `src/dst/prm`. Check `xclbinutil --info`
   or `link.log`.
8. **Area:** if the design is over budget (unlikely at 13 %), use `nk=aes256ctr:1` (the host adapts automatically).
   Never fewer than 4 lanes per word.
9. **Not verifiable here:** C/RTL co-simulation, real HBM bank sizes (every BO is ≤ 1 GiB, and 2 CUs need 1.0 GiB of HBM at
   256 MiB chunks), XRT concurrency of `sync` across CUs, and whether the host throughput limit is `pread` or PCIe.

## 9. Expected results (honest prediction)

* **FPGA compute window (`--no-io`)**: PCIe-bound, **≈ 8–11 GB/s**. Each chunk crosses PCIe twice, with H2D and D2H
  overlapping across the 2 CUs. Kernel-only throughput (`kernel_gbps_per_cu`) should be 12–19 GB/s, limited by HBM AXI
  bursts rather than the AES core. v1 used the same link, so v2's gain comes mostly from the host pipeline and one xclbin
  (no 9 s swap), not from compute.
* **CPU, 24 cores (Ice Lake-SP, 2.1–3.4 GHz):**
  * `vaes`: ≈ 3–5 GB/s per core, which would be 70–120 GB/s ideally, but in-place CTR moves 2 streams through 8-channel
    DDR4-2933. Expect a **memory-bandwidth-bound ≈ 25–40 GB/s**.
  * `aesni` and `openssl`: ≈ 20–35 GB/s.
  * `ttable`: ≈ 0.15–0.2 GB/s per core → **≈ 3–5 GB/s**.
* **So the CPU very likely wins raw throughput** with VAES or AES-NI (about 2.5–4× the PCIe-bound FPGA). The FPGA should
  still beat the no-crypto-extension `ttable` baseline by about 2–3×. With files on SSD, `e2e_gbps` on both platforms will
  be set by the SSD (≈ 2–3 GB/s for NVMe Gen3, or DRAM speed on tmpfs), and the platforms will look similar.
* **Energy per GB:**
  * FPGA: about 25 W for the card plus a socket that is not idle (≈ 60–80 W RAPL) → **≈ 9–12 J/GB system**, or
    ≈ 2.5–3 J/GB card-only.
  * CPU `vaes`: ≈ 165 W TDP plus DRAM ≈ 190 W at 30 GB/s → **≈ 6 J/GB**.
  * `ttable`: ≈ 190 W at 4 GB/s → ≈ 45–60 J/GB.

  The FPGA wins energy only against `ttable`, or under card-only accounting. The paper should report both accountings.
* **When the FPGA would win:** with a Gen4/Gen5 or P2P/network-attached data path (SmartNIC-style, data never crosses to
  host DRAM). There the 19 GB/s per CU at about 25 W is attractive. This is the conclusion the data should support.

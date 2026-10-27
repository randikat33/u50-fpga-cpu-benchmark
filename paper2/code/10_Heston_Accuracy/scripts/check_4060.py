#!/usr/bin/env python3
"""check_4060.py - read-only checks of the RTX 4060 desktop before any measurement (addendum v0.9 preparation).
Prints the GPU, driver, persistence mode, power limits, clocks, the supported SM clocks, whether NVML's
total-energy counter and throttle reasons work, and the RAPL domains with their names and readability."""
import glob, os, time
import pynvml as nv

def s(x):
    return x.decode() if isinstance(x, bytes) else x

nv.nvmlInit()
h = nv.nvmlDeviceGetHandleByIndex(0)
print("GPU:", s(nv.nvmlDeviceGetName(h)), "| driver:", s(nv.nvmlSystemGetDriverVersion()), "| CUDA driver API:", nv.nvmlSystemGetCudaDriverVersion())
for name, f in (("persistence mode", lambda: nv.nvmlDeviceGetPersistenceMode(h)),
                ("power limit (W)", lambda: nv.nvmlDeviceGetPowerManagementLimit(h) / 1000),
                ("power limit range (W)", lambda: [v / 1000 for v in nv.nvmlDeviceGetPowerManagementLimitConstraints(h)]),
                ("default power limit (W)", lambda: nv.nvmlDeviceGetPowerManagementDefaultLimit(h) / 1000),
                ("SM count", lambda: nv.nvmlDeviceGetNumGpuCores(h) // 128),
                ("max SM clock (MHz)", lambda: nv.nvmlDeviceGetMaxClockInfo(h, nv.NVML_CLOCK_SM)),
                ("max memory clock (MHz)", lambda: nv.nvmlDeviceGetMaxClockInfo(h, nv.NVML_CLOCK_MEM)),
                ("current SM clock (MHz)", lambda: nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM)),
                ("temperature (C)", lambda: nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)),
                ("PCIe link", lambda: f"gen {nv.nvmlDeviceGetCurrPcieLinkGeneration(h)} x{nv.nvmlDeviceGetCurrPcieLinkWidth(h)}"),
                ("ECC mode", lambda: nv.nvmlDeviceGetEccMode(h))):
    try:
        print(f"{name}: {f()}")
    except Exception as e:
        print(f"{name}: not available ({e})")
try:
    mem = nv.nvmlDeviceGetSupportedMemoryClocks(h)
    for m in mem[:2]:
        sm = nv.nvmlDeviceGetSupportedGraphicsClocks(h, m)
        print(f"supported SM clocks at memory {m} MHz: {len(sm)} values, {max(sm)}..{min(sm)}; e.g. {sm[:8]}")
except Exception as e:
    print("supported clocks: not available", e)
try:
    e0 = nv.nvmlDeviceGetTotalEnergyConsumption(h); time.sleep(1.0); e1 = nv.nvmlDeviceGetTotalEnergyConsumption(h)
    print(f"NVML total energy counter: WORKS (idle {(e1 - e0) / 1000:.1f} W over 1 s)")
except Exception as e:
    print("NVML total energy counter: NOT SUPPORTED, power will be integrated:", e)
try:
    print("power now (W):", nv.nvmlDeviceGetPowerUsage(h) / 1000)
    print("throttle reasons now: 0x%x" % nv.nvmlDeviceGetCurrentClocksThrottleReasons(h))
except Exception as e:
    print("power/throttle:", e)
print("RAPL domains:")
for d in sorted(glob.glob("/sys/class/powercap/intel-rapl:*")):
    name = open(os.path.join(d, "name")).read().strip()
    try:
        a = int(open(os.path.join(d, "energy_uj")).read()); time.sleep(0.5); b = int(open(os.path.join(d, "energy_uj")).read())
        print(f"  {os.path.basename(d)}: {name}, readable, {(b - a) / 0.5e6:.1f} W now")
    except PermissionError:
        print(f"  {os.path.basename(d)}: {name}, NOT readable (sudo chmod a+r .../energy_uj)")

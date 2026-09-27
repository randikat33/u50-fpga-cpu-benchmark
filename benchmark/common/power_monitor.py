#!/usr/bin/env python3
"""
Unified power / thermal sampler (same code for EVERY benchmark and platform).

Columns written (one row per sample, default 10 Hz):
  t_unix            wall-clock time (s, float)  -> aligned with run_measured.py markers
  pkg0_w, dram0_w   RAPL socket-0 package and socket-0 DRAM power (W)
  pkg1_w, dram1_w   RAPL socket-1 (idle socket, recorded for transparency only)
  card_w            Alveo U50 board power = 12V_PEX + 12V_AUX + 3V3_PEX rails (W)
  card_src          which sensor produced card_w (xmc | hwmon | none)
  fpga_temp_c, cpu0_temp_c

Usage:  power_monitor.py OUT.csv [interval_s]      (stop with SIGTERM / SIGINT)
        power_monitor.py --probe                   (print what sensors were found)
        power_monitor.py --temps                   (print 'fpga_c cpu0_c' once)
"""
import glob, os, re, signal, sys, time

# ----------------------------------------------------------------------------
# RAPL
# ----------------------------------------------------------------------------
def rapl_domains():
    """Return dict name->(energy_path, max_range_uj) for pkg0, dram0, pkg1, dram1."""
    out = {}
    for pkg in sorted(glob.glob("/sys/class/powercap/intel-rapl:[0-9]")):
        idx = pkg.rsplit(":", 1)[1]
        def add(key, d):
            e = os.path.join(d, "energy_uj")
            if os.access(e, os.R_OK):
                try:
                    mx = int(open(os.path.join(d, "max_energy_range_uj")).read())
                except Exception:
                    mx = 2 ** 32
                out[key] = (e, mx)
        add(f"pkg{idx}", pkg)
        for sub in glob.glob(pkg + ":*"):
            try:
                name = open(os.path.join(sub, "name")).read().strip()
            except Exception:
                continue
            if name == "dram":
                add(f"dram{idx}", sub)
    return out

# ----------------------------------------------------------------------------
# Alveo card power: prefer XMC rail sensors (same source as XRT power_profile)
# ----------------------------------------------------------------------------
RAILS = [("xmc_12v_pex_vol", "xmc_12v_pex_curr"),
         ("xmc_12v_aux_vol", "xmc_12v_aux_curr"),
         ("xmc_3v3_pex_vol", "xmc_3v3_pex_curr")]

def _first_int(path):
    try:
        s = open(path).read()
    except Exception:
        return None
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None

def find_xmc_dir():
    for d in glob.glob("/sys/bus/pci/devices/*/xmc*") + glob.glob("/sys/bus/pci/devices/*/*/xmc*"):
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "xmc_12v_pex_curr")):
            return d
    return None

def find_hwmon_power():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            name = open(os.path.join(d, "name")).read().strip().lower()
        except Exception:
            continue
        if any(k in name for k in ("xocl", "xclmgmt", "xmc", "alveo", "u50")):
            for f in ("power1_input", "power1_average"):
                p = os.path.join(d, f)
                if os.access(p, os.R_OK):
                    return p
    return None

def find_fpga_temp():
    xmc = find_xmc_dir()
    if xmc:
        for f in ("xmc_fpga_temp", "xmc_se98_temp0"):
            p = os.path.join(xmc, f)
            if os.path.exists(p):
                return p
    return None

def find_cpu0_temp():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(os.path.join(d, "name")).read().strip() != "coretemp":
                continue
        except Exception:
            continue
        for lab in glob.glob(os.path.join(d, "temp*_label")):
            if open(lab).read().strip() == "Package id 0":
                return lab.replace("_label", "_input")
    return None

class Card:
    def __init__(self):
        self.xmc = find_xmc_dir()
        self.hwmon = find_hwmon_power()
        self.src = "xmc" if self.xmc else ("hwmon" if self.hwmon else "none")

    def watts(self):
        if self.xmc:
            tot, ok = 0.0, False
            for v, c in RAILS:
                mv = _first_int(os.path.join(self.xmc, v))
                ma = _first_int(os.path.join(self.xmc, c))
                if mv is not None and ma is not None:
                    tot += mv * ma / 1e6
                    ok = True
            return tot if ok else float("nan")
        if self.hwmon:
            uw = _first_int(self.hwmon)
            return uw / 1e6 if uw is not None else float("nan")
        return float("nan")

def read_temps():
    f, c = find_fpga_temp(), find_cpu0_temp()
    ft = _first_int(f) if f else None
    ct = _first_int(c) if c else None
    # xmc reports C, coretemp reports milli-C
    if ft is not None and ft > 1000:
        ft /= 1000.0
    if ct is not None:
        ct /= 1000.0
    return ft, ct

def xbutil_fpga_temp():
    """Fallback when sysfs temperature is not readable."""
    import subprocess
    try:
        out = subprocess.run(["xbutil", "examine", "-r", "thermal"], capture_output=True,
                             text=True, timeout=20).stdout
        m = re.search(r"FPGA\s+(\d+)\s*C", out)
        return float(m.group(1)) if m else None
    except Exception:
        return None

# ----------------------------------------------------------------------------
def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        print("RAPL domains :", {k: v[0] for k, v in rapl_domains().items()} or "NONE (run: sudo chmod a+r .../energy_uj)")
        c = Card()
        print("Card power   :", c.src, c.xmc or c.hwmon, "->", round(c.watts(), 2), "W")
        print("FPGA temp    :", find_fpga_temp(), read_temps()[0], "| xbutil:", xbutil_fpga_temp())
        print("CPU0 temp    :", find_cpu0_temp(), read_temps()[1])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--temps":
        ft, ct = read_temps()
        if ft is None:
            ft = xbutil_fpga_temp()
        print(f"{ft if ft is not None else 'nan'} {ct if ct is not None else 'nan'}")
        return

    out = sys.argv[1]
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
    doms = rapl_domains()
    keys = ["pkg0", "dram0", "pkg1", "dram1"]
    card = Card()
    ftp, ctp = find_fpga_temp(), find_cpu0_temp()

    running = [True]
    def stop(*_):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def read_e():
        vals = {}
        for k in keys:
            if k in doms:
                try:
                    vals[k] = int(open(doms[k][0]).read())
                except Exception:
                    vals[k] = None
        return vals

    with open(out, "w", buffering=1) as fh:
        fh.write("t_unix," + ",".join(f"{k}_w" for k in keys) + ",card_w,card_src,fpga_temp_c,cpu0_temp_c\n")
        prev_e, prev_t = read_e(), time.time()
        next_t = prev_t + interval
        while running[0]:
            time.sleep(max(0.0, next_t - time.time()))
            next_t += interval
            now, e = time.time(), read_e()
            dt = now - prev_t
            row = [f"{now:.4f}"]
            for k in keys:
                if k in doms and e.get(k) is not None and prev_e.get(k) is not None:
                    de = e[k] - prev_e[k]
                    if de < 0:                      # counter wrap-around
                        de += doms[k][1]
                    row.append(f"{de / 1e6 / dt:.3f}")
                else:
                    row.append("")
            w = card.watts()
            row.append("" if w != w else f"{w:.3f}")
            row.append(card.src)
            ft = _first_int(ftp) if ftp else None
            if ft is not None and ft > 1000:
                ft /= 1000.0
            ct = _first_int(ctp) if ctp else None
            row.append("" if ft is None else f"{ft}")
            row.append("" if ct is None else f"{ct / 1000.0:.1f}")
            fh.write(",".join(row) + "\n")
            prev_e, prev_t = e, now

if __name__ == "__main__":
    main()

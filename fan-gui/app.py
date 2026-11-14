#!/usr/bin/env python3
"""Fan monitor / controller / profiler for an ITE IT8655E (out-of-tree it87 driver).

Safety model (see FAN_CONTROL_NOTES.md):
  * Manual overrides are raise-biased: UI floors per fan, max 250 (255 = driver gives up control).
  * Manual overrides expire (TTL) and fall back to automatic (enable=2).
  * Thermal guard: CPU / drive / GPU too hot -> all fans back to automatic.
  * Stall guard: a fan we drive manually reads < 250 RPM -> automatic.
  * SIGTERM / container stop -> all fans back to automatic.
  * Restore ALWAYS writes pwmN_enable=2 first, never a low duty, then repairs the saved BIOS curve.
  * Smart mode = slow, deadbanded target-temperature controller (see smart_tick).
  * Profiler only walks fans through duties that were already tested on this machine.
"""
import base64
import collections
import glob
import hmac
import json
import math
import os
import signal
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SYS = os.environ.get("SYS_ROOT", "/sys")
DATA = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8765"))
USER = os.environ.get("FAN_GUI_USER", "admin")
PASSWORD = os.environ.get("FAN_GUI_PASSWORD", "")
GPU_EXPORTER = os.environ.get("GPU_EXPORTER_URL", "http://127.0.0.1:7071/metrics")
GUARD_CPU_C = float(os.environ.get("GUARD_CPU_C", "65"))
GUARD_DRIVE_C = float(os.environ.get("GUARD_DRIVE_C", "48"))
GUARD_GPU_C = float(os.environ.get("GUARD_GPU_C", "85"))
# FAN_CONTROL=0 -> monitor-only: nothing is ever written to the fans (no privileged container needed), no startup cleanup,
# no smart mode, no profiler, no overrides. Metrics, graphs and the thermal view keep working.
CONTROL = os.environ.get("FAN_CONTROL", "1").strip().lower() in ("1", "true", "yes", "on")
# name prefix of the hwmon device that exposes the fans (ITE chips = "it8"). Control is only supported for the it87 driver.
FAN_HWMON_PREFIX = os.environ.get("FAN_HWMON_PREFIX", "it8")

# Fan map learned on this board (PRIME B450M-A II, IT8655E)
FANS = {
    1: {"label": "CPU fan", "detail": "4-pin PWM (CPU_FAN) - pwm1", "min": 140, "max": 250},
    2: {"label": "Case fan (HDD)", "detail": "3-pin voltage control - pwm2", "min": 100, "max": 250},
}
# Smart-mode / profiler bounds: lowest duty that was actually tested on this machine.
SMART = {
    1: {"sensor": "cpu", "label": "CPU temp", "lo": 120, "hi": 250, "start": 160, "tau": 20, "interval": 20,
        "dwell": 120, "t_default": 58.0, "t_range": (45.0, 62.0), "db_default": 3.0},
    2: {"sensor": "drive_max", "label": "hottest drive", "lo": 63, "hi": 250, "start": 100, "tau": 90, "interval": 30,
        "dwell": 180, "t_default": 40.0, "t_range": (32.0, 45.0), "db_default": 2.0},
}
PROFILE_DUTIES = {1: [120, 140, 160, 180, 200, 220, 250], 2: [63, 80, 100, 120, 150, 180, 220, 250]}
PROFILE_ABORT = {1: ("cpu", 60.0), 2: ("drive_max", 44.0)}
AUTO_ATTRS = ["auto_channels_temp", "auto_point1_temp", "auto_point1_temp_hyst",
              "auto_point2_temp", "auto_point3_temp", "auto_slope", "auto_start"]
PWM_AUTO, PWM_MANUAL = 2, 1

lock = threading.RLock()
manual = {}                      # fan -> {"pwm": int, "expires": float|None, "smart": bool}
history = collections.deque(maxlen=1800)   # ~1h at 2s
events = collections.deque(maxlen=200)
guard_state = {"tripped": False, "reason": ""}
status = {"last": {}}
gpu_top = {}                     # filled from the intel-gpu-exporter
smart_state = {n: {"ema": None, "duty": None, "last_act": 0.0, "last_change": 0.0, "saved": 0.0} for n in SMART}
profile_state = {"running": False, "fan": None, "step": 0, "total": 0, "msg": "", "results": [], "cancel": False}
_prev_energy = {}
os.makedirs(DATA, exist_ok=True)


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    events.appendleft(line)
    print(line, flush=True)
    try:
        with open(os.path.join(DATA, "events.log"), "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def rd(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def rdi(path):
    v = rd(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def wr(path, val):
    with open(path, "w") as f:
        f.write(str(val))


def hwmons():
    out = {}
    for d in sorted(glob.glob(os.path.join(SYS, "class/hwmon/hwmon*"))):
        out.setdefault(rd(d + "/name"), []).append(d)
    return out


def find_ite():
    for name, dirs in hwmons().items():
        if name and name.startswith(FAN_HWMON_PREFIX):
            return dirs[0]
    return None


# ---------------------------------------------------------------- config
cfg = {"smart": {}, "learned": {}}


def load_cfg():
    try:
        with open(os.path.join(DATA, "config.json")) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    for n, d in SMART.items():
        c = cfg["smart"].setdefault(str(n), {})
        c.setdefault("enabled", False)
        c.setdefault("target", d["t_default"])
        c.setdefault("deadband", d["db_default"])
        if not CONTROL:
            c["enabled"] = False


def save_cfg():
    try:
        with open(os.path.join(DATA, "config.json"), "w") as f:
            json.dump(cfg, f, indent=1)
    except OSError as e:
        log("could not save config: %s" % e)


def scfg(n):
    return cfg["smart"][str(n)]


# ---------------------------------------------------------------- snapshot
def load_snapshot():
    try:
        with open(os.path.join(DATA, "original.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def take_snapshot(ite):
    """Record the automatic (BIOS) settings once, only while fans are in auto mode."""
    if not CONTROL:
        return
    p = os.path.join(DATA, "original.json")
    if os.path.exists(p):
        return
    snap = {}
    for n in FANS:
        if rdi("%s/pwm%d_enable" % (ite, n)) != PWM_AUTO:
            return
        snap[str(n)] = {a: rd("%s/pwm%d_%s" % (ite, n, a)) for a in AUTO_ATTRS}
    snap["_taken"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(p, "w") as f:
        json.dump(snap, f, indent=1)
    log("Saved original automatic fan settings: %s" % json.dumps(snap))


def verify_auto(ite, n):
    snap = load_snapshot()
    if not snap:
        return
    for a in AUTO_ATTRS:
        want = snap[str(n)].get(a)
        have = rd("%s/pwm%d_%s" % (ite, n, a))
        if want is not None and have != want:
            try:
                wr("%s/pwm%d_%s" % (ite, n, a), want)
                log("fan%d: corrected %s %s -> %s" % (n, a, have, want))
            except OSError as e:
                log("fan%d: could not correct %s: %s" % (n, a, e))


# ---------------------------------------------------------------- control
def set_auto(n, why):
    ite = find_ite()
    if not ite or not CONTROL:
        return
    with lock:
        try:
            wr("%s/pwm%d_enable" % (ite, n), PWM_AUTO)   # enable FIRST, never write low pwm
        except OSError as e:
            log("fan%d: FAILED to restore auto: %s" % (n, e))
            return
        verify_auto(ite, n)
        manual.pop(n, None)
        log("fan%d -> AUTOMATIC (%s)" % (n, why))


def apply_duty(n, pwm, lo, hi, ttl_min=None, smart=False):
    if not CONTROL:
        raise ValueError("monitor-only mode (FAN_CONTROL=0): fan control is disabled")
    ite = find_ite()
    if not ite:
        raise ValueError("fan driver (it87) is not loaded")
    pwm = max(lo, min(hi, int(pwm)))
    if guard_state["tripped"]:
        raise ValueError("thermal guard tripped: %s" % guard_state["reason"])
    with lock:
        take_snapshot(ite)
        base = "%s/pwm%d" % (ite, n)
        if rdi(base + "_enable") != PWM_MANUAL:
            try:
                wr(base, pwm)        # pre-load target before taking over (pwm2 returns EBUSY in auto: fine)
            except OSError:
                pass
            wr(base + "_enable", PWM_MANUAL)
        wr(base, pwm)
        expires = None if smart else time.time() + max(5, min(480, int(ttl_min or 30))) * 60
        manual[n] = {"pwm": pwm, "expires": expires, "smart": smart}
    return pwm


def set_manual(n, pwm, ttl_min):
    if scfg(n)["enabled"]:
        scfg(n)["enabled"] = False
        save_cfg()
        log("fan%d: smart mode switched off by manual override" % n)
    f = FANS[n]
    out = apply_duty(n, pwm, f["min"], f["max"], ttl_min)
    log("fan%d -> MANUAL pwm=%d (%.0f%%) for %d min" % (n, out, out / 2.55, max(5, min(480, int(ttl_min)))))
    return out


def restore_all(why):
    if not CONTROL:
        return
    for n in FANS:
        ite = find_ite()
        if ite and rdi("%s/pwm%d_enable" % (ite, n)) != PWM_AUTO:
            set_auto(n, why)


# ---------------------------------------------------------------- smart controller
def smart_ema(n, value):
    st, d = smart_state[n], SMART[n]
    a = 2.0 / (d["tau"] / 2.0 + 1.0)       # samples arrive every 2 s
    st["ema"] = value if st["ema"] is None else st["ema"] + a * (value - st["ema"])


def smart_tick(s):
    now = time.time()
    for n, d in SMART.items():
        c = scfg(n)
        t = (s.get("temps") or {}).get(d["sensor"])
        if t is not None:
            smart_ema(n, t)                 # keep the smoothed value warm even when off
        if not CONTROL or not c["enabled"] or not s.get("driver"):
            continue
        if guard_state["tripped"] or (profile_state["running"] and profile_state["fan"] == n):
            continue
        st = smart_state[n]
        if t is None:
            scfg(n)["enabled"] = False
            save_cfg()
            set_auto(n, "smart: sensor lost")
            continue
        if n not in manual:                 # (re)take control
            start = cfg["learned"].get(str(n), d["start"])
            st["duty"] = max(d["lo"], min(d["hi"], int(start)))
            try:
                apply_duty(n, st["duty"], d["lo"], d["hi"], smart=True)
            except ValueError:
                continue
            st["last_change"] = st["last_act"] = now
            log("fan%d smart: took control at duty %d (target %.0f C, deadband %.0f)" % (n, st["duty"], c["target"], c["deadband"]))
            continue
        err = st["ema"] - c["target"]
        emergency = err > c["deadband"] + 5
        if now - st["last_act"] < (10 if emergency else d["interval"]):
            continue
        st["last_act"] = now
        duty = st["duty"]
        if emergency:
            duty += 30
        elif err > c["deadband"]:
            duty += min(15, 3 + 3 * (err - c["deadband"]))
        elif err < -c["deadband"] and now - st["last_change"] >= d["dwell"]:
            duty -= 2
        duty = int(max(d["lo"], min(d["hi"], round(duty))))
        if duty != st["duty"]:
            try:
                apply_duty(n, duty, d["lo"], d["hi"], smart=True)
            except ValueError:
                continue
            st["duty"], st["last_change"] = duty, now
            log("fan%d smart: duty -> %d (temp %.1f, target %.0f)" % (n, duty, st["ema"], c["target"]))
        elif now - st["last_change"] >= 600 and now - st["saved"] >= 300 and cfg["learned"].get(str(n)) != duty:
            cfg["learned"][str(n)] = duty
            st["saved"] = now
            save_cfg()
            log("fan%d smart: settled at duty %d - remembered as the starting point" % (n, duty))


# ---------------------------------------------------------------- profiler
class Abort(Exception):
    pass


def run_profile(n):
    ps = profile_state
    was_smart = scfg(n)["enabled"]
    duties = PROFILE_DUTIES[n]
    results = []
    try:
        sensor, limit = PROFILE_ABORT[n]
        ps.update(running=True, fan=n, step=0, total=len(duties), msg="starting", results=[], cancel=False)
        if was_smart:
            set_auto(n, "profile start")
        for i, duty in enumerate(duties):
            ps.update(step=i + 1, msg="duty %d: waiting for the RPM to settle" % duty)
            apply_duty(n, duty, min(duties), 250, ttl_min=10)
            t0, hist = time.time(), []
            while True:
                time.sleep(2)
                last = status.get("last") or {}
                if ps["cancel"]:
                    raise Abort("cancelled")
                if guard_state["tripped"]:
                    raise Abort("thermal guard tripped")
                if not last.get("driver"):
                    raise Abort("driver disappeared")
                temp = (last.get("temps") or {}).get(sensor)
                if temp is not None and temp >= limit:
                    raise Abort("%s reached %.0f C (limit %.0f)" % (sensor, temp, limit))
                rpm = ((last.get("fans") or {}).get(n) or {}).get("rpm")
                if rpm is None:
                    continue
                hist.append(rpm)
                el = time.time() - t0
                if rpm < 200:
                    raise Abort("fan reads %d RPM at duty %d" % (rpm, duty))
                if el >= 12 and len(hist) >= 4 and (max(hist[-4:]) - min(hist[-4:])) <= max(15, 0.03 * max(hist[-4:])):
                    break
                if el >= 45:
                    break
            results.append({"duty": duty, "rpm": int(sum(hist[-3:]) / len(hist[-3:])), "settled": time.time() - t0 < 45})
            ps["results"] = list(results)
        ps["msg"] = "done"
    except Abort as e:
        ps["msg"] = "aborted: %s" % e
        log("fan%d profile aborted: %s" % (n, e))
    except Exception as e:
        ps["msg"] = "error: %r" % e
        log("fan%d profile error: %r" % (n, e))
    finally:
        set_auto(n, "profile finished")
        if results:
            top = max(r["rpm"] for r in results)
            for r in results:
                r["rel_db"] = round(50 * math.log10(max(r["rpm"], 1) / float(top)), 1)   # fan affinity law estimate
            try:
                with open(os.path.join(DATA, "profile_%d.json" % n), "w") as f:
                    json.dump({"taken": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results}, f, indent=1)
            except OSError:
                pass
            ps["results"] = results
            log("fan%d profile: %s" % (n, ", ".join("%d->%d" % (r["duty"], r["rpm"]) for r in results)))
        ps["running"] = False               # smart mode (if still enabled) re-takes control on the next tick


def saved_profiles():
    out = {}
    for n in FANS:
        try:
            with open(os.path.join(DATA, "profile_%d.json" % n)) as f:
                out[str(n)] = json.load(f)
        except (OSError, ValueError):
            pass
    return out


# ---------------------------------------------------------------- intel_gpu_top via the existing exporter
def parse_prom(text):
    rows = []
    for line in text.splitlines():
        if not line.startswith("igpu_"):
            continue
        try:
            head, val = line.rsplit(" ", 1)
            name, _, lab = head.partition("{")
            labels = {}
            for kv in lab.rstrip("}").split('",'):
                if "=" in kv:
                    k, _, v = kv.partition("=")
                    labels[k] = v.strip('"')
            rows.append((name, labels, float(val)))
        except ValueError:
            pass
    return rows


def gpu_poll():
    while True:
        try:
            with urllib.request.urlopen(GPU_EXPORTER, timeout=4) as r:
                rows = parse_prom(r.read().decode())
            g = {"engines": {}, "clients": {}, "ok": True, "ts": time.time()}
            for name, lab, v in rows:
                if name == "igpu_engines_busy_percent":
                    g["engines"][lab.get("engine", "?")] = v
                elif name == "igpu_client_engine_busy_percent":
                    c = g["clients"].setdefault(lab.get("name", "?"), {"pids": set(), "busy": 0.0})
                    c["pids"].add(lab.get("pid"))
                    c["busy"] += v
                elif name == "igpu_power_gpu":
                    g["power_w"] = v
                elif name == "igpu_rc6":
                    g["rc6_pct"] = v
                elif name == "igpu_frequency_actual":
                    g["freq_act"] = v
                elif name == "igpu_frequency_requested":
                    g["freq_req"] = v
                elif name == "igpu_memory_used_bytes" and lab.get("region") == "vram":
                    g["vram_used"] = v
                elif name == "igpu_memory_total_bytes" and lab.get("region") == "vram":
                    g["vram_total"] = v
            g["clients"] = {k: {"procs": len(v["pids"]), "busy": round(v["busy"], 1)} for k, v in g["clients"].items()}
            gpu_top.clear()
            gpu_top.update(g)
        except Exception:
            gpu_top.clear()
            gpu_top["ok"] = False
        time.sleep(5)


# ---------------------------------------------------------------- sampling
def temp_c(path):
    v = rdi(path)
    return None if v is None else v / 1000.0


def watts(key, energy_uj, now):
    if energy_uj is None:
        return None
    prev = _prev_energy.get(key)
    _prev_energy[key] = (now, energy_uj)
    if not prev or now - prev[0] < 0.5 or energy_uj < prev[1]:
        return None
    return (energy_uj - prev[1]) / 1e6 / (now - prev[0])


def sample():
    hw = hwmons()
    ite = find_ite()
    now = time.time()
    s = {"ts": now, "driver": bool(ite), "fans": {}, "temps": {}, "drives": [], "power": {}}
    if ite:
        for n in (1, 2, 3):
            s["fans"][n] = {
                "rpm": rdi("%s/fan%d_input" % (ite, n)),
                "pwm": rdi("%s/pwm%d" % (ite, n)),
                "enable": rdi("%s/pwm%d_enable" % (ite, n)),
            }
    for cpu_driver in ("k10temp", "coretemp"):        # AMD (Tctl) / Intel (package) CPU temperature
        for d in hw.get(cpu_driver, []):
            s["temps"]["cpu"] = temp_c(d + "/temp1_input")
    for name in ("i915", "xe"):
        for d in hw.get(name, []):
            t = temp_c(d + "/temp1_input")
            if t:
                s["temps"]["gpu"] = t
            s["power"]["gpu_w"] = watts("gpu", rdi(d + "/energy1_input"), now)
    for d in hw.get("nvme", []):
        s["temps"]["nvme"] = temp_c(d + "/temp1_input")
    for d in hw.get("jc42", []):
        s["temps"]["dimm"] = temp_c(d + "/temp1_input")
    for d in hw.get("drivetemp", []):
        t = temp_c(d + "/temp1_input")
        if t and t > 0:
            model = rd(d + "/device/model") or os.path.basename(d)
            blk = glob.glob(d + "/device/block/*")
            dev = os.path.basename(blk[0]) if blk else os.path.basename(d)
            s["drives"].append({"name": model.strip(), "dev": dev, "temp": t})
    s["temps"]["drive_max"] = max([x["temp"] for x in s["drives"]], default=None)
    s["power"]["cpu_w"] = watts("cpu", rdi(os.path.join(SYS, "class/powercap/intel-rapl:0/energy_uj")), now)
    freqs = [rdi(p) for p in glob.glob(os.path.join(SYS, "devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"))]
    freqs = [x for x in freqs if x]
    s["cpu"] = {
        "mhz": int(sum(freqs) / len(freqs) / 1000) if freqs else None,
        "governor": rd(os.path.join(SYS, "devices/system/cpu/cpu0/cpufreq/scaling_governor")),
        "boost": rd(os.path.join(SYS, "devices/system/cpu/cpufreq/boost")),
        "load": (rd("/proc/loadavg") or "").split()[:3],
    }
    return s


def guard(s):
    t = s["temps"]
    reason = ""
    if (t.get("cpu") or 0) >= GUARD_CPU_C:
        reason = "CPU %.0f C >= %.0f" % (t["cpu"], GUARD_CPU_C)
    elif (t.get("drive_max") or 0) >= GUARD_DRIVE_C:
        reason = "drive %.0f C >= %.0f" % (t["drive_max"], GUARD_DRIVE_C)
    elif (t.get("gpu") or 0) >= GUARD_GPU_C:
        reason = "GPU %.0f C >= %.0f" % (t["gpu"], GUARD_GPU_C)
    if reason and not guard_state["tripped"]:
        guard_state.update(tripped=True, reason=reason)
        log("THERMAL GUARD tripped (%s): all fans -> automatic, overrides blocked" % reason)
        restore_all("thermal guard")
    elif not reason and guard_state["tripped"]:
        if (t.get("cpu") or 0) < GUARD_CPU_C - 8 and (t.get("drive_max") or 0) < GUARD_DRIVE_C - 5:
            guard_state.update(tripped=False, reason="")
            log("Thermal guard cleared")


stall = {1: 0, 2: 0}


def stall_check(s):
    for n in FANS:
        rpm = ((s.get("fans") or {}).get(n) or {}).get("rpm")
        if n in manual and rpm is not None and rpm < 250:
            stall[n] += 1
            if stall[n] >= 3:
                stall[n] = 0
                scfg(n)["enabled"] = False
                save_cfg()
                log("fan%d STALL: reads %d RPM while under manual control" % (n, rpm))
                set_auto(n, "stall guard")
        else:
            stall[n] = 0


def loop():
    last = {}
    while True:
        try:
            ite = find_ite()
            if ite:
                take_snapshot(ite)
                if not last.get("driver"):
                    log("fan hwmon detected at %s%s" % (ite, "" if CONTROL else " (monitor-only)"))
                    for n in FANS:       # cleanup after a crash: unknown manual mode -> auto
                        if CONTROL and n not in manual and rdi("%s/pwm%d_enable" % (ite, n)) != PWM_AUTO:
                            set_auto(n, "startup cleanup")
            s = sample()
            guard(s)
            now = time.time()
            with lock:
                for n, m in list(manual.items()):
                    if m["expires"] is not None and now >= m["expires"]:
                        set_auto(n, "override expired")
            status["last"] = s
            stall_check(s)
            smart_tick(s)
            s["manual"] = {n: {"pwm": m["pwm"], "left_s": None if m["expires"] is None else int(m["expires"] - now), "smart": m["smart"]}
                           for n, m in manual.items()}
            history.append({
                "t": int(s["ts"]), "cpu": s["temps"].get("cpu"), "drv": s["temps"].get("drive_max"),
                "gpu": s["temps"].get("gpu"),
                "f1": s["fans"].get(1, {}).get("rpm"), "f2": s["fans"].get(2, {}).get("rpm"),
                "cw": s["power"].get("cpu_w"), "gw": s["power"].get("gpu_w"),
            })
            last = s
            status["last"] = s
        except Exception as e:  # never die
            log("loop error: %r" % e)
        time.sleep(2)


# ---------------------------------------------------------------- prometheus
def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def metrics_text():
    out, seen = [], set()

    def m(name, value, labels=None, help_=None, typ="gauge"):
        if value is None:
            return
        if name not in seen:
            seen.add(name)
            if help_:
                out.append("# HELP %s %s" % (name, help_))
            out.append("# TYPE %s %s" % (name, typ))
        lab = ""
        if labels:
            lab = "{" + ",".join('%s="%s"' % (k, esc(v)) for k, v in labels.items()) + "}"
        out.append("%s%s %s" % (name, lab, float(value)))

    s = status.get("last") or {}
    now = time.time()
    m("fangui_up", 1, help_="fan-gui is running")
    m("fangui_driver_loaded", 1 if s.get("driver") else 0, help_="fan hwmon driver present (1) or missing (0)")
    m("fangui_control_enabled", 1 if CONTROL else 0, help_="1 = fan control enabled, 0 = monitor-only mode")
    m("fangui_guard_tripped", 1 if guard_state["tripped"] else 0, help_="Thermal guard active")
    for k, v in (("cpu", GUARD_CPU_C), ("drive", GUARD_DRIVE_C), ("gpu", GUARD_GPU_C)):
        m("fangui_guard_threshold_celsius", v, {"sensor": k}, "Thermal guard thresholds")
    for n, f in (s.get("fans") or {}).items():
        lab = {"fan": n, "name": FANS.get(int(n), {}).get("label", "unused")}
        m("fan_speed_rpm", f.get("rpm"), lab, "Fan speed in RPM")
        m("fan_pwm_duty", f.get("pwm"), lab, "PWM duty 0-255 (unreliable in automatic mode)")
        m("fan_mode", f.get("enable"), lab, "pwm enable: 0 full speed, 1 manual, 2 automatic (BIOS)")
        man = manual.get(int(n))
        m("fan_manual_active", 1 if man else 0, lab, "Manual or smart override active")
        left = man["expires"] - now if man and man["expires"] is not None else 0
        m("fan_manual_seconds_left", max(0, int(left)), lab, "Seconds until a manual override expires")
    for n, c in FANS.items():
        m("fan_manual_floor_duty", c["min"], {"fan": n, "name": c["label"]}, "Lowest duty the GUI will set manually")
    for n, d in SMART.items():
        lab = {"fan": n, "sensor": d["label"]}
        c, st = scfg(n), smart_state[n]
        m("fangui_smart_enabled", 1 if c["enabled"] else 0, lab, "Smart (target temperature) mode on")
        m("fangui_smart_target_celsius", c["target"], lab, "Smart mode target temperature")
        m("fangui_smart_temp_smoothed_celsius", st["ema"], lab, "Smoothed temperature the controller acts on")
        m("fangui_smart_duty", st["duty"] if n in manual and manual[n]["smart"] else None, lab, "Duty chosen by smart mode")
    m("fangui_profile_running", 1 if profile_state["running"] else 0, help_="Fan profiler is running")
    for n, p in saved_profiles().items():
        for r in p["results"]:
            m("fan_profile_rpm", r["rpm"], {"fan": n, "duty": r["duty"]}, "RPM measured at each duty by the profiler")
    t = s.get("temps") or {}
    for k in ("cpu", "gpu", "nvme", "drive_max", "dimm"):
        m("hw_temperature_celsius", t.get(k), {"sensor": k}, "Temperatures")
    for d in s.get("drives") or []:
        m("hw_drive_temperature_celsius", d["temp"], {"drive": d["name"], "dev": d.get("dev", "")}, "Drive temperature via drivetemp")
    p = s.get("power") or {}
    m("cpu_package_watts", p.get("cpu_w"), None, "CPU package power (RAPL), live")
    m("gpu_watts", p.get("gpu_w"), None, "GPU power (i915 hwmon energy), live")
    c = s.get("cpu") or {}
    m("cpu_frequency_mhz_avg", c.get("mhz"), None, "Average CPU core frequency")
    m("cpu_boost_enabled", None if c.get("boost") is None else int(c["boost"]), None, "CPU boost on (1) / off (0)")
    if c.get("governor"):
        m("cpu_governor_info", 1, {"governor": c["governor"]}, "CPU scaling governor")
    for i, k in enumerate(("1m", "5m", "15m")):
        try:
            m("host_load_average", float((c.get("load") or [])[i]), {"window": k}, "Host load average")
        except (IndexError, ValueError):
            pass
    e = rdi(os.path.join(SYS, "class/powercap/intel-rapl:0/energy_uj"))
    m("cpu_package_energy_joules_total", None if e is None else e / 1e6, None, "CPU package energy (RAPL); rate() = watts", "counter")
    for name in ("i915", "xe"):
        for d in hwmons().get(name, []):
            ge = rdi(d + "/energy1_input")
            m("gpu_energy_joules_total", None if ge is None else ge / 1e6, None, "GPU energy; rate() = watts", "counter")
            pl = rdi(d + "/power1_max")
            m("gpu_power_limit_watts", None if pl is None else pl / 1e6, None, "GPU power limit")
    gt = os.path.join(SYS, "class/drm/card0/gt/gt0")
    m("gpu_frequency_mhz", rdi(gt + "/rps_act_freq_mhz"), None, "GPU actual frequency")
    m("gpu_frequency_min_mhz", rdi(gt + "/rps_min_freq_mhz"), None, "GPU minimum frequency")
    rc6 = rdi(gt + "/rc6_residency_ms")
    m("gpu_rc6_residency_seconds_total", None if rc6 is None else rc6 / 1000.0, None, "Time GPU spent in RC6 deep idle", "counter")
    if gpu_top.get("ok"):
        for eng, v in gpu_top["engines"].items():
            m("gpu_engine_busy_percent", v, {"engine": eng}, "intel_gpu_top engine utilisation")
        for nm, c2 in gpu_top["clients"].items():
            m("gpu_client_busy_percent", c2["busy"], {"name": nm}, "intel_gpu_top busy % summed over a program's engines")
        m("gpu_top_power_watts", gpu_top.get("power_w"), None, "intel_gpu_top GPU power")
        m("gpu_rc6_percent", gpu_top.get("rc6_pct"), None, "intel_gpu_top RC6 idle residency %")
        m("gpu_vram_used_bytes", gpu_top.get("vram_used"), None, "GPU VRAM in use")
    mi = {}
    for line in (rd("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        mi[k] = v.split()[0] if v.split() else None
    for k, nm in (("MemTotal", "total"), ("MemAvailable", "available")):
        m("host_memory_bytes", int(mi[k]) * 1024 if mi.get(k) else None, {"kind": nm}, "Host memory")
    for res in ("cpu", "io", "memory"):
        for line in (rd("/proc/pressure/" + res) or "").splitlines():
            parts = line.split()
            kv = dict(x.split("=") for x in parts[1:])
            m("host_pressure_stall_seconds_total", float(kv.get("total", 0)) / 1e6, {"resource": res, "kind": parts[0]}, "PSI stall time", "counter")
    up = (rd("/proc/uptime") or "").split()
    m("host_uptime_seconds", float(up[0]) if up else None, None, "Host uptime")
    arc = {}
    for line in (rd("/proc/spl/kstat/zfs/arcstats") or "").splitlines()[2:]:
        q = line.split()
        if len(q) == 3:
            arc[q[0]] = int(q[2])
    for k in ("size", "c_max", "l2_size"):
        m("zfs_arc_bytes", arc.get(k), {"kind": k}, "ZFS ARC sizes")
    for k in ("hits", "misses"):
        m("zfs_arc_%s_total" % k, arc.get(k), None, "ZFS ARC %s" % k, "counter")
    for d in sorted(glob.glob("/proc/spl/kstat/zfs/*/state")):
        st = rd(d)
        m("zfs_pool_online", 1 if st == "ONLINE" else 0, {"pool": d.split("/")[-2], "state": st}, "ZFS pool state from the kernel")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- http
def authorized(h):
    if not PASSWORD:
        return False
    a = h.headers.get("Authorization", "")
    if not a.startswith("Basic "):
        return False
    try:
        u, _, p = base64.b64decode(a[6:]).decode().partition(":")
    except Exception:
        return False
    return hmac.compare_digest(u, USER) and hmac.compare_digest(p, PASSWORD)


def full_status():
    s = dict(status["last"])
    s["fan_cfg"] = {str(k): v for k, v in FANS.items()}
    s["guard"] = guard_state
    s["control"] = CONTROL
    s["limits"] = {"cpu": GUARD_CPU_C, "drive": GUARD_DRIVE_C, "gpu": GUARD_GPU_C}
    s["events"] = list(events)[:30]
    s["gpu_top"] = gpu_top
    s["smart"] = {}
    for n, d in SMART.items():
        c, st = scfg(n), smart_state[n]
        s["smart"][str(n)] = {"enabled": c["enabled"], "target": c["target"], "deadband": c["deadband"],
                              "sensor": d["label"], "ema": st["ema"], "duty": st["duty"] if n in manual and manual[n]["smart"] else None,
                              "range": d["t_range"], "bounds": [d["lo"], d["hi"]], "learned": cfg["learned"].get(str(n))}
    s["profile"] = {"state": {k: v for k, v in profile_state.items()}, "saved": saved_profiles()}
    return s


class H(BaseHTTPRequestHandler):
    server_version = "fan-gui"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="fan-gui"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not authorized(self):
            return self.deny()
        if self.path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/status":
            return self.send(200, json.dumps(full_status(), default=list))
        if self.path == "/metrics":
            return self.send(200, metrics_text(), "text/plain; version=0.0.4; charset=utf-8")
        if self.path == "/api/history":
            return self.send(200, json.dumps(list(history)))
        self.send(404, "{}")

    def do_POST(self):
        if not authorized(self):
            return self.deny()
        if self.headers.get("X-Requested-With") != "fan-gui":
            return self.send(403, json.dumps({"error": "missing header"}))
        if not CONTROL:
            return self.send(400, json.dumps({"error": "monitor-only mode (FAN_CONTROL=0): fan control is disabled"}))
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/auto_all":
                for k in SMART:
                    scfg(k)["enabled"] = False
                save_cfg()
                profile_state["cancel"] = True
                restore_all("user: all automatic")
                return self.send(200, json.dumps({"ok": True}))
            if self.path == "/api/fan":
                fan = int(body["fan"])
                if fan not in FANS:
                    raise ValueError("unknown fan")
                if body.get("mode") == "auto":
                    scfg(fan)["enabled"] = False
                    save_cfg()
                    set_auto(fan, "user")
                    return self.send(200, json.dumps({"ok": True}))
                if profile_state["running"] and profile_state["fan"] == fan:
                    raise ValueError("the profiler is running on this fan")
                pwm = set_manual(fan, body["pwm"], body.get("ttl_min", 30))
                return self.send(200, json.dumps({"ok": True, "pwm": pwm}))
            if self.path == "/api/smart":
                fan = int(body["fan"])
                if fan not in SMART:
                    raise ValueError("unknown fan")
                c, d = scfg(fan), SMART[fan]
                if "target" in body:
                    lo, hi = d["t_range"]
                    c["target"] = max(lo, min(hi, float(body["target"])))
                if "deadband" in body:
                    c["deadband"] = max(1.0, min(5.0, float(body["deadband"])))
                if "enabled" in body:
                    en = bool(body["enabled"])
                    if en and (profile_state["running"] and profile_state["fan"] == fan):
                        raise ValueError("the profiler is running on this fan")
                    if en and not (status.get("last") or {}).get("driver"):
                        raise ValueError("fan driver (it87) is not loaded")
                    c["enabled"] = en
                    if not en:
                        set_auto(fan, "smart mode off")
                    else:
                        log("fan%d smart mode ON (target %.0f C)" % (fan, c["target"]))
                save_cfg()
                return self.send(200, json.dumps({"ok": True}))
            if self.path == "/api/profile":
                if body.get("action") == "cancel":
                    profile_state["cancel"] = True
                    return self.send(200, json.dumps({"ok": True}))
                fan = int(body["fan"])
                if fan not in FANS:
                    raise ValueError("unknown fan")
                if profile_state["running"]:
                    raise ValueError("a profile is already running")
                if guard_state["tripped"]:
                    raise ValueError("thermal guard tripped")
                if not (status.get("last") or {}).get("driver"):
                    raise ValueError("fan driver (it87) is not loaded")
                profile_state.update(running=True, fan=fan, cancel=False, msg="starting", results=[], step=0, total=len(PROFILE_DUTIES[fan]))
                threading.Thread(target=run_profile, args=(fan,), daemon=True).start()
                return self.send(200, json.dumps({"ok": True}))
            self.send(404, "{}")
        except Exception as e:
            self.send(400, json.dumps({"error": str(e)}))


PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html"), encoding="utf-8").read()


def shutdown(*_):
    log("shutting down%s" % (": restoring all fans to automatic" if CONTROL else " (monitor-only, fans untouched)"))
    restore_all("shutdown")
    os._exit(0)


if __name__ == "__main__":
    if not PASSWORD:
        raise SystemExit("FAN_GUI_PASSWORD must be set")
    load_cfg()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log("fan-gui starting on :%d, %s (guards: CPU %.0f, drive %.0f, GPU %.0f)" % (
        PORT, "fan control ENABLED" if CONTROL else "MONITOR-ONLY (FAN_CONTROL=0)", GUARD_CPU_C, GUARD_DRIVE_C, GUARD_GPU_C))
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=gpu_poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()

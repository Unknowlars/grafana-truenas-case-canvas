#!/usr/bin/env python3
"""Synthetic metrics for the dashboard demo (no hardware needed).

Serves the same metric names and labels as fan-gui and node-exporter, driven by a scripted load cycle:
idle -> load (CPU and GPU heat up, fans ramp, drives warm up with a delay, tray LEDs blink) -> cool-down.
Everything here is made up. It exists to preview the dashboards and to record the README animation.

    GET /fan-gui/metrics   (job fan-gui)
    GET /node/metrics      (job node)

DEMO_CYCLE=40 seconds per cycle (default), PORT=9000.
"""
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CYCLE = float(os.environ.get("DEMO_CYCLE", "40"))
PORT = int(os.environ.get("PORT", "9000"))
T0 = time.time()
lock = threading.Lock()


def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ramp(p, a, b):
    return smooth((p - a) / (b - a))


def load(p):
    """0..1 load shape inside one cycle: idle, ramp up, hold, ramp down, idle."""
    k = CYCLE / 40.0
    p = p / k
    return ramp(p, 6, 14) - ramp(p, 22, 34)


def phase(t):
    return t % CYCLE          # wall-clock based: every viewer (and every restart) sees the same point of the cycle


DRIVES = [("sda", "SAMSUNG MZ7LH960", 36.0, 10.0), ("sdb", "ST12000VN0007-2G", 38.0, 11.0),
          ("sdc", "ST12000VN0007-2G", 37.0, 7.5), ("sdd", "SAMSUNG MZ7LH960", 35.0, 4.0)]
DISKS = ["sda", "sdb", "sdc", "sdd", "sde", "nvme0n1"]


class State:
    def __init__(self):
        self.last = time.time()
        self.io_time = {d: 0.0 for d in DISKS}
        self.read_b = {d: 0.0 for d in DISKS}
        self.write_b = {d: 0.0 for d in DISKS}
        self.cpu_idle = [0.0] * 4
        self.cpu_iowait = [0.0] * 4
        self.cpu_user = [0.0] * 4
        self.rx = 0.0
        self.tx = 0.0
        self.cpu_j = 0.0
        self.gpu_j = 0.0
        self.arc_hits = 5.0e8
        self.arc_miss = 2.0e6

    def step(self, now):
        dt = max(0.0, min(5.0, now - self.last))
        self.last = now
        p = phase(now)
        L = load(p)
        Ld = load((p - 6.0 * CYCLE / 40.0) % CYCLE)
        for i, d in enumerate(DISKS):
            wave = max(0.0, math.sin(2 * math.pi * now / (2.5 + 0.7 * i) + i * 1.3)) ** 3
            busy = 0.03 + 0.85 * wave * (0.3 + 0.7 * Ld) if i < 4 else 0.01 + 0.05 * wave
            self.io_time[d] += busy * dt
            self.read_b[d] += busy * 60e6 * dt
            self.write_b[d] += busy * 90e6 * dt
        for c in range(4):
            busy = min(0.97, 0.12 + 0.72 * L + 0.02 * c)
            self.cpu_user[c] += busy * dt
            self.cpu_iowait[c] += (0.01 + 0.05 * Ld) * dt
            self.cpu_idle[c] += max(0.0, 1 - busy - 0.01 - 0.05 * Ld) * dt
        self.rx += (2e6 + 45e6 * L) * dt
        self.tx += (1e6 + 18e6 * L) * dt
        self.cpu_j += (12 + 58 * L) * dt
        self.gpu_j += (14 + 22 * L) * dt
        self.arc_hits += (4e5 + 2e6 * L) * dt
        self.arc_miss += (900 + 5000 * L) * dt
        return p, L, Ld


ST = State()


def fmt(lines, name, value, labels=None, help_=None, typ="gauge"):
    if help_ and not any(l.startswith("# TYPE %s " % name) for l in lines):
        lines.append("# HELP %s %s" % (name, help_))
        lines.append("# TYPE %s %s" % (name, typ))
    lab = ""
    if labels:
        lab = "{" + ",".join('%s="%s"' % (k, v) for k, v in labels.items()) + "}"
    lines.append("%s%s %.6f" % (name, lab, float(value)))


def fan_gui_metrics():
    with lock:
        now = time.time()
        p, L, Ld = ST.step(now)
    out = []
    m = lambda *a, **k: fmt(out, *a, **k)
    drv = [(dev, model, base + span * Ld) for dev, model, base, span in DRIVES]
    drive_max = max(t for _, _, t in drv)
    cpu_t, gpu_t = 41 + 23 * L, 58 + 17 * L
    rpm1, rpm2 = 1440 + 1160 * L, 910 + 950 * Ld
    m("fangui_up", 1, help_="demo exporter")
    m("fangui_driver_loaded", 1)
    m("fangui_control_enabled", 0)
    m("fangui_guard_tripped", 0)
    for k, v in (("cpu", 65), ("drive", 48), ("gpu", 85)):
        m("fangui_guard_threshold_celsius", v, {"sensor": k})
    for n, name, rpm, mode in ((1, "CPU fan", rpm1, 2), (2, "Case fan (HDD)", rpm2, 1), (3, "unused", 0, 2)):
        lab = {"fan": n, "name": name}
        m("fan_speed_rpm", rpm, lab, "Fan speed in RPM")
        m("fan_pwm_duty", max(0, (rpm - 300) / 6.0), lab)
        m("fan_mode", mode, lab, "pwm enable: 0 full speed, 1 manual, 2 automatic (BIOS)")
        m("fan_manual_active", 1 if n == 2 else 0, lab)
        m("fan_manual_seconds_left", 0, lab)
    for n, label in ((1, "CPU temp"), (2, "hottest drive")):
        lab = {"fan": n, "sensor": label}
        m("fangui_smart_enabled", 1 if n == 2 else 0, lab)
        m("fangui_smart_target_celsius", 58 if n == 1 else 40, lab)
        m("fangui_smart_temp_smoothed_celsius", cpu_t if n == 1 else drive_max, lab)
        if n == 2:
            m("fangui_smart_duty", max(0, (rpm2 - 300) / 6.0), lab)
    m("hw_temperature_celsius", cpu_t, {"sensor": "cpu"}, "Temperatures")
    m("hw_temperature_celsius", gpu_t, {"sensor": "gpu"})
    m("hw_temperature_celsius", 40 + 19 * Ld, {"sensor": "nvme"})
    m("hw_temperature_celsius", 39 + 13 * Ld, {"sensor": "dimm"})
    m("hw_temperature_celsius", drive_max, {"sensor": "drive_max"})
    for dev, model, t in drv:
        m("hw_drive_temperature_celsius", t, {"drive": model, "dev": dev}, "Drive temperature via drivetemp")
    m("cpu_package_watts", 12 + 58 * L)
    m("gpu_watts", 14 + 22 * L)
    m("cpu_frequency_mhz_avg", 1550 + 900 * L)
    m("cpu_boost_enabled", 0)
    m("cpu_governor_info", 1, {"governor": "schedutil"})
    for i, w in enumerate(("1m", "5m", "15m")):
        m("host_load_average", 1.5 + 6 * L * (1 - 0.2 * i), {"window": w})
    m("cpu_package_energy_joules_total", ST.cpu_j, typ="counter", help_="CPU package energy")
    m("gpu_energy_joules_total", ST.gpu_j, typ="counter", help_="GPU energy")
    m("gpu_power_limit_watts", 43)
    m("gpu_frequency_mhz", 300 + 1700 * L)
    m("gpu_rc6_percent", 93 - 70 * L)
    for eng, k in (("Video", 0.6), ("VideoEnhance", 0.3), ("Render/3D", 0.1), ("Compute", 0.0), ("Blitter", 0.0)):
        m("gpu_engine_busy_percent", min(100, 1 + 80 * L * k), {"engine": eng}, "intel_gpu_top engine utilisation")
    m("gpu_top_power_watts", 14 + 22 * L)
    m("gpu_vram_used_bytes", (140 + 900 * L) * 1048576)
    for kind, v in (("total", 40e9), ("available", 20e9 - 6e9 * L)):
        m("host_memory_bytes", v, {"kind": kind})
    m("host_uptime_seconds", 3 * 86400 + (now - T0))
    m("zfs_arc_bytes", 13.5e9 + 1e9 * L, {"kind": "size"})
    m("zfs_arc_bytes", 38e9, {"kind": "c_max"})
    m("zfs_arc_hits_total", ST.arc_hits, typ="counter")
    m("zfs_arc_misses_total", ST.arc_miss, typ="counter")
    for pool in ("big_data", "new_ssd", "boot-pool"):
        m("zfs_pool_online", 1, {"pool": pool, "state": "ONLINE"})
    return "\n".join(out) + "\n"


def node_metrics():
    with lock:
        ST.step(time.time())
    out = []
    m = lambda *a, **k: fmt(out, *a, **k)
    for d in DISKS:
        m("node_disk_io_time_seconds_total", ST.io_time[d], {"device": d}, typ="counter", help_="Total seconds spent doing I/Os")
        m("node_disk_read_bytes_total", ST.read_b[d], {"device": d}, typ="counter")
        m("node_disk_written_bytes_total", ST.write_b[d], {"device": d}, typ="counter")
    for c in range(4):
        m("node_cpu_seconds_total", ST.cpu_idle[c], {"cpu": c, "mode": "idle"}, typ="counter")
        m("node_cpu_seconds_total", ST.cpu_iowait[c], {"cpu": c, "mode": "iowait"}, typ="counter")
        m("node_cpu_seconds_total", ST.cpu_user[c], {"cpu": c, "mode": "user"}, typ="counter")
    m("node_network_receive_bytes_total", ST.rx, {"device": "eth0"}, typ="counter")
    m("node_network_transmit_bytes_total", ST.tx, {"device": "eth0"}, typ="counter")
    return "\n".join(out) + "\n"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/fan-gui/metrics":
            body = fan_gui_metrics()
        elif self.path == "/node/metrics":
            body = node_metrics()
        elif self.path == "/":
            body = "demo exporter: /fan-gui/metrics and /node/metrics (synthetic data)\n"
        else:
            self.send_response(404)
            self.end_headers()
            return
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


if __name__ == "__main__":
    print("demo exporter on :%d, cycle %.0f s (synthetic data)" % (PORT, CYCLE), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()

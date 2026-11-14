#!/usr/bin/env python3
"""Generate the Grafana dashboards for the TrueNAS Jonsbo N4 box.

    python3 build_dashboard.py

writes, next to this file (classic-schema JSON, Grafana 10+; Grafana 13 migrates it on open):
    fan-control-isometric.json          isometric case, layered artwork, dark   (uid truenas-case-fans-iso)
    fan-control-dashboard.json          flat blueprint, dark                    (uid truenas-case-fans)
    fan-control-isometric-light.json    isometric composite, light              (uid truenas-case-fans-iso-light)
    fan-control-dashboard-light.json    flat blueprint, light                   (uid truenas-case-fans-light)

Each is built around one Canvas panel. Artwork is in assets/ (see assets/README.md); every SVG is embedded as a data URI, so
nothing is hosted. Canvas cannot rotate an element from data, so a fan is an animated SVG chosen by RPM through value-mapping
icons (one SVG per RPM step; spin period scaled so 1800 RPM = 1 turn/s; motion-blur variant above 1800 RPM). Heat glows,
airflow and tray LEDs are overlays chosen the same way from temperatures / fan RPM / drive presence.
Component anchors and badge slots come from assets/manifest.json (projected isometric geometry).

Settings live in the CONFIG block below or in config.json (see config.example.json). Artwork for another case: set "assets_dir" in
config.json, follow docs/ARTWORK_PROMPT_TEMPLATE.md and check it with tools/audit_artwork.py. Components, fans, layers and overlays
are all optional: whatever the artwork folder lacks is skipped, and a view whose backdrop is missing is skipped with a message.
"""
import json
import os
import re
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")   # optional, see config.example.json
_CFG = {}
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, encoding="utf-8") as _cf:
        _CFG = json.load(_cf)
ASSETS = os.path.join(HERE, _CFG.get("assets_dir", "assets"))

# ----------------------------------------------------------------------------- CONFIG
DRIVES = [  # (dev label in hw_drive_temperature_celsius, caption, kind)  - in tray order 1..4 (left column top-down, then right column)
    ("sda", "Samsung SSD 960 GB", "ssd"),
    ("sdb", "Seagate IronWolf 12 TB", "hdd"),
    ("sdc", "Seagate IronWolf 12 TB", "hdd"),
    ("sdd", "Samsung SSD 960 GB", "ssd"),
]
DEFAULT_JOB = "fan-gui"
REFRESH = "10s"                     # dashboard auto-refresh (the demo copies use 2s)
NODE_JOB = "node"                   # Prometheus job of node-exporter (disk activity LEDs and the node panels)
DEFAULT_LABELS = {      # texts drawn on the pictures; override any of them in config.json under "labels"
    "title": "JONSBO N4 · TrueNAS", "title_flat": "JONSBO N4  ·  TrueNAS", "front_text": "JONSBO N4",
    "board_caption": "MOTHERBOARD COMPARTMENT  ·  ASUS PRIME B450M-A II",
    "cpu": "RYZEN 7 2700", "cpu_long": "RYZEN 7 2700  ·  8C / 16T", "cooler": "LOW-PROFILE CPU COOLER",
    "gpu": "ARC A310", "gpu_long": "ARC A310 LP  ·  4 GB", "nvme_long": "NVMe  ·  WD SN580 1 TB", "memory_long": "DDR4  ·  4 × 10 GB",
    "fan1": "CPU FAN", "fan2": "CASE FAN", "fan2_caption": "CASE FAN  →  DRIVES",
    "psu": "SFX PSU", "psu_long": "SFX POWER SUPPLY (no telemetry)", "bays_caption": "DRIVE BAYS  ·  6 × 3.5\"  (4 populated)",
}
DEFAULT_JOB = _CFG.get("job", DEFAULT_JOB)
NODE_JOB = _CFG.get("node_job", NODE_JOB)
if _CFG.get("drives"):
    DRIVES = [tuple(d) for d in _CFG["drives"]]
POOLS = list(_CFG.get("pools", ["big_data", "new_ssd", "boot-pool"]))[:3]      # ZFS pools shown (zfs_pool_online{pool=...})
LABELS = dict(DEFAULT_LABELS)
LABELS.update(_CFG.get("labels", {}))
CW, CH = 1100, 742                  # design size of every drawing (px); positions are stored as percentages
RPM_STEP, RPM_MAX = 150, 3000
BLUR_FROM_RPM = 1800                # at and above this speed the motion-blur fan is used
SPIN_RPM_PER_TURN_SECOND = 1800.0   # display speed: 1800 RPM -> 1 turn / second
FAN_ACCENT = {1: "#4da3ff", 2: "#2dd4bf"}
FAN_COLOUR_NAME = {1: "blue", 2: "teal"}
SHOW_OK_GLOW = False                # green "ok" pulse on components; off = only warm / hot glows
AIRFLOW_ON_RPM = 300

DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}

THEMES = {
    "dark": dict(TXT="#e6e9ef", MUT="#8791a3", CARD="rgba(15,19,25,0.88)", EDGE="#3a4452", LEAD="#8791a3", LEADA=0.55,
                 G="#34d399", Y="#fbbf24", O="#fb923c", R="#f87171", B="#60a5fa", WOOD="#f3dcc8", FAINT="#4b5563"),
    "light": dict(TXT="#18212a", MUT="#5b6577", CARD="rgba(255,255,255,0.90)", EDGE="#aeb8c4", LEAD="#44505f", LEADA=0.65,
                  G="#12805c", Y="#b45309", O="#c2410c", R="#c0262d", B="#1d4ed8", WOOD="#f3dcc8", FAINT="#9aa5b4"),
}
T = dict(THEMES["dark"])            # active palette (set_theme)


def set_theme(name):
    T.clear()
    T.update(THEMES[name])


# ----------------------------------------------------------------------------- SVG helpers
def read_asset(name):
    with open(os.path.join(ASSETS, name), encoding="utf-8") as fh:
        return fh.read()


def asset_exists(name):
    return os.path.exists(os.path.join(ASSETS, name))


def all_exist(names):
    return all(asset_exists(n) for n in names)


MANIFEST = {}
if asset_exists("manifest.json"):
    with open(os.path.join(ASSETS, "manifest.json"), encoding="utf-8") as _fh:
        MANIFEST = json.load(_fh)["components"]


def have(comp):
    return comp in MANIFEST


def slot(comp, default):
    b = MANIFEST.get(comp, {}).get("badge")
    return (b["x"], b["y"]) if b else default


def data_uri(svg):
    """SVG -> data URI. ':' and '/' stay readable: Grafana treats a string as an absolute URL only if it contains ':/'
    (the literal http:// in xmlns provides it); otherwise it would be rewritten as a relative path."""
    svg = re.sub(r">\s+<", "><", svg.strip())
    assert "http://www.w3.org/2000/svg" in svg
    # Full-case drawings (viewBox = the design size) must stretch with the panel like the percent-positioned overlays do. Without
    # preserveAspectRatio="none" an SVG keeps its proportions inside the box and letterboxes, so labels drift off their parts.
    if "viewBox='0 0 %d %d'" % (CW, CH) in svg[:600] and "preserveAspectRatio" not in svg[:600]:
        svg = svg.replace("<svg ", "<svg preserveAspectRatio='none' ", 1)
    return "data:image/svg+xml;utf8," + quote(svg, safe=":/=' ,.;()-_@")


def spin_duration(rpm):
    return max(0.25, SPIN_RPM_PER_TURN_SECOND / rpm)


def retime(svg, rpm):
    assert "dur='1.2s'" in svg
    return svg.replace("dur='1.2s'", "dur='%.3fs'" % spin_duration(rpm), 1)


def buckets():
    """[(from, to, centre_rpm)] - the first bucket (0-100 RPM) is 'stopped' (centre 0)."""
    out = [(0.0, 99.999, 0)]
    lo = 100.0
    while lo < RPM_MAX:
        out.append((lo, lo + RPM_STEP - 0.001, (lo + lo + RPM_STEP) / 2.0))
        lo += RPM_STEP
    return out


def range_mapping(lo, hi, svg, index):
    return {"type": "range", "options": {"from": lo, "to": hi, "result": {"index": index, "icon": data_uri(svg)}}}


def fan_mappings(prefix, fan):
    """prefix '' -> flat view files fan_<colour>_*, 'iso_' -> iso_fan<N>_* files."""
    if prefix == "iso_":
        base = "iso_fan%d_" % fan
    else:
        base = "fan_%s_" % FAN_COLOUR_NAME[fan]
    if not all_exist([base + "spin.svg", base + "stopped.svg", base + "blur.svg"]):
        return None
    spin, still, blur = read_asset(base + "spin.svg"), read_asset(base + "stopped.svg"), read_asset(base + "blur.svg")
    out = []
    for i, (lo, hi, c) in enumerate(buckets()):
        svg = still if c == 0 else retime(blur if c >= BLUR_FROM_RPM else spin, c)
        out.append(range_mapping(lo, hi, svg, i))
    return out


def threshold_mappings(files, warm_from, hot_from):
    """Heat glow chosen by temperature. files = (ok, warm, hot) SVG file names."""
    out, i = [], 0
    if SHOW_OK_GLOW:
        out.append(range_mapping(-50, warm_from - 0.001, read_asset(files[0]), i)); i += 1
    out.append(range_mapping(warm_from, hot_from - 0.001, read_asset(files[1]), i)); i += 1
    out.append(range_mapping(hot_from, 1000, read_asset(files[2]), i))
    return out


def activity_mappings(on_file, off_file):
    """Tray LED from disk busy fraction (0-1): lit above 1 % busy over the rate window, otherwise dark."""
    return [range_mapping(-1, 0.0099, read_asset(off_file), 0), range_mapping(0.01, 1000, read_asset(on_file), 1)]


def airflow_mappings(on_file, off_file):
    return [range_mapping(0, AIRFLOW_ON_RPM - 0.001, read_asset(off_file), 0), range_mapping(AIRFLOW_ON_RPM, 100000, read_asset(on_file), 1)]


GLOW_COMP = {"cpu": "cpu_cooler", "gpu": "gpu", "nvme": "nvme", "dimm": "dimm_bank"}


def glow_files(comp):
    return ["glow_%s_%s.svg" % (comp, s) for s in ("ok", "warm", "hot")]


def glow_ok(comp):
    return all_exist(glow_files(comp))


def tray_glow_ok(i):
    return all_exist(glow_files("tray_%d" % i))


def led_ok(i):
    return all_exist(["iso_led_tray_%d_on.svg" % i, "iso_led_tray_%d_off.svg" % i])


def air_ok(kind):
    return all_exist(["iso_airflow_%s_on.svg" % kind, "iso_airflow_%s_off.svg" % kind])


def iso_fan_ok(f):
    return all_exist(["iso_fan%d_%s.svg" % (f, k) for k in ("spin", "stopped", "blur")])


def flat_fan_ok(f):
    return all_exist(["fan_%s_%s.svg" % (FAN_COLOUR_NAME[f], k) for k in ("spin", "stopped", "blur")])


ISO_LAYERS = ["iso_00_base.svg", "iso_10_components.svg", "iso_20_trays_empty.svg", "iso_25_drives.svg", "iso_30_front_panel.svg",
              "iso_40_mesh_foreground.svg"]


def iso_layered(theme):
    return theme == "dark" and all_exist(ISO_LAYERS)


def iso_background(theme):
    if iso_layered(theme):
        return read_asset("iso_00_base.svg")
    name = "case_upper_side_light.svg" if theme == "light" else "case_upper_side.svg"
    return read_asset(name) if asset_exists(name) else None


def flat_background(theme):
    name = "case_background_light.svg" if theme == "light" else "case_background.svg"
    return read_asset(name) if asset_exists(name) else None


def leaders_svg(items):
    """Thin leader lines from each badge to its component anchor (an overlay generated from the manifest)."""
    parts = []
    for comp, accent in items:
        c = MANIFEST[comp]
        b = c["badge"]
        tx, ty = c["badge"]["arrow_tip"]
        cx, cy = b["x"] + b["w"] / 2.0, b["y"] + b["h"] / 2.0
        # clip the centre->tip segment to the badge rectangle edge
        dx, dy = tx - cx, ty - cy
        k = min((b["w"] / 2.0) / abs(dx) if dx else 1e9, (b["h"] / 2.0) / abs(dy) if dy else 1e9)
        sx, sy = cx + dx * k, cy + dy * k
        parts.append("<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' stroke='%s' stroke-opacity='%.2f' stroke-width='1.3'/>"
                     "<circle cx='%.1f' cy='%.1f' r='3.4' fill='%s'/><circle cx='%.1f' cy='%.1f' r='1.4' fill='#0f1319'/>"
                     % (sx, sy, tx, ty, T["LEAD"], T["LEADA"], tx, ty, accent, tx, ty))
    return "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 %d %d'>%s</svg>" % (CW, CH, "".join(parts))


# ----------------------------------------------------------------------------- queries
queries = []


def q(name, expr):
    if name not in [n for n, _ in queries]:
        queries.append((name, expr))


J = 'job="$job"'
for f in (1, 2):
    q("fan%d_rpm" % f, 'fan_speed_rpm{%s,fan="%d"}' % (J, f))
    q("fan%d_img" % f, 'fan_speed_rpm{%s,fan="%d"}' % (J, f))
    q("air%d_img" % f, 'fan_speed_rpm{%s,fan="%d"}' % (J, f))
    q("fan%d_mode" % f, 'fan_mode{%s,fan="%d"}' % (J, f))
    q("fan%d_smart" % f, 'fangui_smart_enabled{%s,fan="%d"}' % (J, f))
    q("fan%d_target" % f, 'fangui_smart_target_celsius{%s,fan="%d"}' % (J, f))
for nm, sensor in (("cpu", "cpu"), ("gpu", "gpu"), ("nvme", "nvme"), ("dimm", "dimm")):
    q(nm + "_temp", 'hw_temperature_celsius{%s,sensor="%s"}' % (J, sensor))
    q("glow_" + nm, 'hw_temperature_celsius{%s,sensor="%s"}' % (J, sensor))
for dev, _, _ in DRIVES:
    q("drive_" + dev, 'hw_drive_temperature_celsius{%s,dev="%s"}' % (J, dev))
    q("glow_" + dev, 'hw_drive_temperature_celsius{%s,dev="%s"}' % (J, dev))
    q("led_" + dev, 'rate(node_disk_io_time_seconds_total{job=~"$node_job",device="%s"}[$__rate_interval])' % dev)
q("cpu_w", 'cpu_package_watts{%s}' % J)
q("gpu_w", 'gpu_watts{%s}' % J)
q("cpu_mhz", 'cpu_frequency_mhz_avg{%s}' % J)
q("load1", 'host_load_average{%s,window="1m"}' % J)
q("boost", 'cpu_boost_enabled{%s}' % J)
q("gpu_video", 'gpu_engine_busy_percent{%s,engine="Video"}' % J)
q("gpu_rc6", 'clamp_max(gpu_rc6_percent{%s}, 100)' % J)
q("uptime", 'host_uptime_seconds{%s}' % J)
q("mem_avail", 'host_memory_bytes{%s,kind="available"}' % J)
q("arc_size", 'zfs_arc_bytes{%s,kind="size"}' % J)
q("arc_hit", '100 * rate(zfs_arc_hits_total{%s}[$__rate_interval]) / (rate(zfs_arc_hits_total{%s}[$__rate_interval]) + rate(zfs_arc_misses_total{%s}[$__rate_interval]))' % (J, J, J))
for _i, _pool in enumerate(POOLS):
    q("pool_%d" % (_i + 1), 'zfs_pool_online{%s,pool="%s"}' % (J, _pool))
q("guard", 'fangui_guard_tripped{%s}' % J)
q("driver", 'fangui_driver_loaded{%s}' % J)


def canvas_targets():
    # "or on() vector(NaN)": if a metric does not exist (no Intel GPU, no RAPL, no node-exporter ...) the field still exists and
    # shows "n/a" (special value mapping) instead of "Field not found"
    return [{"datasource": dict(DS), "expr": "(%s) or on() vector(NaN)" % e, "legendFormat": n, "instant": True, "range": False,
             "refId": "Q%02d" % i} for i, (n, e) in enumerate(queries)]


# ----------------------------------------------------------------------------- field config
def steps(*pairs):
    s = [{"color": pairs[0][1], "value": None}]
    for v, c in pairs[1:]:
        s.append({"color": c, "value": v})
    return {"mode": "absolute", "steps": s}


def ov(field, **props):
    return {"matcher": {"id": "byName", "options": field}, "properties": [{"id": k, "value": v} for k, v in props.items()]}


def na_map(start=0):
    return [{"type": "special", "options": {"match": m, "result": {"index": start + i, "text": "n/a", "color": T["MUT"]}}}
            for i, m in enumerate(("nan", "null"))]


def txt_map(rows):
    out = [{"type": "value", "options": {str(v): {"index": i, "text": t, "color": c}}} for i, (v, t, c) in enumerate(rows)]
    return out + na_map(len(out))


# warm / hot limits per sensor (match the colour thresholds and the thermal guard)
LIMITS = {"cpu": (52, 60), "gpu": (70, 80), "nvme": (55, 65), "dimm": (50, 60), "drive": (42, 45)}


def field_overrides(fan_prefix, layered, compact=False):
    G, Y, O, R, B = T["G"], T["Y"], T["O"], T["R"], T["B"]
    neutral = steps((None, T["TXT"]))
    t_cpu, t_drv = steps((None, G), (52, Y), (60, O), (65, R)), steps((None, G), (42, Y), (45, O), (48, R))
    t_gpu, t_dimm, t_nvme = steps((None, G), (70, Y), (80, O), (85, R)), steps((None, G), (50, Y), (60, O), (70, R)), steps((None, G), (55, Y), (65, O), (75, R))
    o = []
    for f in (1, 2):
        o.append(ov("fan%d_rpm" % f, unit="rotrpm", decimals=0, thresholds=neutral))
        fm = fan_mappings(fan_prefix, f)
        if fm:
            o.append(ov("fan%d_img" % f, mappings=fm))
        o.append(ov("fan%d_mode" % f, mappings=txt_map([(2, "AUTO", G), (1, "OVRD" if compact else "OVERRIDE", Y), (0, "FULL", O)]), thresholds=neutral))
        o.append(ov("fan%d_smart" % f, mappings=txt_map([(1, "SMART", B), (0, " ", T["MUT"])]), thresholds=neutral))
        o.append(ov("fan%d_target" % f, unit="celsius", decimals=0, thresholds=neutral))
    o += [
        ov("cpu_temp", unit="celsius", decimals=0, thresholds=t_cpu), ov("gpu_temp", unit="celsius", decimals=0, thresholds=t_gpu),
        ov("nvme_temp", unit="celsius", decimals=0, thresholds=t_nvme), ov("dimm_temp", unit="celsius", decimals=0, thresholds=t_dimm),
        ov("cpu_w", unit="watt", decimals=1, thresholds=steps((None, B))), ov("gpu_w", unit="watt", decimals=1, thresholds=steps((None, B))),
        ov("cpu_mhz", unit="suffix: MHz", decimals=0, thresholds=neutral), ov("load1", unit="none", decimals=2, thresholds=neutral),
        ov("boost", mappings=txt_map([(1, "ON", O), (0, "OFF", G)]), thresholds=neutral),
        ov("gpu_video", unit="percent", decimals=0, thresholds=neutral), ov("gpu_rc6", unit="percent", decimals=0, thresholds=neutral),
        ov("uptime", unit="s", decimals=0, thresholds=neutral), ov("mem_avail", unit="bytes", decimals=1, thresholds=neutral),
        ov("arc_size", unit="bytes", decimals=1, thresholds=neutral), ov("arc_hit", unit="percent", decimals=1, thresholds=neutral),
        ov("guard", mappings=txt_map([(0, "GUARD OK", G), (1, "GUARD TRIPPED", R)]), thresholds=neutral),
        ov("driver", mappings=txt_map([(1, "DRIVER OK", G), (0, "NO DRIVER", R)]), thresholds=neutral),
    ]
    for pool in ["pool_%d" % (i + 1) for i in range(len(POOLS))]:
        o.append(ov(pool, mappings=txt_map([(1, "ONLINE", G), (0, "PROBLEM", R)]), thresholds=neutral))
    for dev, _, _ in DRIVES:
        o.append(ov("drive_" + dev, unit="celsius", decimals=0, thresholds=t_drv))
    # overlays chosen by value (isometric view only)
    if layered:
        for nm, comp in GLOW_COMP.items():
            if glow_ok(comp):
                o.append(ov("glow_" + nm, mappings=threshold_mappings(glow_files(comp), *LIMITS[nm])))
        for i, (dev, _, _) in enumerate(DRIVES):
            if tray_glow_ok(i + 1):
                o.append(ov("glow_" + dev, mappings=threshold_mappings(glow_files("tray_%d" % (i + 1)), *LIMITS["drive"])))
            if led_ok(i + 1):
                o.append(ov("led_" + dev, mappings=activity_mappings("iso_led_tray_%d_on.svg" % (i + 1), "iso_led_tray_%d_off.svg" % (i + 1))))
        for kind, fld in (("cpu", "air1_img"), ("case", "air2_img")):
            if air_ok(kind):
                o.append(ov(fld, mappings=airflow_mappings("iso_airflow_%s_on.svg" % kind, "iso_airflow_%s_off.svg" % kind)))
    return o


# ----------------------------------------------------------------------------- canvas element builders
class View:
    def __init__(self):
        self.elements = []

    @staticmethod
    def _bg(color="transparent", image=None, size="contain", fixed=None):
        bg = {"color": {"fixed": color}}
        if image:
            bg["image"] = {"mode": "field", "field": image, "fixed": ""}
            bg["size"] = size
        elif fixed:
            bg["image"] = {"mode": "fixed", "fixed": fixed}
            bg["size"] = size
        return bg

    def el(self, etype, name, x, y, w, h, config, bg=None, border=None):
        self.elements.append({
            "type": etype, "name": name, "config": config,
            "background": bg or self._bg(),
            "border": border or {"color": {"fixed": "transparent"}, "width": 0},
            "constraint": {"horizontal": "scale", "vertical": "scale"},   # percentages: stretches with the panel, fonts stay fixed
            "placement": {"left": round(x * 100.0 / CW, 3), "right": round((CW - x - w) * 100.0 / CW, 3),
                          "top": round(y * 100.0 / CH, 3), "bottom": round((CH - y - h) * 100.0 / CH, 3), "rotation": 0},
        })

    def _cfg(self, text, size, color, align="center"):
        return {"align": align, "valign": "middle", "color": {"fixed": color}, "size": size, "text": {"mode": "fixed", "fixed": text}}

    def box(self, name, x, y, w, h, fill="transparent", edge=None, bw=2, radius=8, text="", size=12, color=None, edge_field=None):
        edge = edge or T["EDGE"]
        bcol = {"field": edge_field, "fixed": edge} if edge_field else {"fixed": edge}
        self.el("rectangle", name, x, y, w, h, self._cfg(text, size, color or T["MUT"]), self._bg(fill), {"color": bcol, "width": bw, "radius": radius})

    def chip(self, name, x, y, w, h):
        self.box(name, x, y, w, h, T["CARD"], T["EDGE"], 0, 7)

    def label(self, name, x, y, w, h, text, size=11, color=None, align="left"):
        self.el("text", name, x, y, w, h, self._cfg(text, size, color or T["MUT"], align))

    def value(self, name, x, y, w, h, field, size=20, align="left", colored=True, color=None):
        col = {"field": field, "fixed": color or T["TXT"]} if colored else {"fixed": color or T["TXT"]}
        self.el("metric-value", name, x, y, w, h, {"align": align, "valign": "middle", "color": col, "size": size,
                                                   "text": {"mode": "field", "field": field, "fixed": ""}})

    def cell(self, name, x, y, w, caption, field, size=22):
        self.label(name + "_cap", x, y, w, 14, caption, 10)
        self.value(name, x, y + 12, w, size + 12, field, size)

    def row(self, name, x, y, w, caption, field, size=13):
        self.label(name + "_cap", x, y, w * 0.55, 24, caption, 12)
        self.value(name, x + w * 0.45, y, w * 0.55, 24, field, size, "right")

    def image(self, name, x, y, w, h, field):
        self.el("rectangle", name, x, y, w, h, self._cfg("", 12, T["MUT"]), self._bg("transparent", field, "fill"))

    def fixed_layer(self, name, svg):
        self.el("rectangle", name, 0, 0, CW, CH, self._cfg("", 12, T["MUT"]), self._bg("transparent", fixed=data_uri(svg), size="fill"))

    def icon(self, name, x, y, size, asset):
        if not asset_exists("icon_%s.svg" % asset):
            return
        svg = read_asset("icon_%s.svg" % asset).replace("#8791a3", T["MUT"])
        self.el("rectangle", name, x, y, size, size, self._cfg("", 10, T["MUT"]), self._bg("transparent", fixed=data_uri(svg), size="contain"))


def root_for(view, background_svg):
    # Grafana ignores the root frame's background `size` (it keeps the picture's proportions, so it letterboxes whenever the panel is
    # wider or taller than the 1100x742 drawing while every overlay is positioned in percent of the panel). The drawing is therefore
    # a full-size element with `fill` as the bottom-most layer, the same way the other full-size layers are done.
    base = View()
    base.fixed_layer("case_background", background_svg)
    view.elements.insert(0, base.elements[0])
    return {
        "background": {"color": {"fixed": "transparent"}},
        "border": {"color": {"fixed": "transparent"}},
        "constraint": {"horizontal": "left", "vertical": "top"},
        "elements": view.elements, "name": "Element root",
        "placement": {"height": 100, "left": 0, "rotation": 0, "top": 0, "width": 100}, "type": "frame",
    }


# ----------------------------------------------------------------------------- view 1: flat blueprint
def build_blueprint(theme):
    v = View()
    wood = T["WOOD"]
    v.chip("hdr_chip", 36, 20, 1034, 36)
    v.label("title", 44, 20, 520, 34, LABELS["title_flat"], 20, T["TXT"])
    v.value("driver_badge", 800, 22, 130, 28, "driver", 12, "center")
    v.value("guard_badge", 940, 22, 130, 28, "guard", 12, "center")
    v.chip("mb_cap_chip", 44, 66, 330, 18)
    v.label("mb_cap", 46, 68, 560, 18, LABELS["board_caption"], 10)

    v.label("cpu_title", 70, 106, 220, 16, LABELS["cpu_long"], 11)
    v.cell("cpu_temp", 70, 124, 110, "TEMP", "cpu_temp", 26)
    v.cell("cpu_w", 190, 124, 110, "PACKAGE POWER", "cpu_w", 26)
    v.cell("cpu_mhz", 70, 170, 110, "CLOCK", "cpu_mhz", 16)
    v.cell("load1", 190, 170, 110, "LOAD 1m", "load1", 16)
    v.chip("nvme_chip", 62, 224, 244, 42)
    v.label("nvme_cap", 70, 230, 150, 30, LABELS["nvme_long"], 11)
    v.value("nvme_temp", 224, 230, 76, 30, "nvme_temp", 20, "right")
    v.label("gpu_title", 70, 280, 230, 16, LABELS["gpu_long"], 11)
    v.cell("gpu_temp", 70, 296, 70, "TEMP", "gpu_temp", 18)
    v.cell("gpu_w", 148, 296, 70, "POWER", "gpu_w", 18)
    v.cell("gpu_video", 226, 296, 70, "VIDEO", "gpu_video", 18)

    v.label("cooler_cap", 332, 106, 240, 14, LABELS["cooler"], 10, align="center")
    if flat_fan_ok(1):
        v.image("fan1", 352, 124, 200, 200, "fan1_img")
    v.chip("fan1_chip", 330, 322, 252, 28)
    v.value("fan1_rpm", 330, 322, 120, 26, "fan1_rpm", 17, "center")
    v.value("fan1_mode", 452, 322, 62, 26, "fan1_mode", 11, "center")
    v.value("fan1_smart", 516, 322, 62, 26, "fan1_smart", 11, "center")
    v.chip("dimm_chip", 606, 298, 150, 60)
    v.label("dimm_cap", 600, 302, 160, 14, LABELS["memory_long"], 10, align="center")
    v.cell("dimm_temp", 618, 318, 130, "DIMM TEMP", "dimm_temp", 16)

    v.label("sys_title", 796, 96, 240, 16, "SYSTEM", 11)
    for i, (cap, fld) in enumerate([("Uptime", "uptime"), ("CPU boost", "boost"), ("RAM available", "mem_avail"), ("ZFS ARC size", "arc_size"),
                                    ("ARC hit ratio", "arc_hit"), ("GPU deep idle (RC6)", "gpu_rc6"), ("GPU power", "gpu_w")]):
        v.row("r_%s" % fld, 796, 118 + 30 * i, 246, cap, fld)

    v.label("wood_text", 30, 374, 1040, 40, LABELS["front_text"], 15, wood, "center")
    v.label("bay_cap", 46, 428, 540, 18, LABELS["bays_caption"], 10)
    slots = [(50, 452), (50, 534), (50, 616), (320, 452), (320, 534), (320, 616)]      # tray order: left column top-down, then right
    for i, (sx, sy) in enumerate(slots):
        if i < len(DRIVES):
            dev, desc, _ = DRIVES[i]
            nm = "bay_%s" % dev
            v.box(nm, sx, sy, 260, 74, "transparent", T["EDGE"], 3, 8, edge_field="drive_" + dev)
            v.label(nm + "_dev", sx + 14, sy + 8, 150, 18, dev, 14, T["TXT"])
            v.label(nm + "_desc", sx + 14, sy + 30, 160, 34, desc, 11)
            v.value(nm + "_t", sx + 160, sy + 14, 90, 46, "drive_" + dev, 30, "right")
        else:
            v.label("bay_empty%d" % i, sx, sy, 260, 74, "empty bay", 11, T["FAINT"], "center")

    if flat_fan_ok(2):
        v.image("fan2", 610, 470, 180, 180, "fan2_img")
    v.chip("fan2_cap_chip", 640, 450, 140, 18)
    v.label("fan2_cap", 600, 452, 200, 16, LABELS["fan2_caption"], 10, align="center")
    v.chip("fan2_chip", 600, 654, 220, 52)
    v.value("fan2_rpm", 604, 656, 100, 28, "fan2_rpm", 17, "center")
    v.value("fan2_mode", 706, 656, 56, 28, "fan2_mode", 11, "center")
    v.value("fan2_smart", 764, 656, 52, 28, "fan2_smart", 11, "center")
    v.label("fan2_tcap", 604, 686, 90, 16, "SMART TARGET", 9, align="center")
    v.value("fan2_target", 694, 684, 60, 20, "fan2_target", 11, "left", False, T["MUT"])
    v.label("zfs_title", 844, 458, 190, 16, "ZFS POOLS", 11)
    for i, pool in enumerate(POOLS):
        v.row("p_pool_%d" % (i + 1), 844, 480 + 30 * i, 198, pool, "pool_%d" % (i + 1))
    v.label("psu_text", 830, 604, 226, 100, LABELS["psu_long"], 11, T["FAINT"], "center")

    fan2_el = next((e for e in v.elements if e["name"] == "fan2"), None)     # animated airflow: case fan -> the right-hand bays
    targets = [DRIVES[3][0]] if len(DRIVES) > 3 else []                       # right column top (tray 4); tray 5/6 are empty labels
    if fan2_el is not None and targets:
        fan2_el["connections"] = [{
            "color": {"fixed": FAN_ACCENT[2]}, "direction": {"fixed": "forward", "mode": "fixed"},
            "lineStyle": {"animate": True, "style": "dashed"}, "path": "straight",
            "radius": {"fixed": 15, "max": 200, "min": 0}, "size": {"fixed": 2, "max": 10, "min": 1},
            "source": {"x": -1, "y": 0}, "sourceOriginal": {"x": 610, "y": 560},
            "target": {"x": 1, "y": 0}, "targetName": "bay_%s" % d, "targetOriginal": {"x": 580, "y": 489},
        } for d in targets]
    return v, flat_background(theme), ""


# ----------------------------------------------------------------------------- view 2: isometric (layered dark / composite light)
def build_isometric(theme):
    v = View()
    layered = iso_layered(theme)
    tip_accent = {}

    def badge(comp, caption, icon, items, border_field=None, bw=2, accent=None):
        """150x36 badge on the manifest's slot for `comp`. items: (field, size, x_offset, width, align). Skipped if the artwork has no such part."""
        if not have(comp):
            return
        b = MANIFEST[comp]["badge"]
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        v.box("b_%s" % comp, x, y, w, h, T["CARD"], T["EDGE"], bw, 8, edge_field=border_field)
        v.icon("b_%s_ic" % comp, x + 5, y + 4, 13, icon)
        v.label("b_%s_cap" % comp, x + 21, y + 2, w - 24, 14, caption, 9)
        for k, (fld, size, ox, ow, al) in enumerate(items):
            v.value("b_%s_v%d" % (comp, k), x + ox, y + 15, ow, h - 16, fld, size, al)
        tip_accent[comp] = accent or T["LEAD"]

    # --- artwork layers in z-order (the manifest lists the order); everything optional
    if layered:
        v.fixed_layer("L10_components", read_asset("iso_10_components.svg"))
    for nm, comp in GLOW_COMP.items():
        if glow_ok(comp):
            v.image("L12_glow_%s" % nm, 0, 0, CW, CH, "glow_" + nm)
    for f in (1, 2):
        if iso_fan_ok(f):
            v.image("L15_fan%d" % f, 0, 0, CW, CH, "fan%d_img" % f)
    if layered:
        v.fixed_layer("L20_trays_empty", read_asset("iso_20_trays_empty.svg"))
        v.fixed_layer("L25_drives", read_asset("iso_25_drives.svg"))
    for i, (dev, _, _) in enumerate(DRIVES):
        if tray_glow_ok(i + 1):
            v.image("L28_glow_%s" % dev, 0, 0, CW, CH, "glow_" + dev)
    if layered:
        v.fixed_layer("L30_front_panel", read_asset("iso_30_front_panel.svg"))
        v.fixed_layer("L40_mesh", read_asset("iso_40_mesh_foreground.svg"))
    for kind, fld in (("cpu", "air1_img"), ("case", "air2_img")):
        if air_ok(kind):
            v.image("L42_air_%s" % kind, 0, 0, CW, CH, fld)
    for i, (dev, _, _) in enumerate(DRIVES):
        if led_ok(i + 1):
            v.image("L45_led_%s" % dev, 0, 0, CW, CH, "led_" + dev)

    # --- badges on the manifest slots (leader lines are an overlay generated below)
    badge("cpu_cooler", LABELS["cpu"], "cpu", [("cpu_temp", 15, 6, 60, "left"), ("cpu_w", 13, 70, 76, "left")], "cpu_temp", accent=T["B"])
    if iso_fan_ok(1):
        badge("cpu_fan", LABELS["fan1"], "fan", [("fan1_rpm", 14, 6, 74, "left"), ("fan1_mode", 9, 80, 36, "center"), ("fan1_smart", 8, 116, 32, "center")],
              accent=FAN_ACCENT[1])
    badge("gpu", LABELS["gpu"], "gpu", [("gpu_temp", 15, 6, 44, "left"), ("gpu_w", 12, 52, 48, "left"), ("gpu_video", 12, 104, 40, "left")], "gpu_temp",
          accent="#c084fc")
    badge("nvme", "NVME", "nvme", [("nvme_temp", 15, 6, 60, "left")], "nvme_temp", accent=T["MUT"])
    badge("dimm_bank", "MEMORY", "ram", [("dimm_temp", 15, 6, 50, "left"), ("mem_avail", 11, 60, 86, "right")], "dimm_temp", accent=T["MUT"])
    if iso_fan_ok(2):
        badge("case_fan", LABELS["fan2"], "fan", [("fan2_rpm", 14, 6, 74, "left"), ("fan2_mode", 9, 80, 36, "center"), ("fan2_smart", 8, 116, 32, "center")],
              accent=FAN_ACCENT[2])
    for i, (dev, desc, kind) in enumerate(DRIVES):
        comp = "tray_%d" % (i + 1)
        if not have(comp):
            continue
        badge(comp, "%s  ·  %s" % (dev, kind.upper()), kind, [("drive_" + dev, 15, 6, 70, "left")], "drive_" + dev, 3, accent=T["G"])
        bb = MANIFEST[comp]["badge"]
        v.label("b_%s_desc" % comp, bb["x"] + 66, bb["y"] + 15, 82, 20, " ".join(desc.split()[-2:]), 10, align="right")
    if have("psu"):
        b = MANIFEST["psu"]["badge"]
        v.box("b_psu", b["x"], b["y"], b["w"], b["h"], T["CARD"], T["EDGE"], 2, 8)
        v.icon("b_psu_ic", b["x"] + 5, b["y"] + 4, 13, "bolt")
        v.label("b_psu_cap", b["x"] + 21, b["y"] + 2, b["w"] - 24, 14, LABELS["psu"], 9)
        v.label("b_psu_txt", b["x"] + 6, b["y"] + 15, b["w"] - 10, 18, "no telemetry", 11, T["FAINT"])
        tip_accent["psu"] = T["FAINT"]

    # --- header and cards in free badge slots (fallback positions if the artwork's manifest has no such slot)
    v.label("title", 8, 6, 148, 20, LABELS["title"], 12, T["TXT"])
    v.value("driver_badge", 8, 28, 120, 18, "driver", 10, "left")
    v.value("guard_badge", 8, 46, 120, 18, "guard", 10, "left")
    sx, sy = slot("free_1", slot("wood_panel", (6, 132)))     # compact system card, two lines (manifest slot free_1, legacy name wood_panel)
    v.box("c_sys", sx, sy, 150, 36, T["CARD"], T["EDGE"], 2, 8)
    v.label("c_sys_0_c", sx + 8, sy + 2, 70, 16, "Uptime", 10)
    v.value("c_sys_0", sx + 70, sy + 2, 74, 16, "uptime", 11, "right")
    v.label("c_sys_1_c", sx + 8, sy + 18, 70, 16, "CPU boost", 10)
    v.value("c_sys_1", sx + 70, sy + 18, 74, 16, "boost", 11, "right")
    zx, zy = slot("free_2", slot("mesh_top", (786, 6)))        # ZFS card (free_2, legacy mesh_top)
    v.box("c_zfs", zx, zy, 150, 78, T["CARD"], T["EDGE"], 2, 8)
    v.label("c_zfs_t", zx + 8, zy + 2, 130, 14, "ZFS POOLS", 9)
    for i, pool in enumerate(POOLS):
        v.label("c_zfs_%d_c" % i, zx + 8, zy + 18 + 18 * i, 70, 18, pool, 10)
        v.value("c_zfs_%d" % i, zx + 70, zy + 18 + 18 * i, 74, 18, "pool_%d" % (i + 1), 10, "right")
    mx, my = slot("free_3", slot("dimm_1", (630, 6)))          # cache / GPU idle card (free_3, legacy dimm_1)
    v.box("c_mem", mx, my, 150, 78, T["CARD"], T["EDGE"], 2, 8)
    v.label("c_mem_t", mx + 8, my + 2, 130, 14, "ZFS CACHE · GPU IDLE", 9)
    for i, (cap, fld) in enumerate([("ARC size", "arc_size"), ("ARC hit", "arc_hit"), ("GPU RC6", "gpu_rc6")]):
        v.label("c_mem_%d_c" % i, mx + 8, my + 18 + 18 * i, 70, 18, cap, 10)
        v.value("c_mem_%d" % i, mx + 70, my + 18 + 18 * i, 74, 18, fld, 10, "right")

    # --- leader lines (generated overlay, drawn under the badges)
    if tip_accent:
        v.fixed_layer("L50_leaders", leaders_svg([(c, a) for c, a in tip_accent.items()]))
        leader = v.elements.pop(next(i for i, e in enumerate(v.elements) if e["name"] == "L50_leaders"))
        v.elements.insert(next(i for i, e in enumerate(v.elements) if e["name"].startswith("b_")), leader)

    return v, iso_background(theme), "iso_"


# ----------------------------------------------------------------------------- panels
_id = [0]


def pid():
    _id[0] += 1
    return _id[0]


def tgt(expr, legend, ref):
    return {"datasource": dict(DS), "expr": expr, "legendFormat": legend, "refId": ref, "range": True}


def color_override(name, color):
    return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


def ts(title, desc, x, y, w, h, targets, unit, overrides_=None, minv=None, maxv=None, fill=18):
    custom = {"drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2, "fillOpacity": fill, "gradientMode": "opacity",
              "showPoints": "never", "spanNulls": True}
    if minv is not None:
        custom["axisSoftMin"] = minv
    if maxv is not None:
        custom["axisSoftMax"] = maxv
    return {"id": pid(), "type": "timeseries", "title": title, "description": desc, "datasource": dict(DS),
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
            "fieldConfig": {"defaults": {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
                            "overrides": overrides_ or []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}}}


CANVAS_H = 20   # 20 rows is about 750 px: matches the 1100x742 drawing at a 16-column width


def charts():
    return [
        ts("Fan speed", "Measured RPM. Automatic = BIOS curve; override and smart modes appear as flat steps.", 16, 0, 8, 5,
           [tgt('fan_speed_rpm{%s,fan=~"1|2"}' % J, "{{name}}", "A")], "rotrpm",
           [color_override("CPU fan", FAN_ACCENT[1]), color_override("Case fan (HDD)", FAN_ACCENT[2])], 0),
        ts("Temperatures", "CPU (Tctl), GPU, NVMe, memory and the hottest drive.", 16, 5, 8, 5,
           [tgt('hw_temperature_celsius{%s,sensor=~"cpu|gpu|nvme|dimm|drive_max"}' % J, "{{sensor}}", "A")], "celsius", None, None, None, 8),
        ts("Power", "CPU package (RAPL) and GPU (i915 energy counter), live rates.", 16, 10, 8, 5,
           [tgt('cpu_package_watts{%s}' % J, "CPU", "A"), tgt('gpu_watts{%s}' % J, "GPU", "B")], "watt",
           [color_override("CPU", "#4da3ff"), color_override("GPU", "#c084fc")], 0),
        ts("Smart mode: temperature vs target", "Smoothed temperature the controller acts on, against its target (only while smart mode is on).", 16, 15, 8, 5,
           [tgt('fangui_smart_temp_smoothed_celsius{%s}' % J, "{{sensor}} (smoothed)", "A"),
            tgt('fangui_smart_target_celsius{%s} and on(fan) (fangui_smart_enabled{%s} == 1)' % (J, J), "{{sensor}} target", "B")],
           "celsius", None, None, None, 6),
        ts("Drive temperatures", "Every drive reported by drivetemp.", 0, CANVAS_H, 8, 8,
           [tgt('hw_drive_temperature_celsius{%s}' % J, "{{dev}} {{drive}}", "A")], "celsius", None, None, None, 8),
        ts("Arc GPU engines", "intel_gpu_top engine utilisation via the intel-gpu-exporter.", 8, CANVAS_H, 8, 8,
           [tgt('gpu_engine_busy_percent{%s}' % J, "{{engine}}", "A")], "percent", None, 0, 100, 12),
        ts("Fan control state", "Mode per fan: 2 = automatic (BIOS), 1 = override or smart, 0 = full speed. Guard: 1 = tripped.", 16, CANVAS_H, 8, 8,
           [tgt('fan_mode{%s,fan=~"1|2"}' % J, "{{name}} mode", "A"), tgt('fangui_guard_tripped{%s}' % J, "guard tripped", "B")],
           "none", None, 0, 2, 0),
    ]


NJ = 'job=~"$node_job"'


def node_charts(y):
    return [
        ts("CPU usage (node-exporter)", "Busy and I/O-wait share of all CPU threads.", 0, y, 8, 7,
           [tgt('100 * (1 - avg(rate(node_cpu_seconds_total{%s,mode="idle"}[$__rate_interval])))' % NJ, "busy", "A"),
            tgt('100 * avg(rate(node_cpu_seconds_total{%s,mode="iowait"}[$__rate_interval]))' % NJ, "iowait", "B")],
           "percent", [color_override("busy", "#4da3ff"), color_override("iowait", "#f87171")], 0, 100, 12),
        ts("Network (node-exporter)", "Receive and transmit per physical interface (virtual, bridge and docker interfaces hidden).", 8, y, 8, 7,
           [tgt('rate(node_network_receive_bytes_total{%s,device!~"lo|veth.*|br-.*|docker.*"}[$__rate_interval])' % NJ, "{{device}} rx", "A"),
            tgt('rate(node_network_transmit_bytes_total{%s,device!~"lo|veth.*|br-.*|docker.*"}[$__rate_interval])' % NJ, "{{device}} tx", "B")],
           "Bps", None, 0, None, 10),
        ts("Disk throughput (node-exporter)", "Read and write per physical disk (partitions, loop and zram devices hidden).", 16, y, 8, 7,
           [tgt('rate(node_disk_read_bytes_total{%s,device=~"sd[a-z]+|nvme[0-9]+n[0-9]+"}[$__rate_interval])' % NJ, "{{device}} read", "A"),
            tgt('rate(node_disk_written_bytes_total{%s,device=~"sd[a-z]+|nvme[0-9]+n[0-9]+"}[$__rate_interval])' % NJ, "{{device}} write", "B")],
           "Bps", None, 0, None, 10),
    ]


def dashboard(uid, title, desc, builder, theme):
    set_theme(theme)
    _id[0] = 0
    view, background, fan_prefix = builder(theme)
    canvas = {
        "id": pid(), "type": "canvas", "title": "", "description": "Fans spin at a speed that follows their RPM (scaled: 1800 RPM = one turn per "
        "second; motion blur above %d RPM); numbers are live from the fan-gui metrics." % BLUR_FROM_RPM,
        "datasource": dict(DS), "gridPos": {"x": 0, "y": 0, "w": 16, "h": CANVAS_H},
        "targets": canvas_targets(),
        "transformations": [{"id": "joinByField", "options": {"byField": "Time", "mode": "outer"}}],
        "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}, "color": {"mode": "thresholds"},
                                     "mappings": na_map()},
                        "overrides": field_overrides(fan_prefix, builder is build_isometric, builder is build_isometric)},
        "options": {"inlineEditing": False, "showAdvancedTypes": False, "panZoom": False, "infinitePan": False, "zoomToContent": False,
                    "tooltip": {"mode": "single", "disableForOneClick": False}, "root": root_for(view, background)},
    }
    panels = [canvas] + charts() + node_charts(CANVAS_H + 8)
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids)), "duplicate panel ids"
    for p in panels:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24 and g["w"] > 0 and g["h"] > 0, p["title"]
    names = [e["name"] for e in view.elements]
    assert len(names) == len(set(names)), "duplicate canvas element names: %s" % sorted({n for n in names if names.count(n) > 1})
    fields = {n for n, _ in queries}
    for e in view.elements:
        c = e["config"]
        f = c["text"].get("field") if isinstance(c.get("text"), dict) else None
        assert not f or f in fields, "element %s uses unknown field %s" % (e["name"], f)
        img = e["background"].get("image", {})
        assert img.get("mode") != "field" or img.get("field") in fields, "element %s uses unknown image field %s" % (e["name"], img.get("field"))
    return {
        "uid": uid, "title": title, "description": desc, "tags": ["truenas", "fans", "homelab", "canvas"],
        "editable": True, "graphTooltip": 1, "refresh": REFRESH, "timezone": "browser", "time": {"from": "now-3h", "to": "now"},
        "timepicker": {"refresh_intervals": ["1s", "2s", "5s", "10s", "30s", "1m", "5m", "15m"]},   # a refresh value must be in this list or Grafana ignores it
        "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                                  "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]},
        "links": [{"title": "Case views", "type": "dashboards", "tags": ["canvas"], "asDropdown": True, "includeVars": True, "keepTime": True,
                   "icon": "external link", "targetBlank": False, "url": "", "tooltip": "Switch between the flat / isometric and dark / light case views"}],
        "panels": panels,
        "templating": {"list": [
            {"name": "DS_PROMETHEUS", "label": "Prometheus", "type": "datasource", "query": "prometheus", "refresh": 1,
             "includeAll": False, "multi": False, "options": [], "regex": "", "current": {}},
            {"name": "job", "label": "Job", "type": "query", "datasource": dict(DS),
             "query": {"query": "label_values(fangui_up, job)", "qryType": 1, "refId": "job"},
             "definition": "label_values(fangui_up, job)", "refresh": 2, "includeAll": False, "multi": False, "sort": 1,
             "current": {"text": DEFAULT_JOB, "value": DEFAULT_JOB}, "options": []},
            {"name": "node_job", "label": "Node job", "type": "constant", "query": NODE_JOB, "hide": 2,
             "current": {"text": NODE_JOB, "value": NODE_JOB}, "options": [{"selected": True, "text": NODE_JOB, "value": NODE_JOB}]}]},
        "version": 1,
    }


OUTPUTS = [
    ("fan-control-isometric.json", build_isometric, "dark", "truenas-case-fans-iso", "TrueNAS Case & Fans (Jonsbo N4, isometric)",
     "Isometric Jonsbo N4: fans spin with their RPM, heat glows, airflow and live temperatures, power and ZFS state. Data: fan-gui /metrics."),
    ("fan-control-dashboard.json", build_blueprint, "dark", "truenas-case-fans", "TrueNAS Case & Fans (Jonsbo N4)",
     "Flat blueprint of the Jonsbo N4 with fans that spin with their RPM, live temperatures, power and ZFS state. Data: fan-gui /metrics."),
    ("fan-control-isometric-light.json", build_isometric, "light", "truenas-case-fans-iso-light", "TrueNAS Case & Fans (Jonsbo N4, isometric, light)",
     "Light theme isometric Jonsbo N4 with spinning fans, heat glows and live data. Data: fan-gui /metrics."),
    ("fan-control-dashboard-light.json", build_blueprint, "light", "truenas-case-fans-light", "TrueNAS Case & Fans (Jonsbo N4, light)",
     "Light theme flat blueprint of the Jonsbo N4 with spinning fans and live data. Data: fan-gui /metrics."),
]

if __name__ == "__main__":
    # the demo stack (docker-compose.demo.yml) uses 2 s refresh copies of the two dark views so the animation looks lively
    os.makedirs(os.path.join(HERE, "demo"), exist_ok=True)
    for sub, refresh, only_dark in (("", "10s", False), ("demo", "2s", True)):
        REFRESH = refresh
        for fname, builder, theme, uid, title, desc in OUTPUTS:
            if only_dark and theme != "dark":
                continue
            if builder is build_isometric and (not MANIFEST or iso_background(theme) is None):
                print("skipped %-42s (needs assets/manifest.json and %s)" % (os.path.join(sub, fname), "the iso_* layers or case_upper_side%s.svg" % ("_light" if theme == "light" else "")))
                continue
            if builder is build_blueprint and flat_background(theme) is None:
                print("skipped %-42s (needs case_background%s.svg)" % (os.path.join(sub, fname), "_light" if theme == "light" else ""))
                continue
            d = dashboard(uid, title, desc, builder, theme)
            out = os.path.join(HERE, sub, fname)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=1)
            print("wrote %-42s %2d panels, %3d canvas elements, %4.0f KB, refresh %s" % (
                os.path.join(sub, fname), len(d["panels"]), len(d["panels"][0]["options"]["root"]["elements"]), os.path.getsize(out) / 1024.0, refresh))

#!/usr/bin/env python3
"""Audit a folder of dashboard artwork (SVGs + manifest.json) before you use it.

    python3 tools/audit_artwork.py monitoring/grafana/assets
    python3 tools/audit_artwork.py ~/my-case-artwork

Why: the artwork usually comes from an AI illustration agent. SVG files can carry scripts, external requests or text, and Grafana
loads them as images with a few strict requirements. This script checks all of that, checks the manifest, and tells you which
dashboard views the artwork can build. It only reads files and never runs or renders them. Exit code 1 if any ERROR was found.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

CW, CH = 1100, 742
FORBIDDEN_TAGS = {"script", "foreignobject", "image", "text", "tspan", "textpath", "iframe", "audio", "video", "style-link"}
SIZE_WARN = 40_000        # bytes per file
SIZE_ERR = 150_000
TOTAL_WARN = 400_000

errors, warnings, notes = [], [], []


def err(f, msg):
    errors.append("%s: %s" % (f, msg))


def warn(f, msg):
    warnings.append("%s: %s" % (f, msg))


def local(tag):
    return tag.split("}")[-1].lower()


def check_svg(path, name, text):
    # 1) well-formed XML
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        err(name, "not valid XML (%s)" % e)
        return None
    if local(root.tag) != "svg":
        err(name, "root element is not <svg>")
        return None
    # 2) the literal namespace string must be present: Grafana recognises an absolute URL by ':/' (see the README)
    if "http://www.w3.org/2000/svg" not in text:
        err(name, "missing the literal xmlns='http://www.w3.org/2000/svg' (keep the http://)")
    vb = root.get("viewBox")
    if not vb:
        err(name, "no viewBox")
    if root.get("width") and re.search(r"px|\d$", root.get("width")):
        warn(name, "has a fixed width attribute; remove width/height and rely on the viewBox")
    # 3) forbidden content
    for el in root.iter():
        t = local(el.tag)
        if t in FORBIDDEN_TAGS:
            err(name, "contains <%s> (not allowed: scripts, images, text and embedded content break or leak)" % t)
        for attr, val in el.attrib.items():
            a = attr.split("}")[-1].lower()
            if a.startswith("on"):
                err(name, "event handler attribute %s" % attr)
            if a == "href" and not val.startswith("#"):
                err(name, "external reference %s=%r" % (attr, val[:60]))
            if "javascript:" in val.lower():
                err(name, "javascript: URL")
    if re.search(r"@import|url\(\s*['\"]?https?:", text, re.I):
        err(name, "@import or external url() found")
    if "&#" in text and "<!ENTITY" in text:
        err(name, "XML entity declaration")
    # 4) size
    n = len(text.encode("utf-8"))
    if n > SIZE_ERR:
        err(name, "%d KB is too large for a data URI inside the dashboard JSON (limit %d KB)" % (n // 1000, SIZE_ERR // 1000))
    elif n > SIZE_WARN:
        warn(name, "%d KB is large (aim for under %d KB)" % (n // 1000, SIZE_WARN // 1000))
    return vb


def expected_viewbox(name):
    if name.startswith("icon_"):
        return "0 0 24 24"
    if name.startswith(("fan_blue", "fan_teal")):
        return "0 0 120 120"
    if name.startswith(("iso_", "glow_", "case_")):
        return "0 0 %d %d" % (CW, CH)
    return None


def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__)
        return 2
    d = sys.argv[1]
    files = sorted(os.listdir(d))
    svgs = [f for f in files if f.lower().endswith(".svg")]
    names = set(svgs)
    total = 0
    for f in svgs:
        text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
        total += len(text.encode("utf-8"))
        vb = check_svg(os.path.join(d, f), f, text)
        exp = expected_viewbox(f)
        if vb and exp and " ".join(vb.replace(",", " ").split()) != exp:
            err(f, "viewBox is %r, expected %r for this kind of file" % (vb, exp))
        # animation contract
        if re.match(r"(iso_fan\d|fan_(blue|teal))_(spin|blur)\.svg$", f):
            n = len(re.findall(r"dur='1\.2s'", text))
            if n != 1:
                err(f, "must contain exactly one dur='1.2s' (single quotes) on the rotor's animateTransform; found %d" % n)
            if "animateTransform" not in text:
                err(f, "no animateTransform (the fan would not spin)")
        if re.match(r"(iso_fan\d|fan_(blue|teal))_stopped\.svg$", f) and re.search(r"<animate", text):
            err(f, "the stopped fan must be static (no animation)")
    if total > TOTAL_WARN:
        warn("(total)", "%d KB of SVG in total; the dashboard JSON embeds every file several times over (aim under %d KB)" % (total // 1000, TOTAL_WARN // 1000))

    # ------------------------------------------------------------------ manifest
    comps = {}
    mpath = os.path.join(d, "manifest.json")
    if os.path.exists(mpath):
        try:
            comps = json.load(open(mpath, encoding="utf-8"))["components"]
        except (ValueError, KeyError) as e:
            err("manifest.json", "invalid (%s); expected {\"components\": {...}}" % e)
        for name, c in comps.items():
            b = c.get("badge")
            if not b or not all(k in b for k in ("x", "y", "w", "h")) or "arrow_tip" not in b:
                err("manifest.json", "component %s needs badge {x, y, w, h, arrow_tip: [x, y]}" % name)
                continue
            if b["x"] < 0 or b["y"] < 0 or b["x"] + b["w"] > CW or b["y"] + b["h"] > CH:
                err("manifest.json", "badge of %s is outside the %dx%d canvas" % (name, CW, CH))
            tx, ty = b["arrow_tip"]
            if not (0 <= tx <= CW and 0 <= ty <= CH):
                err("manifest.json", "arrow_tip of %s is outside the canvas" % name)
    else:
        notes.append("no manifest.json: the isometric views cannot be built (the flat view does not need it)")

    # ------------------------------------------------------------------ what can be built
    def have_all(*n):
        return all(x in names for x in n)

    layers = ["iso_00_base.svg", "iso_10_components.svg", "iso_20_trays_empty.svg", "iso_25_drives.svg", "iso_30_front_panel.svg", "iso_40_mesh_foreground.svg"]
    report = []
    iso_dark = bool(comps) and (have_all(*layers) or "case_upper_side.svg" in names)
    iso_light = bool(comps) and "case_upper_side_light.svg" in names
    report.append(("isometric dark  (fan-control-isometric.json)", iso_dark, "layered" if have_all(*layers) else "composite" if iso_dark else
                   "needs manifest.json and the six iso_* layers or case_upper_side.svg"))
    report.append(("isometric light (fan-control-isometric-light.json)", iso_light, "needs manifest.json and case_upper_side_light.svg" if not iso_light else ""))
    report.append(("flat dark       (fan-control-dashboard.json)", "case_background.svg" in names, "needs case_background.svg"))
    report.append(("flat light      (fan-control-dashboard-light.json)", "case_background_light.svg" in names, "needs case_background_light.svg"))

    parts = []
    for f in (1, 2):
        ok = have_all(*["iso_fan%d_%s.svg" % (f, k) for k in ("spin", "stopped", "blur")])
        parts.append("iso fan%d %s" % (f, "ok" if ok else "missing (that fan will not animate in the isometric views)"))
        if ok and ("cpu_fan" if f == 1 else "case_fan") not in comps:
            warn("manifest.json", "iso fan %d files exist but the manifest has no %s badge" % (f, "cpu_fan" if f == 1 else "case_fan"))
    for col, f in (("blue", 1), ("teal", 2)):
        ok = have_all(*["fan_%s_%s.svg" % (col, k) for k in ("spin", "stopped", "blur")])
        parts.append("flat fan%d %s" % (f, "ok" if ok else "missing"))
    glows = [c for c in ("cpu_cooler", "gpu", "nvme", "dimm_bank", "psu") + tuple("tray_%d" % i for i in range(1, 9))
             if have_all(*["glow_%s_%s.svg" % (c, s) for s in ("ok", "warm", "hot")])]
    leds = [i for i in range(1, 9) if have_all("iso_led_tray_%d_on.svg" % i, "iso_led_tray_%d_off.svg" % i)]
    air = [k for k in ("cpu", "case") if have_all("iso_airflow_%s_on.svg" % k, "iso_airflow_%s_off.svg" % k)]
    trays = sorted(int(re.sub(r"\D", "", c)) for c in comps if re.fullmatch(r"tray_\d+", c))
    if trays and trays != list(range(1, len(trays) + 1)):
        err("manifest.json", "tray components must be numbered tray_1..tray_N without gaps, found %s" % trays)
    for c in glows:
        if c != "psu" and c not in comps:
            warn("glow_%s_*.svg" % c, "no matching component in the manifest")
    icons = [f for f in svgs if f.startswith("icon_")]

    print("Artwork folder: %s  (%d SVG files, %d KB)\n" % (d, len(svgs), total // 1000))
    print("Dashboard views this artwork can build:")
    for label, ok, why in report:
        shown = why if (not ok or why in ("layered", "composite")) else ""
        print("  [%s] %s%s" % ("x" if ok else " ", label, ("  - " + shown) if shown else ""))
    print("\nParts found:")
    print("  " + "; ".join(parts))
    print("  heat glows for: %s" % (", ".join(glows) or "none"))
    print("  tray LEDs for trays: %s" % (", ".join(map(str, leds)) or "none"))
    print("  airflow overlays: %s" % (", ".join(air) or "none"))
    print("  manifest components (%d): %s" % (len(comps), ", ".join(sorted(comps)) or "none"))
    print("  icons: %d" % len(icons))
    for n in notes:
        print("\nNOTE: " + n)
    if warnings:
        print("\nWARNINGS (%d)" % len(warnings))
        for w in warnings:
            print("  - " + w)
    if errors:
        print("\nERRORS (%d)" % len(errors))
        for e in errors:
            print("  - " + e)
        print("\nFAILED: fix the errors above before using this artwork.")
        return 1
    print("\nPASSED: no scripts, text, images or external references; the files meet the dashboard's requirements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# TrueNAS server: CPU power and fan control - lab notes

> This is the author's lab notebook: what was measured on one machine, what went wrong and why. It is the reference for the *why*. For how to set the project up, read the repository `README.md`. Host names, IP addresses and directories are replaced by placeholders (`<server>`, `/mnt/<pool>/fan-control`).

Recorded 14 November 2026. Server `<server>` (TrueNAS SCALE 27.0.0-RC.1, kernel `6.18.52-debug+truenas`).
Everything below was observed or tested on this machine; "not verified" marks guesses.

## 1. Hardware

| Part | Detail |
|---|---|
| Board | ASUS PRIME B450M-A II |
| CPU | AMD Ryzen 7 2700 (8C/16T), driver `acpi-cpufreq` |
| GPU | ASRock Intel Arc A310 4 GB (`8086:56a6`, subsystem `1849:6007`) at `0b:00.0` |
| Fan chip | **ITE IT8655E** Super I/O (found at port `0x2e`, ID `0x8655`; hwmon base `0x290`) |
| Fans | **fan1** = CPU fan, 4-pin PWM, `pwm1`. **fan2** = case fan pointing at the HDDs, 3-pin (voltage control), `pwm2`. **fan3** = nothing detected (0 RPM), `pwm3` |
| Pools | `big_data`, `new_ssd` (both ONLINE) |

## 2. CPU power / heat

- Governors available: `schedutil` (default, kept) and `performance`. No `powersave` (acpi-cpufreq on Zen 1).
- No settable CPU watt limit exists (`ryzenadj` does not support Zen 1; powercap is read-only).
- **Boost disabled**: CPU 59 °C -> 43 °C at similar load, average clock 3.07 -> 2.15 GHz.
- Persistent via a TrueNAS post-init script (id 3): `echo 0 > /sys/devices/system/cpu/cpufreq/boost`. Untick it under System > Advanced > Init/Shutdown Scripts to undo.
- Optional further saving (not applied): cap `scaling_max_freq`.

## 3. Fans: what we learned

### 3.1 Discovery
1. Stock kernel drivers `nct6775` and `it87` -> "No such device"; no fan sensors visible; the `asus` hwmon only shows one temperature.
2. Reading the Super I/O ID (ITE unlock sequence `87 01 55 55` on port `0x2e`, read registers `0x20/0x21`) returned `0x8655` = IT8655E.
3. The out-of-tree driver <https://github.com/frankcrawford/it87> (commit `bd7a566`) lists the IT8655E as supported. TrueNAS kernel headers exist in `/usr/src/linux-headers-truenas-debug-amd64`, module signing is not enforced (`sig_enforce=N`, lockdown `none`), so an unsigned module loads (taints the kernel).
4. Built in a container (no compiler on the host): `Dockerfile.it87` in `/mnt/<pool>/fan-control`, result `/mnt/<pool>/fan-control/it87-out/it87.ko` (sha256 `d99c3a4c09be...3593`, vermagic `6.18.52-debug+truenas`). Loaded with `insmod`; "Found IT8655E chip at 0x290, revision 6".

### 3.2 What the driver exposes (`/sys/class/hwmon/hwmonN`, name `it8655`; N changes - find it by name)

| File | Meaning | Note |
|---|---|---|
| `fan1_input` / `fan2_input` / `fan3_input` | RPM | fan3 = 0 (no fan) |
| `pwmN` | duty 0-255 | writable; in auto mode the value is NOT the real duty (pwm1 reads 0 while the fan spins ~1500 RPM) |
| `pwmN_enable` | 0 = full speed, 1 = manual, 2 = automatic (BIOS curve) | **2 is the safe state** |
| `pwmN_auto_*` | BIOS curve: channel, points, slope, start | must be left alone |
| `temp1-6_input`, `inX_input` | **Not usable** (all six temps identical 44 °C; voltages unscaled) | Use `k10temp` for CPU temp |

Other useful sensors: CPU `k10temp` Tctl, GPU `i915` temp1, NVMe `nvme`, drives `drivetemp`.

### 3.3 Original automatic settings (restored every time; saved by the GUI in `data/original.json`)

| | auto_channels_temp | point1 / hyst | point2 | point3 | slope | start |
|---|---|---|---|---|---|---|
| fan1 (CPU) | 1 | -128000 / -128000 | 20000 | 70000 | 40 | 0 |
| fan2 (case) | 1 | -128000 / -128000 | 40000 | 70000 | 51 | 63 |

### 3.4 Control tests (all "raise only", baseline CPU 44-46 °C, boost off)

**fan2 (3-pin, case fan)**: manual duty 63 -> 610 RPM, 100 -> ~903, 160 -> ~1295 (auto ran ~770-850 RPM). Restore OK.

**fan1 (4-pin, CPU fan)**: manual duty 120 -> ~1490 RPM, 180 -> ~1945, 255 -> 2490 (auto ran ~1520-1640). CPU temperature stayed 42-46 °C. Restore OK.

### 3.5 Lessons / gotchas
- **Automatic mode runs the fan faster than its `pwm` reading says.** Manual duty 63 on fan2 gave ~610 RPM, *lower* than the automatic ~900. So the GUI floors are 100 (fan2, ~auto speed) and 140 (fan1, ~1650 RPM).
- **Duty 255 makes the driver set `pwmN_enable=0` (full speed, no control).** The GUI never writes more than 250.
- **Restore order matters:** write `pwmN_enable=2` first. Never write a low duty to `pwmN` on the way back (it would slow a fan in manual mode).
- Writing `pwmN` while in automatic mode: fan2 returns `EBUSY` (ignored, then enable=1 is set and the duty written again); fan1 accepted it.
- **The driver changes `pwmN_auto_start` when a manual duty is written** (fan2: 63 -> 100). After going back to automatic, always compare against the saved original and rewrite it. The GUI does this.
- **`/tmp` on TrueNAS is mounted no-exec.** My first CPU-fan test used a restore script in `/tmp`; both the restore and the 45 s watchdog failed with "Permission denied", leaving the CPU fan at full speed (2490 RPM) for ~30 s until I ran `bash /tmp/restore_pwm1.sh`. Run restore code via `bash script` or inline, and keep it on a data pool.
- The in-kernel `it87`/`nct6775` modules and the `asus` WMI hwmon cannot do this; the out-of-tree module is required.
- The module disappears on reboot and may fail to load after a TrueNAS kernel update (vermagic mismatch). Rebuild with `Dockerfile.it87` against the new headers.
- Loading the module taints the kernel (harmless, appears in logs).

### 3.6 Which fan is which (from the owner)
fan1 = CPU fan on the 4-pin CPU_FAN header. fan2 = case fan blowing at the hard disks, 3-pin. fan3 header unused.

## 4. Fan GUI

Folder: `fan-gui/` (this repo) -> deployed in `/mnt/<pool>/fan-control/fan-gui`.

- **URL:** `http://<server>:18765` - user `admin`, password in `/mnt/<pool>/fan-control/fan-gui/env.list` (`FAN_GUI_PASSWORD`). HTTP on the LAN only; do not expose to the internet.
- **Container:** `fan-gui` (image `local/fan-gui`), `--privileged` (needed to write `/sys`), `--network host` (to reach the intel-gpu-exporter on 127.0.0.1:7071; app listens on 18765), `--restart unless-stopped`, volume `data/` for `original.json` (saved BIOS settings), `config.json` (smart settings and remembered duties), `profile_N.json` and `events.log` (audit log). Plain Python 3.12 standard library, no dependencies.
- **Boot:** TrueNAS post-init script id 4 runs `insmod /mnt/<pool>/fan-control/it87-out/it87.ko`. The container shows "driver NOT loaded" (monitoring only) until it is.
- **Shows:** both fan RPMs, mode, duty; CPU, GPU, NVMe, DIMM and drive temperatures; CPU/GPU watts; Arc engine usage (intel_gpu_top); CPU clock/governor/boost/load; 1-hour history charts; event log.
- **Controls:** per-fan manual duty slider with a TTL (15 min, 30 min, 2 h, 8 h), "Automatic" per fan, and "All fans -> automatic".
- **Safety built in**
  - Floors: fan1 >= 140/255, fan2 >= 100/255; max 250. Server-side clamp.
  - Overrides expire (default 30 min) and fall back to automatic.
  - Thermal guard: CPU >= 65 °C, any drive >= 48 °C or GPU >= 85 °C -> all fans automatic and overrides blocked until cooled.
  - `docker stop`/SIGTERM restores automatic (tested).
  - Every return to automatic checks and repairs the saved BIOS curve values.
  - HTTP basic auth, and POSTs need an `X-Requested-With: fan-gui` header (blocks cross-site requests).
  - Fan3 and the BIOS curve are never modified.
- **Tested through the API:** unauthenticated -> 401; missing header -> 403; duty 10 clamped to the floor; manual 120 -> RPM rose; automatic restored; `docker stop` restored automatic.
- **Limits / ideas not built:** no temperature-driven custom curve (the BIOS curve stays in charge), no fan3, no alerts or notifications, no HTTPS. A "follow drive temperature" mode for the case fan and a push notification when the guard trips would be natural next steps.

## 4b. Prometheus metrics

`GET http://<server>:18765/metrics` (same basic auth; Prometheus text format, ~120 lines, no duplicate series).

```yaml
scrape_configs:
  - job_name: fan-gui
    scrape_interval: 15s
    basic_auth: {username: admin, password: <FAN_GUI_PASSWORD>}
    static_configs: [{targets: ['<server>:18765']}]
```

| Metric | Meaning |
|---|---|
| `fan_speed_rpm{fan,name}`, `fan_pwm_duty`, `fan_mode` (0 full, 1 manual, 2 auto), `fan_manual_active`, `fan_manual_seconds_left`, `fan_manual_floor_duty` | fans and the override state |
| `fangui_driver_loaded`, `fangui_guard_tripped`, `fangui_guard_threshold_celsius{sensor}` | health of the tool itself |
| `hw_temperature_celsius{sensor=cpu,gpu,nvme,drive_max,dimm}`, `hw_drive_temperature_celsius{drive,dev}` | temperatures |
| `cpu_frequency_mhz_avg`, `cpu_boost_enabled`, `cpu_governor_info`, `host_load_average{window}` | CPU state |
| `cpu_package_energy_joules_total`, `gpu_energy_joules_total` | counters: `rate()` = watts |
| `gpu_frequency_mhz`, `gpu_rc6_residency_seconds_total` | GPU clocks and time spent in its deep-idle state (`rate()` near 1 = fully idle) |
| `host_memory_bytes{kind}`, `host_pressure_stall_seconds_total{resource,kind}`, `host_uptime_seconds` | memory and pressure (PSI) |
| `zfs_arc_bytes{kind}`, `zfs_arc_hits_total`, `zfs_arc_misses_total`, `zfs_pool_online{pool,state}` | ZFS cache and pool state |

Already covered elsewhere on this server (so not duplicated): `smartctl-exporter` (SMART per drive), `intel-gpu-exporter` (GPU engine usage), Scrutiny, `alloy-hetzner`.

Useful PromQL:
- CPU package watts: `rate(cpu_package_energy_joules_total[5m])`
- GPU watts: `rate(gpu_energy_joules_total[5m])`
- GPU idle fraction: `rate(gpu_rc6_residency_seconds_total[5m])`
- ARC hit ratio: `rate(zfs_arc_hits_total[5m]) / (rate(zfs_arc_hits_total[5m]) + rate(zfs_arc_misses_total[5m]))`
- Alert: `fan_speed_rpm{fan="1"} < 300 and fan_mode == 2` (CPU fan stopped), `fangui_guard_tripped == 1`, `zfs_pool_online == 0`, `fangui_driver_loaded == 0`, `fan_manual_active == 1` for longer than a day.

Power observed on the first scrape: the Arc A310 averaged about 15.6 W since boot and the CPU package about 11 W. The GPU is not idle: `intel_gpu_top` (via the existing `intel-gpu-exporter`) shows Frigate's `ffmpeg`, `frigate.detecto` and a `python3` process attached, with the Video engine about 1% busy and RC6 (deep idle) around 80%. PCIe ASPM is disabled on the card; enabling it in the BIOS and watching `gpu_watts` and `gpu_rc6_percent` is a possible power saving, not a fault.

Added in v2: `cpu_package_watts`, `gpu_watts` (live), `gpu_top_power_watts`, `gpu_engine_busy_percent{engine}`, `gpu_client_busy_percent{name}`, `gpu_rc6_percent`, `gpu_vram_used_bytes`, `hw_temperature_celsius{sensor="dimm"}`, `fangui_smart_*`, `fangui_profile_running`, `fan_profile_rpm{fan,duty}`. The GPU data is read from the existing exporter at `127.0.0.1:7071`, which is why the container now runs with `--network host` (port 18765 unchanged).

## 4c. Fan profiler and smart mode (v2)

### What the research says (fan-curve practice)
- Hysteresis, response time and averaging are three separate levers: hysteresis changes what it takes to *reverse* a decision, delay changes how long a condition must persist, averaging smooths the input signal. Typical hysteresis is 3-5 °C. Longer step-down than step-up delay gives quiet, stable fans; keep the top of the curve aggressive. One shaped curve driven by the hottest relevant sensor beats tuning each fan separately. Change one thing at a time and test idle and load. ([fan hysteresis guide](https://shop.zimaspace.com/blogs/tech-ai-hub/fan-hysteresis-reduce-fan-noise-home-server), [Igor's Lab fan-control tutorial](https://igorslab.de/en/fan-control-correct-use-and-set-basics-curves-and-tools-tutorial/5))
- Linux `fancontrol`/`pwmconfig` measure the duty at which a fan starts (MINSTART) and keeps spinning (MINSTOP), and a fan may need a kick to start but run at a lower duty. Some boards smooth the fan response over about a minute, so settling time must be measured, not assumed. ([fancontrol man page](https://www.mankier.com/8/fancontrol), [pwmconfig](https://www.mankier.com/8/pwmconfig))
- Drive temperature: sources disagree on an ideal range (roughly 25-45 °C), but agree that staying within the manufacturer's rated limit matters more than chasing the coldest value, and that over-cooling is not beneficial. The drive guard here is 48 °C, the smart target 40 °C. ([HDD Sentinel](https://hdsentinel.com/blog/safe-hard-drive-temp), [Google study summary](https://www.networkworld.com/article/827250/data-center-google-research-cranking-up-the-heat-may-not-harm-your-disk-drives.html))
- I did not find an authoritative source on calibration math, so the profiler is deliberately simple.

### How v2 applies it
- **Profiler** (button per fan): steps the fan through duties that were already tested on this machine (fan1 120-250, fan2 63-250), waits for the RPM to settle (stable within 3% over 8 s, 12-45 s per step), records RPM, and returns the fan to automatic. It aborts and restores on: cancel, thermal guard, CPU >= 60 °C (fan1) / drive >= 44 °C (fan2), RPM < 200, or driver loss. A 10-minute override timer is the backstop. Results are saved to `data/profile_N.json` and shown with an estimated noise difference from the fan laws (about 50·log10 of the speed ratio; an estimate, not a measurement).
- **Smart mode** (checkbox + target per fan, off by default): holds a steady temperature instead of following a curve.
  - Input is a smoothed temperature (EMA: CPU about 20 s, drives about 90 s). Fan1 follows CPU temperature (default target 58 °C, deadband ±3), fan2 follows the hottest drive (target 40 °C, deadband ±2).
  - Inside the deadband the duty does not change. Above it, the duty rises by 3 + 3 per degree over (max 15) every 20-30 s, and +30 immediately if more than 5 degrees over. Below it, the duty falls only 2 steps per 2-3 minutes. This is the "fast up, slow down, hold in between" shape the research recommends.
  - Duty bounds: fan1 120-250, fan2 63-250 (the lowest values tested). A duty that has not changed for 10 minutes is remembered and used as the starting point next time ("auto calibrate" by settling, not by a separate test).
  - Pauses while the thermal guard is tripped or the profiler runs, and re-takes control afterwards. Setting manual duty or pressing Automatic turns it off. A stalled fan (under 250 RPM for 6 s while we control it) returns to automatic and turns smart mode off.

### Measured on this server (13:19, drives 40 °C, CPU about 44 °C)
fan2 profile (duty -> RPM, noise vs max): 63 -> 615 (-23 dB), 80 -> 751 (-19), 100 -> 911 (-14), 120 -> 1053 (-11), 150 -> 1246 (-8), 180 -> 1424 (-5), 220 -> 1632 (-2), 250 -> 1772 (0). Linear, about 6 RPM per duty step, no stall at 63, settles in 12-20 s. Smart mode held the case fan at a flat 909 RPM for 72 s with the drives on target; turning it off restored automatic and repaired `auto_start` (the driver moves it from 63 to the manual duty).

Not yet done: fan1 (CPU) profile and a long smart-mode soak under load. The case fan has no "stop" test because it is never taken below 63.

### Limits worth knowing
- The drive sensor has 1 °C resolution, so the smoothed value moves in small steps; the deadband (±2) absorbs that.
- Smart mode cannot lower the CPU fan far: its lowest tested duty (120, about 1490 RPM) is close to what the BIOS already runs, so the gain there is stability, not silence.
- If the app is killed without a clean stop, the fans stay at their last duty until the container restarts (seconds), then it returns them to automatic.

## 4d. Grafana dashboard

`monitoring/grafana/` holds four generated Grafana dashboards (flat blueprint and isometric, each in dark and light) with a Canvas panel of the Jonsbo N4 (animated fans chosen by RPM through value-mapping icons, live temperatures, power, ZFS state) plus seven chart panels. The artwork in `assets/` came from a separate illustration agent working from written briefs (two rounds); it was audited (no scripts/images/text/external refs) before use. Round 2 added layered isometric drawing, a geometry manifest, heat glows, airflow, tray LEDs, motion-blur fans and light variants; the isometric dashboard uses all of those except the PSU glow. Details, import steps and the animation trick: `monitoring/grafana/README.md`. Built after reading Grafana's canvas source: the built-in wind-turbine element can spin from data but draws a tower through the rotor, and zoom-to-fit is behind a feature flag, so the case uses percentage positioning and SVG fans.

## 4e. All-in-one stack (shareable)

`docker-compose.yml` in the repo root bundles fan-gui, node-exporter, optional intel-gpu-exporter, Prometheus and Grafana (datasource and the four dashboards provisioned), so others can `docker compose up` and import nothing by hand. Design decisions:
- **Monitor-only is the default** (`FAN_CONTROL=0`): the app refuses every write (POST endpoints return 400, no startup cleanup, no snapshot, smart mode forced off) and runs unprivileged. Control is opt-in through `docker-compose.control.yml`. This also made it possible to test the whole stack on the author's server next to the running controller without touching the fans.
- **RAPL needs privileged.** Docker masks `/sys/devices/virtual/powercap`; `systempaths=unconfined` unmasks it but the file still returns "Permission denied" without privileged (tested). CPU power is therefore n/a in the default stack; `docker-compose.power.yml` makes the container privileged for that.
- fan-gui reaches the GPU exporter by service name, so it no longer needs the host network. node-exporter does (real NIC metrics) and Prometheus reaches it via `host.docker.internal`.
- Prometheus reads the fan-gui password from a file written by the container start command from `FAN_GUI_PASSWORD` (works on any Compose version, no secrets file for the user to create).
- Dashboards fall back to **n/a** for any missing metric (`or on() vector(NaN)` plus a special value mapping).
- Pinned images: Prometheus v3.16.0, node-exporter v1.12.1, Grafana 13.3.1, intel-gpu-exporter 1.0.1.
- Verified on the server (separate project, different ports): all services healthy, both Prometheus targets up, datasource and four dashboards provisioned, isometric view is Grafana's home dashboard, a write attempt is refused, the node panels and tray LEDs get data, and the dashboards degrade to n/a with the GPU exporter stopped. The test project and its volumes were removed afterwards.
- Added `coretemp` (Intel) next to `k10temp` for the CPU temperature, and `FAN_HWMON_PREFIX` for other fan chips (monitoring only).

## 4f. Demo mode and the README animation

`docker-compose.demo.yml` runs `demo/demo_exporter.py`: synthetic metrics with the same names and labels as fan-gui and node-exporter, driven by a scripted cycle (idle, load, cool-down; 40 s by default, wall-clock based so every viewer sees the same point). It exists because the real server never gets hot enough to show the amber and red states, and because it lets anyone try the dashboards without hardware. The demo Grafana has anonymous viewer access, listens on localhost, uses 2 s refresh copies of the two dark dashboards (`monitoring/grafana/demo/`) and a datasource `timeInterval` of 1 s so `$__rate_interval` is short and the drive LEDs react quickly.

How `docs/dashboard-demo.gif` was made (headless Chromium via Playwright): demo cycle set to 20 s, page opened with `refresh=1s`, capture aligned to the start of a cycle using the server's HTTP `Date` header, lossless PNG screenshots of the canvas panel at about 11 fps, resampled to 10 fps and assembled with Pillow into a 64-colour 640 px GIF (about 8 MB). Lossless frames matter: JPEG noise makes every pixel differ and the GIF balloons to 15-19 MB.

Gotchas found on the way:
- **A dashboard refresh value that is not in `timepicker.refresh_intervals` is silently ignored** (a `2s` refresh did nothing until the list contained it). The generator now writes `1s ... 15m` into every dashboard.
- Auto-refresh does not run in single-panel view mode (`viewPanel=`) or when the time controls are hidden; use the full dashboard and clip the panel.
- Grafana's `min_refresh_interval` defaults to 5 s; the demo sets 1 s.

## 4g. Artwork for other cases (prompt template, audit, generator)

`docs/ARTWORK_PROMPT_TEMPLATE.md` is a fill-in brief for an illustration agent (three tiers: starter, full, flat blueprint, plus a round-2 follow-up), distilled from the two briefs that produced the Jonsbo N4 set. `tools/audit_artwork.py` checks an artwork folder (XML validity, the literal `xmlns`, no script/foreignObject/image/text/external references/event handlers, the single `dur='1.2s'` per spin/blur fan and none in the stopped fan, viewBoxes, size budgets, manifest badges inside the canvas, tray numbering) and reports which of the four dashboard views it can build; it never renders or runs anything. It passes the repo's own set and catches a deliberately broken folder (script, text, image, external href, bad xmlns, missing dur, out-of-bounds badge).

`build_dashboard.py` was generalised so it does not assume the N4: `assets_dir`, `labels`, `pools` and `drives` in `config.json`; every component, fan, layer, glow, LED, airflow overlay and icon is optional; with no `iso_*` layers it falls back to the composite `case_upper_side.svg`; views whose backdrop is missing are skipped with a message; info cards use manifest slots `free_1..3` (legacy fallback `wood_panel`, `mesh_top`, `dimm_1`). Checks: output for the repo's own assets is identical to the previous generator apart from the pool field names, and a deliberately reduced set (one fan, two drives, composite art, one pool, custom labels) rendered correctly in Grafana 13.3.1 against the demo data (unknown pool shows n/a, no console errors).

## 5. Handy commands (run as root on the server)

```bash
H=$(for h in /sys/class/hwmon/hwmon*; do [ "$(cat $h/name)" = it8655 ] && echo $h; done)
cat $H/fan1_input $H/fan2_input                      # RPM
cat $H/pwm1_enable $H/pwm2_enable                    # want: 2 2
echo 2 > $H/pwm1_enable; echo 2 > $H/pwm2_enable     # emergency: back to BIOS curve
docker stop fan-gui                                  # also restores automatic
rmmod it87                                           # unload driver
```

## 6. What is on the server (clean-up list)

On the author's server everything lives in one folder on the data pool, `/mnt/<pool>/fan-control/`: `it87-out/it87.ko`, `fan-gui/` (with `env.list`, `data/`). Images: `local/it87-build`, `local/fan-gui`. TrueNAS init scripts added: id 3 (boost off), id 4 (load it87). Leftovers from the earlier Arc firmware investigation (`cand/`, `Dockerfile.igsc`, image `local/intel-igsc`, `Dockerfile.it87` copies) can be deleted.

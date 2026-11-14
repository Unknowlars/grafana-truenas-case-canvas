# Artwork

SVG assets for the Canvas dashboards, produced by a separate illustration agent from written briefs (two rounds). The current set is round 2. `manifest.json` holds the projected geometry (polygons, anchors, badge slots, fan matrices) for the 1100 x 742 isometric drawing.

Audit before use (both rounds): no scripts, `foreignObject`, images, `<text>`, external references or event handlers in any SVG; literal `xmlns='http://www.w3.org/2000/svg'` and a `viewBox` present; the files were rendered as CSS background images (how Grafana uses them) in headless Chromium and checked for animation. The agent's own preview page (it contains a script) was not run and is not kept. Total: 73 SVGs, about 123 KB.

| Group | Files | Used by |
|---|---|---|
| Isometric layers (1100 x 742, transparent except the base) | `iso_00_base`, `iso_10_components`, `iso_20_trays_empty`, `iso_25_drives`, `iso_30_front_panel`, `iso_40_mesh_foreground` | isometric dark view (stacked in this z-order) |
| Isometric composites | `case_upper_side.svg`, `case_upper_side_light.svg` | `case_upper_side_light.svg` is the light isometric backdrop; the dark composite is not used (the layers are) |
| Isometric fans | `iso_fan1_*`, `iso_fan2_*` (`spin`, `blur`, `stopped`) | both isometric views; `dur='1.2s'` is rewritten per RPM step |
| Flat view | `case_background.svg`, `case_background_light.svg`, `fan_blue_*`, `fan_teal_*` (`spin`, `blur`, `stopped`) | flat views |
| Heat glows | `glow_<component>_<ok\|warm\|hot>.svg` for cpu_cooler, gpu, nvme, dimm_bank, psu, tray_1..4 | isometric views; `ok` and the PSU glows are not used (no telemetry) |
| Airflow | `iso_airflow_cpu_on/off.svg`, `iso_airflow_case_on/off.svg` | isometric views |
| Tray LEDs | `iso_led_tray_1..4_on/off.svg` | isometric views: `on` when the disk was more than 1 % busy (node-exporter), `off` otherwise |
| Icons | `icon_*.svg` (24 x 24) | badges; `#8791a3` is rewritten to the theme's muted colour |

Rules to keep when replacing or adding artwork (learned from Grafana's canvas code): keep the literal `http://` in `xmlns`; no scripts, external references or `<text>`; animation by SMIL or CSS only; keep `viewBox="0 0 1100 742"` for full-case drawings so the percentage overlays line up; keep a single `dur='1.2s'` on the fan's `animateTransform` (the generator replaces exactly that string); keep the tray numbering and the manifest in step with the drawing.

The drawings are schematic, not exact industrial models. Check the licence/provenance of any artwork you add yourself.

**Using artwork for another case:** keep this folder as the Jonsbo N4 example, put yours in a new folder (for example `assets-mycase/`) and set `"assets_dir"` in `monitoring/grafana/config.json`. The prompt template is `docs/ARTWORK_PROMPT_TEMPLATE.md`; `python3 tools/audit_artwork.py <folder>` checks a set. The three information cards use manifest slots `free_1` (150 x 36), `free_2` and `free_3` (150 x 78); older sets without them fall back to the slots named `wood_panel`, `mesh_top` and `dimm_1`, which is what this folder uses.

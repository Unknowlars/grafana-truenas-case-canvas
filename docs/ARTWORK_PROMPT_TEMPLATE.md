# Make the pictures for your own case

The dashboards in this repo draw a **Jonsbo N4**. If you have a different case, you can have an AI illustration agent draw yours and plug it in: the data side (fans, temperatures, drives, ZFS) does not change, only the artwork does.

This page is a **prompt template** for that agent, plus the steps to check and install what it gives you. It is the same approach that produced the Jonsbo N4 artwork in `monitoring/grafana/assets/` (use that folder as a worked example of what a finished set looks like).

**What you need:** an AI assistant that can write and test SVG files and a preview web page (a coding assistant or an assistant with a code sandbox), and 3 to 4 photos of your case.

## The four steps

1. **Fill in the sheet** below (5 minutes) and paste your photos.
2. **Give the agent the prompt** for the tier you want, with your sheet values filled in.
3. **Audit what comes back:** `python3 tools/audit_artwork.py <folder>` (no scripts, text or external references; files meet the dashboard's rules).
4. **Install it:** put the folder next to the Jonsbo one, point `config.json` at it, run the generator, look at the result in the demo.

## Which tier?

| Tier | What the agent draws | You get | Effort |
|---|---|---|---|
| **1. Starter** | one isometric drawing of your case (without fans), two fan overlays, a small manifest | the isometric dashboard with animated fans and value badges | one conversation |
| **2. Full** | tier 1 plus separate layers (so fans sit *behind* the front panel), heat glows, airflow, drive LEDs, icons, motion-blur fans, a light variant | everything in the Jonsbo dashboards | two or three rounds |
| **3. Flat blueprint** | a schematic front view with empty boxes at fixed positions | the flat dashboards; works for almost any case because it is only a schematic | one conversation |

Start with tier 1 and add tier 2 in a second round (a follow-up prompt for that is at the end). You can build only the views your artwork supports: the generator skips the rest and says why.

## Step 1: the fill-in sheet

Write these down first. They go into the prompt where you see `{{DOUBLE_BRACES}}`.

| Placeholder | What to write | Example (my case) |
|---|---|---|
| `{{CASE_MODEL}}` | make and model | Jonsbo N4 |
| `{{PHOTOS}}` | "attached: front, top, three-quarter, inside" (attach 3 or 4 photos from different angles, or links) | front, top, 3/4, inside |
| `{{CASE_STYLE}}` | materials and look | black steel open frame, wood-grain front panel, mesh sides |
| `{{LAYOUT}}` | where things are, in plain words | motherboard compartment on top, six drive trays in the lower front in two columns of three, PSU lower right |
| `{{BOARD}}` | form factor | micro-ATX |
| `{{CPU_COOLER}}` | type and size | low-profile air cooler with a 120 mm fan on top |
| `{{FAN1}}` | the fan you control first (usually the CPU fan): position, size, type | CPU cooler fan, 120 mm, top of the cooler, 4-pin PWM |
| `{{FAN2}}` | the second fan (or "none") | case fan on the right side blowing across the drives, 120 mm, 3-pin |
| `{{GPU}}` | yes / no, size, where | low-profile card in the PCIe slot, right of the cooler |
| `{{NVME}}` | yes / no, where | M.2 slot below the CPU |
| `{{RAM}}` | number of sticks and orientation | 4 sticks, standing upright, right of the cooler |
| `{{PSU}}` | type and where | SFX, lower right |
| `{{TRAYS}}` | number of drive bays, arrangement, **numbering order** and which are populated | 6 trays; tray 1-3 left column top to bottom, 4-6 right column; 1 and 4 are 2.5" SSDs, 2 and 3 are 3.5" HDDs, 5 and 6 empty |
| `{{FREE_SPACE}}` | which empty areas around the picture can hold information cards | top-left, top-right, lower-left |
| `{{PALETTE}}` | colours to match the case (the dashboard background is near-black `#0f1319`) | steel greys, wood `#8b4f2e`, accents blue `#4da3ff` and teal `#2dd4bf` |

Limits of the dashboard: **two controllable fans** (fan 1 blue, fan 2 teal), up to **four** drive badges (more trays can be drawn, but the dashboard shows the first four you list), one GPU, one NVMe, a memory bank, a PSU. If your case has fewer parts, say so; the generator skips whatever the artwork does not have.

## Step 2: the prompt (tier 1, Starter)

Copy the whole block, replace every `{{...}}`, and attach your photos.

````text
You are a technical illustrator and SVG animator. Produce SVG files for a Grafana dashboard that shows a home server inside
{{CASE_MODEL}} as a live, animated picture. Another tool places live numbers (temperatures, fan RPM, power) on top of your
drawing, so you only draw the case and its parts: NEVER draw text, numbers, legends or labels.

## The machine (draw it faithfully)
Reference photos: {{PHOTOS}}. Study them and match proportions, materials and details.
Case style: {{CASE_STYLE}}.
Layout: {{LAYOUT}}.
Motherboard: {{BOARD}}. CPU cooler: {{CPU_COOLER}}.
Fan 1: {{FAN1}}.
Fan 2: {{FAN2}}.
GPU: {{GPU}}. NVMe: {{NVME}}. Memory: {{RAM}}. Power supply: {{PSU}}.
Drive trays: {{TRAYS}}.
Palette: {{PALETTE}}. Flat shading with soft depth, clean schematic style (not photorealistic). Dark-UI friendly.

## Canvas
Every full-picture file uses viewBox="0 0 1100 742" (units are px). Draw the case as an isometric view (about 30 degrees, seen
from the front-left and above) filling the middle of the canvas, leaving margins for information cards:
at least 150 px free on the left, 90 px on the top and 150 px on the right. Free areas for cards: {{FREE_SPACE}}.

## Files to deliver (exact names)
1. case_upper_side.svg   - the whole case WITHOUT the two fans (the fans are separate files). Opaque background #0f1319.
2. iso_fan1_spin.svg, iso_fan1_stopped.svg, iso_fan1_blur.svg   - fan 1 only, viewBox 0 0 1100 742, transparent, drawn in the same
   isometric projection and at exactly the place it sits on the case, so it overlays case_upper_side.svg perfectly.
   Accent colour blue #4da3ff (rim ring and blade edges).
3. iso_fan2_spin.svg, iso_fan2_stopped.svg, iso_fan2_blur.svg   - the same for fan 2, accent teal #2dd4bf. (Skip if the case has one fan.)
4. manifest.json  - geometry of every part, see "Manifest" below.
5. preview.html   - see "Verification".

### Fan files in detail
- spin: the rotor (blades + hub) is one <g> that rotates 360 degrees about the fan's centre with a single
  <animateTransform attributeName='transform' type='rotate' from='0 CX CY' to='360 CX CY' dur='1.2s' calcMode='linear'
  repeatCount='indefinite'/> where CX CY is the rotor centre in that file's coordinates (if the fan sits inside a matrix transform,
  put the animateTransform on the rotor group inside it and use local coordinates). The string dur='1.2s' (single quotes) must
  appear EXACTLY ONCE in the file: a script rewrites it to change the speed. Frame, screws, ring and hub highlight are static.
  Use about 9 curved blades. Nothing else may animate.
- stopped: the same picture, completely static (no animation elements at all), hub colour red #ef4444.
- blur: for fast speeds. Same frame; the rotor is drawn as a translucent disc plus 3 ghost blade sets at slight angular offsets
  with lower opacity, rotating with the same single dur='1.2s' animateTransform (once per file).

## Manifest
manifest.json describes every part in the 1100 x 742 drawing so that value cards and leader lines can be placed exactly:
{
  "viewBox": [0, 0, 1100, 742],
  "components": {
    "cpu_cooler": {
      "polygon": [[x, y], ...],           // outline of the part as seen in the drawing
      "anchor": [x, y],                   // a point on the part
      "badge": {"x": 162, "y": 48, "w": 150, "h": 36, "arrow_tip": [x, y]}   // a free 150x36 rectangle for its value card, and
    },                                                                         // the point on the part its leader line ends on
    ...
  }
}
Required components (use exactly these ids): cpu_cooler, cpu_fan (fan 1), case_fan (fan 2, if any), gpu, nvme, dimm_bank, psu,
tray_1 ... tray_N (numbered without gaps, in the order given in the tray description above).
Three extra entries for information cards: free_1 (rectangle 150x36), free_2 and free_3 (rectangles 150x78). For these only badge.x and
badge.y matter, but give a full badge object. Skip a part the case does not have.
Rules for badges: every badge rectangle must lie completely inside the canvas, completely outside the case silhouette, must not
overlap any other badge, and its arrow_tip must lie on the part. Spread them around the picture so leader lines do not cross much.

## HARD technical rules for EVERY svg file
- Pure SVG. No <script>, <foreignObject>, <image>, <text>, <tspan>, external references (href/xlink:href must be local '#id' only), @import,
  web fonts, event attributes or javascript: urls. Convert any text-like shape to plain paths, or leave it out.
- The root element carries the LITERAL attribute xmlns='http://www.w3.org/2000/svg' (keep the http://, do not omit or encode it)
  and a viewBox. No width/height in px.
- Use single quotes inside attributes. Hex colours only (no currentColor, no CSS variables).
- Animation only with SMIL or CSS keyframes inside the file. It must run when the file is used as an <img> or as a CSS
  background-image: that is how it will be used. Test exactly that way, not by opening the SVG directly.
- Reuse shapes with <defs>/<use> and declare xmlns:xlink and write BOTH href and xlink:href on <use>.
- Size: each fan file under 5 KB, case_upper_side.svg under 40 KB, everything together under 250 KB.
- Do not invent data. Do not add titles, legends or numbers.

## Verification (required)
1. Build preview.html that shows every file at exactly 1100 x 742 stacked in the right order, each SVG used as a CSS
   background-image on a div (not inline <svg>), with buttons to switch each fan between spin, blur and stopped and a toggle that draws
   the manifest polygons, anchors, badges and leader lines on top so I can see they sit on the parts.
2. Confirm every animation runs when used as a background-image, that there are no console errors and no network requests.
3. Check yourself against the hard rules above (search the files for <script, <text, <image, href=http, on... attributes).
4. Report each file's size in KB and anything you could not do or approximated. Deliver the files individually and as one zip.
If two requirements conflict, say which one you broke and why instead of silently choosing.
````

## Step 2b: the prompt for tier 3 (Flat blueprint)

Use this instead (or in addition). It is easier, because it is a schematic with fixed positions, so it works for nearly any case.

````text
You are a technical illustrator. Draw a flat schematic front/cutaway view of {{CASE_MODEL}} as one SVG, for a Grafana dashboard that
overlays live values on top. Reference photos: {{PHOTOS}}. Style: {{CASE_STYLE}}; palette {{PALETTE}}; dark UI friendly.

File: case_background.svg, viewBox 0 0 1100 742. NO text of any kind (live labels are added by another tool).
Draw the real case around EMPTY, flat-coloured rectangles at exactly these places (x, y, width, height in the 1100 x 742 space).
The placeholders are only where information will be shown; adapt what is drawn around them to the real case.
 case outline 10,10,1080,722 | top chamber 30,64,1040,300 | front panel strip 30,374,1040,40 | bottom chamber 30,424,1040,296 |
 motherboard 44,90,722,264 | CPU cooler body 322,102,262,250 (fan area 352,124,200,200 stays empty) |
 four memory sticks at x=604,638,672,706 y=102, each 26x196 | NVMe card 58,222,252,46 | GPU card 58,276,252,70 |
 CPU card 58,102,252,112 | system card 782,90,274,264 | drive bays at (50,452) (50,534) (50,616) (320,452) (320,534) (320,616),
 each 260x74 | case fan area 610,470,180,180 (stays empty) | ZFS card 830,452,226,138 | PSU 830,604,226,100.
If your case has no such part, draw something plausible but keep the placeholder area empty or dim.
Also deliver case_background_light.svg: the same geometry for a light page background.

Then deliver the fan files for the flat view: fan_blue_spin.svg, fan_blue_stopped.svg, fan_blue_blur.svg and fan_teal_spin.svg,
fan_teal_stopped.svg, fan_teal_blur.svg: square, viewBox 0 0 120 120, transparent, rotor centre at 60,60, 9 curved blades, frame with four
corner screws, accent ring (blue #4da3ff / teal #2dd4bf); animation, stopped and blur rules exactly as in the isometric prompt
(one dur='1.2s' per spin/blur file, nothing else animated, stopped has no animation and a red #ef4444 hub).

Apply the same HARD technical rules and verification as before: pure SVG, literal xmlns='http://www.w3.org/2000/svg', no script/text/image/
external references, single quotes, test as CSS background-image, report sizes.
````

## Step 3: audit what you got

Unzip into a new empty folder (treat downloaded files as untrusted; do not open the agent's `preview.html` unless you have read it) and run:

```bash
python3 tools/audit_artwork.py ~/my-case-artwork
```

It prints which dashboard views the artwork can build, which parts it found, and any **errors** (scripts, text, images, external references, a broken `xmlns`, a missing or duplicated `dur='1.2s'`, a manifest badge outside the canvas) or **warnings** (large files). Fix errors by sending the message back to the agent, for example: "`fan_blue_spin.svg: must contain exactly one dur='1.2s'; found 0`".

Common problems and what to tell the agent:

| Problem | Say this |
|---|---|
| Text or labels in the drawing | "Remove all `<text>`; convert to paths or leave it out. Live labels are added elsewhere." |
| Fan does not spin when used as an image | "Test it as a CSS background-image. SMIL `animateTransform` inside the file works there; JavaScript does not." |
| Fans not on the case | "The fan overlay must use the same projection and position as the drawing; overlay it on `case_upper_side.svg` in preview.html and show me." |
| Badges overlap the drawing | "Move every badge rectangle fully outside the case silhouette and re-run the overlap check." |
| Files too big | "Reuse blades with `<defs>`/`<use>` and drop decorative detail; keep each fan under 5 KB." |
| It used an `<image>` with a photo | "No raster images. Redraw as vector shapes." |

## Step 4: install and look

1. Put the files in a new folder next to the Jonsbo set, for example `monitoring/grafana/assets-mycase/`.
2. Copy `monitoring/grafana/config.example.json` to `config.json` and edit it: `"assets_dir": "assets-mycase"`, your drives, ZFS pool names (up to three) and the labels (case name, CPU, GPU).
3. Generate: `python3 monitoring/grafana/build_dashboard.py`. It prints which views it built and which it skipped and why.
4. Look at it with moving data, no hardware needed: `docker compose -f docker-compose.demo.yml up -d`, open <http://localhost:3000>, and watch your case run through the idle, load, cool-down cycle. For your real data use the normal stack (`docker compose restart grafana` after regenerating).
5. Adjust: tell the agent what looks off (badge positions, fan alignment) and regenerate. The generator is deterministic, so a fix is one more round.

## Follow-up prompt: tier 2 (add layers, glows, airflow, LEDs, light variant)

Use this in the same conversation once tier 1 works.

````text
Round 2: make the artwork more realistic and more informative. Keep every technical rule from round 1. Deliver these extra files, all
viewBox 0 0 1100 742 and transparent unless stated, aligned to the same drawing and manifest:

1. Layers instead of the single drawing, in this z-order (each a separate file): iso_00_base.svg (chassis, shelf, motherboard; opaque
   background #0f1319), iso_10_components.svg (cooler body, memory, GPU, NVMe, PSU), iso_20_trays_empty.svg (empty tray mouths),
   iso_25_drives.svg (drive bodies in the populated trays), iso_30_front_panel.svg, iso_40_mesh_foreground.svg (frame rails and perforated
   panels in front of everything). Fans stay separate overlays and sit between iso_10 and iso_20, so layers 20-40 hide parts of the fans
   like the real case does. Keep case_upper_side.svg as the combined version of the same layers (without fans).
2. Heat glows, one file per component and state: glow_<component>_ok.svg, _warm.svg, _hot.svg for cpu_cooler, gpu, nvme, dimm_bank, psu
   and tray_1..tray_N (the populated trays). A soft glow clipped to the component's polygon from the manifest, pulsing with
   <animate attributeName='opacity' values='.25;.85;.25'> with periods 3.0 s (ok, green #34d399), 1.8 s (warm, amber #fbbf24),
   0.9 s (hot, red #f87171).
3. Airflow: iso_airflow_cpu_on.svg and iso_airflow_case_on.svg: animated streamlines in the isometric projection (a few dots moving along curves
   with <animateMotion>, fading at the ends), blue for the CPU fan, teal for the case fan; plus _off.svg static dim versions.
4. Drive LEDs: iso_led_tray_N_on.svg and iso_led_tray_N_off.svg for each populated tray, a tiny LED at the tray's activity light position.
5. Icons for the cards, viewBox 0 0 24 24, flat, stroke or fill #8791a3 only: icon_cpu, icon_gpu, icon_ram, icon_nvme, icon_hdd, icon_ssd,
   icon_fan, icon_bolt, icon_thermometer, icon_ok, icon_warning, icon_critical.
6. Light variants: case_upper_side_light.svg (and case_background_light.svg if you drew the flat view): same geometry and the same
   manifest, light page background.
7. Update preview.html: layer toggles, a glow-state switch per component, airflow on/off, fan speed (slow, medium, fast, blur, stopped),
   dark/light. Re-run all checks and report sizes (everything under 250 KB in total).
````

## Notes

- **Licence and provenance:** you are responsible for the artwork you add. Check what your AI tool's terms allow.
- **Photos of other people's property or private spaces:** crop them to the case before sharing them with a service.
- **Not just AI:** a designer can deliver the same files. The audit script does not care who drew them.
- **If something is off in the generator**, the contract in this page is the source of truth: file names, component ids, the single `dur='1.2s'`, the 1100 x 742 viewBox. `build_dashboard.py` documents the rest at its top.

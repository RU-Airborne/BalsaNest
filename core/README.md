# BalsaNest

## Current features

- takes **SVG, DXF, or PDF** part drawings exported at physical scale
  (SolidWorks can export DXF/PDF directly; PDF text watermarks are dropped);
- reads paths plus common SVG primitives (`rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`) through `svgpathtools`;
- flattens SVG transforms before nesting;
- duplicates each part by quantity;
- supports grain constraints:
  - `parallel`
  - `perpendicular`
  - `free`
  - or explicit allowed rotation angles in JSON;
- **flips/mirrors parts** (grain-preserving) so cambered airfoils and other
  asymmetric parts can interlock flat-edge-to-flat-edge;
- uses actual polygonal geometry rather than only rectangular bounding boxes;
- **no-fit-polygon (NFP) contact placement**: candidate positions are generated
  along each placed part's exact spacing-inflated silhouette (Minkowski sums of
  convex decompositions), so parts hug curved and slanted neighbours at true
  contact — discs nestle hexagonally, hypotenuses mate — instead of only
  aligning to bounding-box edges;
- **bottom-left compaction** slides each part into the tightest reachable spot,
  including the concave pocket of a flipped neighbour;
- **nests small parts inside larger parts' scrap cut-outs** (e.g. aileron ribs
  inside a bulkhead's lightening window), validated to sit clear of the material —
  and a placement in scrap always wins over open sheet space, since scrap costs
  nothing;
- **packs into concave pockets** (an arch-shaped bulkhead cutaway, a C-channel
  opening) that plain bounding-box packing never looks inside;
- **stitches segment-soup outlines**: SolidWorks/Inkscape exports that draw one
  outline as dozens of disconnected segments are chained back into closed
  contours, so hole detection, labels and collision stay correct;
- **minimises the used footprint** so the rest of the sheet stays as one large
  usable off-cut;
- enforces edge margin and minimum part-to-part spacing;
- **catches jobs that will not fit** up front (a part too big for the sheet, or a
  quantity that exceeds an optional sheet cap) with a clear message;
- runs several greedy packing passes with adaptive early-stopping and keeps the
  best result — or an optional **genetic algorithm** that evolves the insertion
  order live until stopped;
- supports **non-rectangular stock**: a polygon sheet outline (uploaded as
  SVG/DXF/PDF or painted by hand in the web UI) with interior holes treated as
  blocked areas;
- optional **common-line merging**: with part spacing 0, an edge shared by two
  touching parts is cut once instead of twice;
- automatically creates additional stock sheets if everything cannot fit on one;
- writes physical-size SVG output on three layers:
  - **Cut** — no fill, full red `#ff0000`, `1 px` stroke;
  - **Raster labels** — each part's file-name engraved in black, horizontal, and
    aligned onto shared rows so the laser head travels less;
  - **Debug** (optional) — blue bounding boxes + sheet outline for inspection;
  - 96 px/in viewBox throughout;
- emits a JSON summary with placements, rotation/mirror state, footprint, nested
  parts and approximate utilization.

## Important limitation

The packing engine proposes candidate positions from **true no-fit polygons** (exact-contact geometry, always on). The placement *order* is a greedy multi-pass **heuristic** by default; an optional SVGnest-style **genetic algorithm** (`optimize_layout_ga`, selectable as the optimizer in the web UI) evolves the insertion order for tighter nests at the cost of much longer runtimes. Neither is a mathematically guaranteed globally optimal nest.

The output writer can optionally **merge common cut lines** (`"merge_common_cuts": true`, or the toggle in the web UI): with part spacing 0, an edge shared by two touching parts is cut once instead of twice. Only exactly coincident segments merge.

Importers: **SVG**, **DXF**, and **PDF** (SolidWorks can export DXF or PDF
directly, skipping the Inkscape conversion step entirely). DWG is a proprietary
binary format and is rejected with guidance to export DXF instead.

## Run the web UI

```bash
python webui.py
```

Opens `http://127.0.0.1:7860` in the browser (AUTOMATIC1111-style dark UI,
red accent). The page is four sections:

- **Visualizer** — an elevated canvas that always shows the sheet (empty at
  first, with browser-side inch rulers, 0,0 top-left). Pressing **Start**
  streams the layout live: the heuristic shows every pass, the genetic
  algorithm shows every generation under a pulsing EVOLVING banner until you
  press **Stop evolving** (or its generation cap is reached). A debug-overlay
  toggle (on by default) shows part bboxes, margins, scrap cut-outs and
  pockets, and can be flipped at any moment — even mid-run.
- **Output** — download the laser-ready SVGs + JSON summary (real hairline
  strokes; the visualizer uses thickened preview strokes).
- **Settings** — three columns: sheet (dimensions, grain direction with wood
  thumbnails, and a **custom sheet shape** sub-section where you upload an
  outline or paint one on a graph-paper canvas in a floating window), nesting
  (optimizer choice, passes/generations, mirroring, hole-nesting, partial
  results, advanced fine-tuning), and output/laser conventions (labels with a
  raster-vs-outline sample, colours, hairline / px / inch cut strokes,
  common-line merging).
- **Parts** — drag-and-drop SVG/DXF/PDF files; each becomes a card with a
  vector thumbnail, measured size in inches, quantity slider, grain alignment
  radio (with a picture legend), and a DXF unit override when needed.

Flags: `--port`, `--host` (use `0.0.0.0` to reach it from another machine),
`--share` for a temporary public Gradio link, `--no-browser`.
Windows: double-click `run_webui.bat`.

## Run interactively

```bash
python balsanest.py
```

The terminal asks for:

1. sheet width / X size;
2. sheet height / Y size;
3. sheet grain axis;
4. edge margin;
5. minimum part spacing;
6. optimization settings;
7. each SVG;
8. quantity;
9. grain rule;
10. source part grain/reference-axis angle;
11. output filename.

### Grain convention

BalsaNest needs to know how the grain direction you want on the **part** relates to the drawing.

By default, the part grain/reference axis is assumed to be horizontal in the source SVG:

```text
part reference axis ─────────────>  0°
```

If the sheet grain runs along X:

```text
sheet grain ─────────────────────>
```

then:

- `parallel` permits rotations 0° and 180°;
- `perpendicular` permits rotations 90° and 270°;
- `free` permits 0°, 90°, 180°, 270°.

If the desired grain axis in a particular CAD export is drawn at a different angle, enter that angle as `grain_angle_deg`.

## Run from a saved config

```bash
python balsanest.py --config examples/example_job.json
```

Example:

```json
{
  "sheet": {
    "width": 36.0,
    "height": 3.0,
    "grain_axis": "x",
    "margin": 0.05,
    "spacing": 0.04,
    "grid_step": 0.04,
    "passes": 8,
    "max_sheets": null,
    "allow_mirror": true,
    "allow_nesting_in_holes": true,
    "min_hole_area": 0.02
  },
  "sample_step": 0.015,
  "seed": 42,
  "output": "wing_ribs_laser.svg",
  "labels": { "enabled": true, "min_font_in": 0.06, "max_font_in": 0.5, "align_bands": true },
  "debug_borders": false,
  "parts": [
    {
      "file": "parts/rib.svg",
      "quantity": 8,
      "grain": "parallel",
      "grain_angle_deg": 0
    },
    {
      "file": "parts/gusset.svg",
      "quantity": 6,
      "grain": "free"
    }
  ]
}
```

### Sheet options

| key | default | meaning |
| --- | --- | --- |
| `max_sheets` | `null` | Hard cap on stock sheets. `null` = add sheets as needed. If a job needs more, it errors and lists the parts that did not fit. |
| `allow_mirror` | `true` | Allow mirrored/flipped orientations (grain is preserved). Doubles the useful orientations for asymmetric parts. |
| `allow_nesting_in_holes` | `true` | Allow small parts to be placed inside larger parts' scrap cut-outs. |
| `min_hole_area` | `0.02` | in²; ignore cut-outs smaller than this when hole-nesting. |
| `outline_file` | – | Optional non-rectangular stock: an SVG/DXF/PDF drawing whose largest closed contour becomes the sheet shape (its size overrides `width`/`height`; interior contours mark blocked holes/defects). In the web UI you can also upload this file or paint the shape directly on a canvas. |

### Machine defaults file

Settings that describe your machine and shop conventions — sheet size, laser
colours, stroke style, label mode — rarely change per job. Put them once in
**`balsanest_defaults.json`** (looked up in the job config's directory first,
then the current working directory) and every job config only needs to carry
what is specific to that job:

- job config values always **override** the defaults (deep-merged key by key);
- the interactive wizard uses the defaults as its suggested answers, so a run
  on the usual machine is just Enter-Enter-Enter;
- `parts` and `output` are ignored in the defaults file — it describes the
  machine, not a job.

The repository ships a `balsanest_defaults.json` listing **every supported
parameter at its built-in default value**, so it doubles as a reference: edit
the values you care about (e.g. your laser's `cut_color` / `cut_stroke`) and
delete nothing.

### CLI flags

```text
--output PATH        override the output path
--dry-run            load + validate geometry (and run capacity checks) only
--debug              add the blue debug bounding-box layer
--no-labels          skip the raster name labels
--no-mirror          disable mirrored orientations
--no-hole-nesting    disable nesting parts inside scrap cut-outs
--max-sheets N       cap the number of stock sheets
--allow-partial      write whatever fits instead of erroring on an over-full job
```

## Behaviours worth knowing

### Part-name raster labels

Every placed part is engraved with its source file name (without extension) in
**black** on the `Raster labels` layer. Key properties:

- **Always inside the material.** The label is anchored at the part's *pole of
  inaccessibility* (the interior point farthest from every edge and hole) and its
  size is grown only as far as the whole text block still passes an actual
  containment test. It can never spill past the outline, cross a cut-out, or run
  off the sheet — including on tapered airfoils and thin bulkhead frames.
- **Adaptive font**, sized to the *part*, not just the local material width.
- **Multi-line** wrapping when that yields a larger, legible font. Multi-word
  names break on word boundaries (`aileron_rib` → `aileron` / `rib`); a single
  word stays on one line unless it genuinely cannot fit.
- **Always horizontal** — a laser head rasters horizontally, so vertical text is
  much slower. Labels **slide vertically inside their part** to join nearby
  labels on a shared raster band (fewer slow vertical head moves), are pulled
  toward each other horizontally to shorten the sweep, and are **never banded
  with labels far across the sheet** (a shared band's raster lines sweep its
  whole x-span, so distant labels are cheaper on separate bands).
- Parts too small to hold a legible label are **left blank and reported** as a
  warning rather than mislabelled.

### Flipping / mirroring

With `allow_mirror` on, each part also gets mirrored orientations. Because the
reflection is across an axis aligned with the grain, the material grain
direction is preserved, so this is safe for `parallel` / `perpendicular` parts.
Airfoils then interlock with the flat/thin trailing edges together and the
curved noses tucked against each other. Mirror copies that merely reproduce a
plain rotation (symmetric parts) are dropped automatically.

### No-fit-polygon contact placement

For every (placed part, incoming part) pair, BalsaNest computes the **no-fit
polygon** — the region of positions where the incoming part would overlap the
placed one, built as Minkowski sums of convex decompositions of the two
outlines and inflated by the part spacing. The boundary of that region is the
locus of *exact contact at the minimum legal separation*. Candidate positions
are sampled along the boundary of `sheet interior − union of all NFPs`, so
every proposal either hugs a neighbour at exactly the spacing or rides the
sheet margin.

This finds the tight spots that bounding-box contact lines and bottom-left
sliding can never reach: a disc nestling into the valley between two discs
(hexagonal packing), slanted edges mating face-to-face, a part teleported into
an enclosure it could never *slide* into (fine — parts are cut, not slid). On
the airfoil example job this shortens the used strip from ~24.7 in to ~16.7 in
of stock.

NFPs are computed on slightly inflated vertex-light supersets of the outlines
(cached per orientation pair), so every proposed seed is guaranteed feasible
for the exact geometry; the exact collision test still confirms each one, and
the existing exact-geometry compaction does the final snugging. NFP contact
placement is always on; each part pair falls back to axis-aligned contact
lines if its NFP computation fails.

### Nesting inside scrap cut-outs

If a bulkhead or former has a large square/triangular through-cut in the middle,
that middle piece is scrap. BalsaNest will drop smaller parts (aileron ribs,
gussets, …) into that scrap area when they fit with full spacing to the cut
edge. The summary and terminal report every such nesting, e.g.

```text
-> nested aileron_rib_001 inside cut-out of bulkhead_001
   (verify those cut-outs are through-cut scrap, not engraved features, before cutting.)
```

A feasible scrap-hole placement always **wins outright** over open sheet space —
open sheet next to a part is usable stock, while a cut-out is waste either way.
Parts are packed **tightly inside holes** too: compacted into the hole's corner
and seeded against already-nested neighbours so they interlock instead of
floating mid-hole, leaving the rest of the cut-out as one usable scrap piece.
Nesting also **recurses**: a part placed into scrap that has its own cut-outs
offers those cut-outs to even smaller parts.

Multi-view exports are handled at load time: when a file contains several
identical disconnected copies of the same piece (a SolidWorks drawing with two
views), BalsaNest keeps **one copy** and says so — `quantity` then counts single
physical pieces instead of rigid view-groups pinned at their drawn offsets.

**Safety note:** BalsaNest treats every closed interior contour as a through-cut
(scrap), because in this workflow all red paths are cut. Before relying on a
nested part, confirm the host part's centre is actually cut out and not, say, an
engrave-only marking. Disable with `--no-hole-nesting` or
`"allow_nesting_in_holes": false`. See `example_bulkhead_job.json`.

### Packing into concave pockets

Parts whose outline curves inward (an arch-shaped bulkhead cutaway, an airfoil's
underside, a C-channel) leave pockets that bounding-box candidate positions never
propose. BalsaNest detects each placed part's pockets (convex hull minus the
outline, shrunk by the spacing) and seeds candidate positions inside them. Unlike
scrap holes, pockets are ordinary connected stock, so these candidates compete on
the normal packing objective — they win whenever they tighten the layout.

Pocket seeds are also proposed when the incoming part is **bigger** than the
pocket: aligned to the pocket mouth so it can dip in partially. Combined with
180° rotations, this is what lets two arch-shaped parts **interlock
tetris-style** ("handshake") — one part flipped so its arch wraps around the
other's — instead of stacking side by side.

### Debug layer (`--debug` / `"debug_borders": true`)

The `Debug (do not cut)` layer visualises what the nester saw. Colours:

| colour | meaning |
| --- | --- |
| **blue** solid rects | each part's placed bounding box |
| **blue** dashed rect | the sheet outline |
| **orange** band | edge margin — unusable border zone |
| **green** fill | scrap cut-outs large enough for hole-nesting (already shrunk by the spacing) |
| **purple** fill | concave pockets the packer may fill |

A colour **legend** is embedded on the layer itself, placed in free sheet space
beside the used footprint, so a printout or screenshot is self-explaining.

Delete the whole layer before cutting (it is grouped as one Inkscape layer).

### Reinsertion polish

After the greedy passes, the best layout gets a **polish pass**: each part on the
footprint boundary (and each small part) is pulled out and re-placed with full
knowledge of everyone else's final position, keeping only moves that strictly
shrink the layout score. This is how parts placed early — before pockets and
scrap holes existed — migrate into them afterwards.

### Compact footprint objective

The packer's primary ranking criterion for every candidate position is the area
of the **combined bounding box of everything on the sheet** after the placement,
so the used stock stays a small clean rectangle and the off-cut stays one large
usable piece. The secondary criterion is the **convex-hull area of the whole
cluster**: every position *inside* the current bounding box ties on the first
criterion, and the hull term is what tells a gap-filling position (adds no hull
area) apart from one that merely leans against the cluster's edge — so internal
gaps are actively filled before the footprint grows. Parts therefore cluster
into one tight block instead of drifting to opposite ends of the sheet. The two
deliberate exceptions: a part that fits inside another part's **scrap cut-out**
is placed there even when that cut-out is far from the cluster (scrap costs
nothing), and a part that cannot fit near the cluster naturally starts a new
shelf at the frontier.

### Smallest footprint

When the parts do not fill the sheet, BalsaNest packs them into the smallest
bounding box it can (tucked into one corner) so the remaining wood stays as one
large, usable rectangle rather than being sprinkled across the sheet.

### Capacity / oversize checks

Before nesting, BalsaNest verifies every distinct part fits on one empty sheet in
at least one allowed orientation, and warns if a job clearly needs more sheets
than `max_sheets`. If parts still cannot be placed, it errors and names them
(unless `--allow-partial` is set).

You can override the grain logic with explicit rotations:

```json
{
  "file": "special_part.svg",
  "quantity": 2,
  "grain": "free",
  "rotations": [0, 180]
}
```

## SVG scale contract

This is intentionally strict because silent scale errors are dangerous for laser-cut aircraft parts.

Best input:

```xml
<svg
  width="8.25in"
  height="1.75in"
  viewBox="0 0 792 168">
```

At 96 px/in, that says the 792-unit drawing width is physically 8.25 inches.

The program:

- reads root `width`, `height`, and `viewBox`;
- derives the physical inches-per-user-unit scale;
- rejects non-uniform X/Y scaling;
- keeps output at 96 px/in.

If an SVG has a `viewBox` but no physical `width`/`height`, BalsaNest assumes SVG CSS pixels (`96 px = 1 in`).

## Output

One sheet:

```text
wing_ribs_laser.svg
wing_ribs_laser_summary.json
```

Multiple sheets:

```text
wing_ribs_laser_sheet_01.svg
wing_ribs_laser_sheet_02.svg
wing_ribs_laser_summary.json
```

No sheet border is drawn on cutting layers, because a laser program may
interpret that border as another cut. (The optional sheet outline lives on the
non-cutting **Debug** layer.)

### Laser conventions (default, editable per machine)

| colour | meaning | stroke |
| --- | --- | --- |
| red `#ff0000` | cut | **hairline** (`~0.001 in`, Inkscape `-inkscape-stroke:hairline`) |
| black `#000000` | raster engrave (default label mode) | filled text, no stroke |
| blue `#0000ff` | outline engrave (optional label mode, much faster) | hairline |

The document opens in Inkscape showing **inches at 1:1 scale** (a
`sodipodi:namedview` with `document-units="in"` is embedded), and the wizard's
default sheet is a 32 × 18 in cutting bed.

Everything is configurable for other machines:

```json
{
  "cut_color": "#ff0000",
  "cut_stroke": "hairline",          // or a number = stroke width in px
  "labels": { "mode": "raster", "color": "#000000", "outline_color": "#0000ff" },
  "group_labels_with_parts": true
}
```

Set `"labels": {"mode": "outline"}` for blue hairline outline-engraved names
instead of black raster fills — outline engraving is far faster.

### Part + label grouping

By default each part's cut paths **and its name label live in one Inkscape
group** (`group_labels_with_parts`), so selecting or moving a part in Inkscape
brings its label along. Colours still separate the laser operations. Set the
option to `false` to get separate `Cut` / `Labels` layers instead.

### DXF input

Part files may be `.dxf` (exported straight from SolidWorks — no Inkscape
conversion step). Units are read from the DXF header (`$INSUNITS`); a file with
no units declared is assumed to be inches with a printed NOTE, and can be forced
per part:

```json
{ "file": "rib.dxf", "quantity": 4, "grain": "parallel", "units": "mm" }
```

### PDF input

Part files may also be `.pdf` (exported straight from SolidWorks). Only vector
geometry is read — text is ignored, so the SolidWorks watermark at the bottom
of the page ("SOLIDWORKS Educational Product...") is dropped automatically;
vectorized watermarks confined to the bottom page edge are filtered out too
(with a printed NOTE). PDF user space is a fixed 72 points per inch, so a 1:1
export nests at true physical scale. Multi-page PDFs use page 1 only.

## Accuracy controls

`sample_step` is how finely curved paths are approximated for collision checking, in inches.

Default:

```json
"sample_step": 0.015
```

Smaller values produce more accurate geometry but take longer.

`grid_step` controls the fallback search resolution used when obvious contact placements do not work.

Default:

```json
"grid_step": 0.04
```

A smaller grid can find tighter layouts at the cost of runtime.


## Project files

```text
webui.py                       # web UI launcher (python webui.py)
balsanest.py                   # terminal CLI launcher
run_webui.bat                  # Windows double-click launcher (web UI)
run_balsanest.bat              # Windows double-click launcher (wizard)
requirements.txt
balsanest_defaults.json        # machine/shop defaults (every parameter listed)
balsanest_core/                # the nesting library, one module per concern
  __init__.py                  #   public API + module map
  errors.py                    #   BalsaNestError
  constants.py                 #   units, namespaces, tunables
  models.py                    #   dataclasses: SheetSpec, Variant, Placement, ...
  svg_geometry.py              #   SVG<->shapely math, mirroring, transforms
  importer.py                  #   Svg/Dxf/Pdf importers + load_sheet_boundary
  variants.py                  #   grain rules -> orientation set
  holes.py                     #   scrap cut-out detection + nesting
  nfp.py                       #   no-fit-polygon exact-contact candidates
  packing.py                   #   greedy engine: collision, compaction, passes
  ga.py                        #   genetic algorithm over the insertion order
  capacity.py                  #   up-front oversize / capacity checks
  labels.py                    #   raster-band label planning
  output.py                    #   SvgSheetWriter + JSON summary
  config.py                    #   JSON job -> JobSpec
  cli.py                       #   wizard, argument parsing, run_job, main
balsanest_web/                 # the Gradio web UI, split by concern
  previews.py                  #   thumbnails, rulers, drawing grid, sheet views
  parts.py                     #   upload-list state
  sheets.py                    #   custom sheet shapes (upload / trace a drawing)
  nesting.py                   #   the streaming run_nest generator
  assets.py                    #   theme, CSS, dark-mode JS, legend graphics
  ui.py                        #   build_ui(): layout + event wiring
examples/
  *.json                       # ready-made job configs
  parts/                       # real + synthetic part drawings (SVG / DXF)
docs/                          # README assets (example nest image)
outputs/                       # generated nests land here (gitignored)
tests/
  test_core.py                 # the test suite across the whole pipeline
```

### Architecture

```
 part files (.svg .dxf .pdf)          job config / web settings
            |                                    |
            v                                    v
   +------------------+               +--------------------+
   |  importer.py     |               |  config.py         |
   |  svg_geometry.py |               |  models.py         |
   +--------+---------+               +---------+----------+
            | LoadedPart (exact paths +         | SheetSpec / OutputOptions
            | shapely collision geometry)       | (optional polygon boundary)
            v                                   v
   +----------------------------------------------------------+
   | variants.py   grain rules -> allowed orientations        |
   | capacity.py   up-front fit checks                        |
   +----------------------------------------------------------+
   |                     nesting engine                       |
   |  packing.py   greedy engine: pack_in_order,              |
   |               heuristic_passes, polish_layout            |
   |  ga.py        genetic algorithm over insertion order     |
   |  nfp.py       no-fit-polygon exact-contact seeds         |
   |  holes.py     scrap-hole / concave-pocket nesting        |
   +---------------------------+------------------------------+
                               | LayoutResult (placements)
                               v
   +----------------------------------------------------------+
   |  labels.py    raster-band label planning                 |
   |  output.py    laser SVG sheets + JSON summary            |
   +---------------------------+------------------------------+
                               v
        front-ends:  webui.py (balsanest_web/ package)
                     balsanest.py (cli.py terminal wizard)
```


Each stage is a module with a single responsibility, wired together only through
the dataclasses in `models.py`, so a module can be changed or replaced in
isolation. Importers share one seam: `SvgPartImporter`, `DxfPartImporter` and
`PdfPartImporter` all expose the same `load()` and converge on svgpathtools
paths (`load_sheet_boundary` reuses them for stock outlines). The optimisers
share another: `pack_in_order` is the greedy evaluation primitive, consumed by
the multi-pass heuristic (`heuristic_passes` / `optimize_layout`) and the
genetic algorithm (`ga_generations` / `optimize_layout_ga`) alike — both are
generators so front-ends can stream a live preview per pass/generation. Two
front-ends exist today: the terminal CLI (`cli.py`) and the Gradio web UI
(`webui.py`); anything new only needs to build a `JobSpec` and call `run_job`
or drive the same pipeline functions directly.

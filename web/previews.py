from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional

from core import OutputOptions, SheetLayout, SheetSpec, write_sheet_svg
from core.svg_geometry import path_to_local_inches

# Coarse sampling for previews/dims only; nesting re-loads parts at the
# user-chosen accuracy.
PREVIEW_SAMPLE_STEP = 0.02


def _geometry_rings_d(geom: Any) -> str:
    """A part's sampled collision polygons as one clean SVG path string.
    Guaranteed well-formed plain decimals, unlike raw imported paths (PDF
    imports in particular can serialize into huge or browser-hostile data)."""
    from shapely.geometry import Polygon

    if isinstance(geom, Polygon):
        polys = [geom]
    else:
        polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
    d_parts = []
    for poly in polys:
        for ring in [poly.exterior, *poly.interiors]:
            pts = list(ring.coords)
            if len(pts) < 3:
                continue
            d_parts.append(
                "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in pts) + " Z"
            )
    return " ".join(d_parts)


def part_preview_datauri(part: Any) -> str:
    """Render a small inline-SVG thumbnail of a LoadedPart.

    Prefers the sampled collision geometry (compact, always valid); falls back
    to the exact imported paths for parts whose geometry is degenerate."""
    w, h = part.base_width_in, part.base_height_in
    pad = 0.03 * max(w, h) + 0.01
    stroke = max(w, h) / 110.0

    d = _geometry_rings_d(part.geometry)
    if d:
        body = (
            f'<path d="{d}" fill="none" fill-rule="evenodd" stroke="#d00" '
            f'stroke-width="{stroke:.5f}" stroke-linejoin="round"/>'
        )
    else:
        ds = []
        for p in part.paths:
            if len(p) == 0:
                continue
            try:
                ds.append(path_to_local_inches(part, p).d())
            except Exception:
                continue
        body = "".join(
            f'<path d="{pd}" fill="none" stroke="#d00" stroke-width="{stroke:.5f}" '
            f'stroke-linecap="round"/>'
            for pd in ds
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{-pad:.4f} {-pad:.4f} {w + 2 * pad:.4f} {h + 2 * pad:.4f}">'
        f"{body}</svg>"
    )
    # Served as a file URL rather than an inline data URI: with many parts the
    # embedded images made streamed UI updates megabytes big, which Safari's
    # stream decoding intermittently corrupted ("could not parse server
    # response"). Requires launch(allowed_paths=[tempdir]).
    out = Path(tempfile.mkdtemp(prefix="balsanest_thumb_")) / "part.svg"
    out.write_text(svg, encoding="utf-8")
    return file_url(out)


def file_url(path: Path | str) -> str:
    """URL under which Gradio serves a temp file (see allowed_paths at launch)."""
    from urllib.parse import quote

    return "/gradio_api/file=" + quote(str(path))


def svg_file_to_img(path: Path) -> str:
    return (
        f'<img src="{file_url(path)}" '
        f'style="width:100%;height:auto;background:#fff;'
        f'border:1px solid #444;border-radius:8px;display:block"/>'
    )


def _ruler_ticks(total: float, horizontal: bool) -> str:
    """Inch ruler ticks + labels positioned in percent, so they stay aligned
    with the neighbouring image at any browser zoom. 0 sits at the top-left."""
    label_step = 1 if total <= 24 else 2
    halves = total <= 16
    spans = []
    i = 0.0
    while i <= total + 1e-9:
        pct = i / total * 100
        whole = abs(i - round(i)) < 1e-9
        size = 9 if whole else 5
        if horizontal:
            spans.append(
                f'<span style="position:absolute;left:{pct:.3f}%;bottom:0;'
                f'height:{size}px;border-left:1px solid #8a8a95"></span>'
            )
            if whole and int(round(i)) % label_step == 0:
                spans.append(
                    f'<span style="position:absolute;left:{pct:.3f}%;bottom:10px;'
                    f'font-size:10px;color:#9a9aa5;transform:translateX(-50%)">'
                    f"{int(round(i))}</span>"
                )
        else:
            spans.append(
                f'<span style="position:absolute;top:{pct:.3f}%;right:0;'
                f'width:{size}px;border-top:1px solid #8a8a95"></span>'
            )
            if whole and int(round(i)) % label_step == 0:
                spans.append(
                    f'<span style="position:absolute;top:{pct:.3f}%;right:11px;'
                    f'font-size:10px;color:#9a9aa5;transform:translateY(-50%)">'
                    f"{int(round(i))}</span>"
                )
        i += 0.5 if halves else 1.0
    return "".join(spans)


def ruled_img_html(img: str, w_in: float, h_in: float) -> str:
    """Wrap a sheet image with browser-side inch rulers (0,0 at top-left)."""
    return (
        '<div style="margin:4px 0 10px">'
        '<div style="display:flex"><div style="width:28px;flex:none"></div>'
        f'<div style="position:relative;height:24px;flex:1">{_ruler_ticks(w_in, True)}</div></div>'
        '<div style="display:flex;align-items:stretch">'
        f'<div style="position:relative;width:28px;flex:none">{_ruler_ticks(h_in, False)}</div>'
        f'<div style="flex:1;min-width:0">{img}</div></div></div>'
    )


def sheets_html(files: list[Path], summary: dict) -> str:
    """Per-sheet captioned, ruler-wrapped images for the visualizer."""
    svg_files = [f for f in files if f.suffix == ".svg"]
    sheet_w, sheet_h = summary["sheet_size_in"]
    html_parts = []
    for i, (svg, info) in enumerate(zip(svg_files, summary["sheets"]), start=1):
        html_parts.append(
            f'<div style="margin-bottom:2px;font-weight:600">Sheet {i} / '
            f'{len(svg_files)} &mdash; {info["part_count"]} parts, '
            f'~{info["approx_part_area_utilization_percent"]}% material used</div>'
            + ruled_img_html(svg_file_to_img(svg), sheet_w, sheet_h)
        )
    return "".join(html_parts)


# --- graph-paper drawing canvas ----------------------------------------------

_GRID_FILE_CACHE: dict[tuple, str] = {}
_GRID_ARRAY_CACHE: dict[tuple, Any] = {}


def grid_canvas_file(w_in: float, h_in: float, cw: int, ch: int) -> str:
    """Graph-paper drawing background as a PNG file: 1-inch grid with numbers
    along the top and left edges. Baked into the canvas so it stays aligned
    under the editor's zoom and pan, unlike an external ruler. Passed to the
    editor as a plain filepath -- the form Gradio serves most reliably."""
    key = (round(w_in, 3), round(h_in, 3), cw, ch)
    cached = _GRID_FILE_CACHE.get(key)
    if cached and Path(cached).exists():
        return cached
    import numpy as np
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (cw, ch), (248, 248, 250))
    d = ImageDraw.Draw(img)
    ppx = cw / w_in
    ppy = ch / h_in
    minor = (233, 233, 239)
    major = (205, 205, 214)
    label = (140, 140, 150)

    i = 0.5
    while i < w_in:
        x = round(i * ppx)
        whole = abs(i - round(i)) < 1e-9
        d.line([(x, 0), (x, ch)], fill=major if whole else minor, width=1)
        i += 0.5
    i = 0.5
    while i < h_in:
        y = round(i * ppy)
        whole = abs(i - round(i)) < 1e-9
        d.line([(0, y), (cw, y)], fill=major if whole else minor, width=1)
        i += 0.5

    step = 1 if w_in <= 24 else 2
    for n in range(0, int(w_in) + 1, step):
        d.text((min(round(n * ppx) + 3, cw - 14), 2), str(n), fill=label)
    step = 1 if h_in <= 24 else 2
    for n in range(step, int(h_in) + 1, step):
        d.text((3, min(round(n * ppy) + 2, ch - 12)), str(n), fill=label)

    path = Path(tempfile.mkdtemp(prefix="balsanest_grid_")) / "grid.png"
    img.save(path)
    _GRID_FILE_CACHE[key] = str(path)
    _GRID_ARRAY_CACHE[key] = np.asarray(img).copy()
    return str(path)


def grid_background_array(w_in: float, h_in: float, cw: int, ch: int):
    """The grid background as a numpy array (generating it if needed), used to
    tell painted strokes apart from the untouched graph paper."""
    key = (round(w_in, 3), round(h_in, 3), cw, ch)
    if key not in _GRID_ARRAY_CACHE:
        grid_canvas_file(w_in, h_in, cw, ch)
    return _GRID_ARRAY_CACHE.get(key)


# --- sheet-shape previews ----------------------------------------------------

def _poly_d(poly: Any) -> str:
    def ring(coords) -> str:
        return "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in coords) + " Z"

    return " ".join(
        [ring(poly.exterior.coords)] + [ring(i.coords) for i in poly.interiors]
    )


def boundary_html(poly: Any, w: float, h: float, note: str) -> str:
    """Small wood-coloured thumbnail of a custom sheet outline plus a note."""
    pad = 0.03 * max(w, h)
    stroke = max(w, h) / 120.0
    return (
        f'<div style="display:flex;gap:14px;align-items:center;margin:2px 0">'
        f'<svg width="150" height="95" viewBox="{-pad:.3f} {-pad:.3f} '
        f'{w + 2 * pad:.3f} {h + 2 * pad:.3f}" '
        f'style="background:#1c1c22;border-radius:6px;flex:none">'
        f'<path d="{_poly_d(poly)}" fill="#c9a06c" fill-rule="evenodd" '
        f'stroke="#8a6a3f" stroke-width="{stroke:.4f}"/></svg>'
        f'<div style="font-size:13px;line-height:1.4">{note}</div></div>'
    )


def empty_sheet_viz(boundary: Optional[Any], w: float, h: float) -> str:
    """Show a not-yet-nested (custom or plain) sheet in the visualizer."""
    sheet = SheetSpec(width=float(w), height=float(h), boundary=boundary)
    out = Path(tempfile.mkdtemp(prefix="balsanest_web_")) / "sheet_shape.svg"
    write_sheet_svg(
        out, SheetLayout([]), sheet, 1, 1,
        OutputOptions(label_parts=False, draw_boundary=True),
    )
    kind = "Custom sheet shape" if boundary is not None else "Empty sheet"
    return (
        f'<div style="margin-bottom:2px;font-weight:600">{kind} '
        f"&mdash; {float(w):.2f} &times; {float(h):.2f} in</div>"
        + ruled_img_html(svg_file_to_img(out), float(w), float(h))
    )

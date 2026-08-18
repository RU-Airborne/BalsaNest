from pathlib import Path
from typing import Any, Optional

import gradio as gr

from core import BalsaNestError, load_sheet_boundary

from .previews import (
    PREVIEW_SAMPLE_STEP,
    boundary_html,
    empty_sheet_viz,
    grid_background_array,
)

TIGHT = "Tightest layout"
OFFCUT = "Largest usable off-cut"

# Shown in the outline panel whenever the objective was switched for you.
SWITCH_NOTE = (
    '<div style="margin-top:6px;padding:6px 9px;border-radius:6px;'
    'background:rgba(46,139,87,0.16);border:1px solid #2e8b57;'
    'font-size:12px;line-height:1.35">'
    "<b>Optimize for</b> switched to <b>Largest usable off-cut</b>. "
    "On an odd-shaped sheet, packing into the smallest rectangle tends to eat "
    "into the good end. Change it back if you would rather have the tightest "
    "layout."
    "</div>"
)


def set_outline_from_file(path: Optional[str], sheet_w: float, sheet_h: float):
    """Load an uploaded outline drawing as the custom sheet shape."""
    if not path:
        return (
            None, gr.update(value="", visible=False), gr.update(), gr.update(),
            empty_sheet_viz(None, sheet_w, sheet_h), gr.update(value=TIGHT),
        )
    try:
        boundary, w, h = load_sheet_boundary(Path(path), PREVIEW_SAMPLE_STEP)
    except BalsaNestError as exc:
        raise gr.Error(str(exc)) from exc
    holes = len(boundary.interiors)
    note = "<b>Custom sheet shape active</b>" + (
        f" ({holes} blocked hole(s))" if holes else ""
    ) + SWITCH_NOTE
    gr.Warning(
        "Custom sheet shape: Optimize for switched to Largest usable off-cut."
    )
    # The WKT rides along with the path so a nesting run can draw the custom
    # sheet on its very first frame, before it re-reads the drawing at the
    # accuracy the user picked.
    state = {"kind": "file", "path": str(path), "w": w, "h": h, "wkt": boundary.wkt}
    return (
        state,
        gr.update(value=boundary_html(boundary, w, h, note), visible=True),
        gr.update(value=round(w, 3)),
        gr.update(value=round(h, 3)),
        empty_sheet_viz(boundary, w, h),
        gr.update(value=OFFCUT),
    )


def set_outline_from_drawing(editor_value: Any, sheet_w: float, sheet_h: float):
    """Trace the painted canvas into a sheet polygon at physical scale. The
    canvas maps onto the current sheet width x height in inches."""
    import numpy as np
    from shapely.geometry import MultiPolygon, box as shp_box
    from shapely.ops import unary_union

    comp = (editor_value or {}).get("composite")
    if comp is None:
        raise gr.Error("Draw the sheet shape first: paint it with the brush.")
    arr = np.asarray(comp)
    mask = None
    if arr.ndim == 3 and arr.shape[2] >= 3:
        # Primary detection: anything that differs from the known graph-paper
        # background is a painted stroke. Works for any brush colour, and
        # the eraser restores the background so erased areas don't count.
        H, W = arr.shape[:2]
        bg = grid_background_array(float(sheet_w), float(sheet_h), W, H)
        if bg is not None and bg.shape[:2] == (H, W):
            diff = np.abs(
                arr[..., :3].astype(int) - bg[..., :3].astype(int)
            ).max(axis=2)
            mask = diff > 40
        if mask is None or not mask.any():
            if arr.shape[2] == 4 and arr[..., 3].min() < 250:
                mask = arr[..., 3] > 40  # transparent canvas: any stroke
            else:
                gray = arr[..., :3].astype(float).mean(axis=2)
                mask = gray < 120  # dark strokes on light paper
    else:
        mask = arr.astype(float) < 120
    if mask.sum() < 25:
        raise gr.Error(
            "The canvas looks empty. Paint the usable material with the "
            "brush first (any colour that stands out from the white paper)."
        )

    H, W = mask.shape
    f = max(1, int(np.ceil(max(H, W) / 120)))  # downsample for tracing speed
    Hc, Wc = H // f, W // f
    coarse = mask[: Hc * f, : Wc * f].reshape(Hc, f, Wc, f).mean(axis=(1, 3)) > 0.4
    sx = float(sheet_w) / W
    sy = float(sheet_h) / H
    cells = [
        shp_box(x * f * sx, y * f * sy, (x + 1) * f * sx, (y + 1) * f * sy)
        for y, x in zip(*np.nonzero(coarse))
    ]
    if not cells:
        raise gr.Error("Could not trace a filled area. Use a bigger brush.")
    poly = unary_union(cells)
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    poly = poly.simplify(1.5 * f * max(sx, sy)).buffer(0)
    # Simplify/buffer can nudge the shape a hair past the canvas edge. The
    # sheet keeps its size, so clip rather than rescale.
    poly = poly.intersection(shp_box(0.0, 0.0, float(sheet_w), float(sheet_h)))
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    minx, miny, maxx, maxy = poly.bounds
    shape_w, shape_h = maxx - minx, maxy - miny
    if shape_w < 0.5 or shape_h < 0.5:
        raise gr.Error("The traced shape is under half an inch. Draw it larger.")

    w, h = float(sheet_w), float(sheet_h)
    note = "<b>Drawn sheet shape active</b>" + SWITCH_NOTE
    gr.Warning(
        "Drawn sheet shape: Optimize for switched to Largest usable off-cut."
    )
    state = {"kind": "wkt", "wkt": poly.wkt, "w": w, "h": h}
    # The drawing window is closed by a chained step in the UI, NOT from here:
    # closing it re-renders the modal away while this event is still running,
    # which strands the event's progress indicator blinking forever.
    return (
        state,
        gr.update(value=boundary_html(poly, w, h, note), visible=True),
        gr.update(),
        gr.update(),
        empty_sheet_viz(poly, w, h),
        gr.update(value=OFFCUT),
    )

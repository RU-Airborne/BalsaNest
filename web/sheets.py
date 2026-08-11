from __future__ import annotations

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


def set_outline_from_file(path: Optional[str], sheet_w: float, sheet_h: float):
    """Load an uploaded outline drawing as the custom sheet shape."""
    if not path:
        return (
            None, "", gr.update(), gr.update(),
            empty_sheet_viz(None, sheet_w, sheet_h),
        )
    try:
        boundary, w, h = load_sheet_boundary(Path(path), PREVIEW_SAMPLE_STEP)
    except BalsaNestError as exc:
        raise gr.Error(str(exc)) from exc
    holes = len(boundary.interiors)
    note = (
        f"<b>Custom sheet shape active</b> &mdash; {w:.2f} &times; {h:.2f} in"
        + (f", {holes} blocked hole(s)" if holes else "")
        + ".<br>Parts will only be placed inside this outline. Remove the file "
        "to go back to a plain rectangle."
    )
    state = {"kind": "file", "path": str(path), "w": w, "h": h}
    return (
        state,
        boundary_html(boundary, w, h, note),
        gr.update(value=round(w, 3)),
        gr.update(value=round(h, 3)),
        empty_sheet_viz(boundary, w, h),
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
        # background is a painted stroke -- works for any brush colour, and
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
            "The canvas looks empty -- paint the usable material with the "
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
        raise gr.Error("Could not trace a filled area -- use a bigger brush.")
    poly = unary_union(cells)
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    poly = poly.simplify(1.5 * f * max(sx, sy)).buffer(0)
    minx, miny, maxx, maxy = poly.bounds
    from shapely.affinity import translate as shp_translate

    poly = shp_translate(poly, xoff=-minx, yoff=-miny)
    w, h = maxx - minx, maxy - miny
    if w < 0.5 or h < 0.5:
        raise gr.Error("The traced shape is under half an inch -- draw it larger.")

    note = (
        f"<b>Drawn sheet shape active</b> &mdash; traced to {w:.2f} &times; {h:.2f} in "
        f"(the canvas maps onto the sheet width x height)."
        "<br>Press Clear below to go back to a plain rectangle."
    )
    state = {"kind": "wkt", "wkt": poly.wkt, "w": w, "h": h}
    return (
        state,
        boundary_html(poly, w, h, note),
        gr.update(value=round(w, 3)),
        gr.update(value=round(h, 3)),
        False,  # close the drawing window
        empty_sheet_viz(poly, w, h),
    )

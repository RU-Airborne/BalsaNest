import base64
import json
import tempfile
from pathlib import Path
from typing import Optional

import gradio as gr

from core import DEFAULT_WELD_IN

from .parts import reload_with_units, sync_parts
from .previews import boundary_html, empty_sheet_viz

JOB_VERSION = 1


def save_job(
    parts: list[dict],
    outline: Optional[dict],
    sheet_w: float, sheet_h: float, grain_axis: str,
    margin: float, spacing: float, max_sheets: float,
    optimizer: str, passes: float, allow_mirror: bool, allow_holes: bool,
    allow_partial: bool, compress: bool, objective: str,
    grid_step: float, sample_step: float, weld: float,
    seed: float,
    label_parts: bool, export_unlabeled: bool, export_scrap: bool,
    label_mode: str, label_font: str,
    label_color: str, outline_color: str, cut_color: str,
    cut_stroke_mode: str, stroke_px: float, stroke_in: float, kerf: float,
    merge_cuts: bool, debug_overlay: bool,
):
    """Bundle the whole session (part files included) into one JSON file."""
    if not parts:
        raise gr.Error("Nothing to save yet: add at least one part first.")

    def embed(path: Path) -> str:
        try:
            return base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise gr.Error(f"Could not read {path.name} for saving: {exc}") from exc

    job_parts = []
    for p in parts:
        path = Path(p["path"])
        job_parts.append(
            {
                "filename": path.name,
                "data": embed(path),
                "qty": int(p["qty"]),
                "grain": p["grain"],
                "angle": float(p["angle"]),
                "units": p["units"],
            }
        )

    job_outline: Optional[dict] = None
    if outline:
        if outline.get("kind") == "file":
            path = Path(outline["path"])
            job_outline = {"kind": "file", "filename": path.name, "data": embed(path)}
        else:
            job_outline = {
                "kind": "wkt",
                "wkt": outline["wkt"],
                "w": outline["w"],
                "h": outline["h"],
            }

    job = {
        "balsanest_job": JOB_VERSION,
        "parts": job_parts,
        "outline": job_outline,
        "sheet": {
            "width": float(sheet_w), "height": float(sheet_h),
            "grain_axis": grain_axis, "margin": float(margin),
            "spacing": float(spacing), "max_sheets": int(max_sheets),
        },
        "nesting": {
            "optimizer": optimizer, "passes": int(passes),
            "allow_mirror": bool(allow_mirror), "allow_holes": bool(allow_holes),
            "allow_partial": bool(allow_partial), "compress": bool(compress), "objective": str(objective),
            "grid_step": float(grid_step),
            "sample_step": float(sample_step), "weld_distance": float(weld),
            "seed": int(seed),
        },
        "output": {
            "label_parts": bool(label_parts),
            "export_unlabeled": bool(export_unlabeled),
            "export_scrap": bool(export_scrap),
            "label_mode": label_mode, "label_font": label_font,
            "label_color": label_color,
            "outline_color": outline_color, "cut_color": cut_color,
            "cut_stroke_mode": cut_stroke_mode, "stroke_px": float(stroke_px),
            "stroke_in": float(stroke_in), "kerf": float(kerf),
            "merge_cuts": bool(merge_cuts),
            "debug_overlay": bool(debug_overlay),
        },
    }
    out = Path(tempfile.mkdtemp(prefix="balsanest_job_")) / "balsanest_job.json"
    out.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return gr.update(value=str(out), visible=True)


def load_job(path: Optional[str]):
    """Restore a job file saved by ``save_job``. Returns updates in the
    exact order of the load wiring in web/ui.py."""
    if not path:
        raise gr.Error("Choose a job file first.")
    try:
        job = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise gr.Error(f"Could not read the job file: {exc}") from exc
    if not isinstance(job, dict) or "balsanest_job" not in job:
        raise gr.Error("This file is not a BalsaNest job file.")

    tmp = Path(tempfile.mkdtemp(prefix="balsanest_job_load_"))
    file_paths: list[str] = []
    for i, jp in enumerate(job.get("parts", [])):
        # One subfolder per part keeps original filenames (and therefore part
        # names) even when two saved parts share a filename.
        folder = tmp / f"part_{i:02d}"
        folder.mkdir()
        f = folder / Path(jp["filename"]).name
        f.write_bytes(base64.b64decode(jp["data"]))
        file_paths.append(str(f))

    parts, _ = sync_parts(file_paths, [])
    for i, (entry, jp) in enumerate(zip(parts, job.get("parts", []))):
        entry.update(
            qty=int(jp.get("qty", 1)),
            grain=jp.get("grain", "free"),
            angle=float(jp.get("angle", 0.0)),
            units=jp.get("units", "auto"),
        )
        if entry["units"] != "auto" and entry["suffix"] == ".dxf":
            parts = reload_with_units(parts, i)

    sheet = job.get("sheet", {})
    sheet_w = float(sheet.get("width", 32.0))
    sheet_h = float(sheet.get("height", 18.0))

    outline = job.get("outline")
    outline_state: Optional[dict] = None
    outline_note = ""
    outline_file_update = gr.update(value=None)
    if outline and outline.get("kind") == "file":
        f = tmp / "outline" / Path(outline["filename"]).name
        f.parent.mkdir()
        f.write_bytes(base64.b64decode(outline["data"]))
        # Setting the outline upload component replays its change handler,
        # which rebuilds the outline state, note and preview from the file.
        outline_file_update = gr.update(value=str(f))
        viz_update = gr.update()
    elif outline and outline.get("kind") == "wkt":
        from shapely import wkt as shp_wkt

        try:
            poly = shp_wkt.loads(outline["wkt"])
        except Exception as exc:
            raise gr.Error(f"The saved sheet shape is invalid: {exc}") from exc
        w, h = float(outline["w"]), float(outline["h"])
        outline_state = {"kind": "wkt", "wkt": outline["wkt"], "w": w, "h": h}
        outline_note = boundary_html(
            poly, w, h, "<b>Drawn sheet shape active</b>"
        )
        outline_file_update = gr.update()
        viz_update = empty_sheet_viz(poly, w, h)
    else:
        viz_update = empty_sheet_viz(None, sheet_w, sheet_h)

    nest = job.get("nesting", {})
    out = job.get("output", {})
    return (
        parts, parts, gr.update(value=file_paths),
        sheet_w, sheet_h,
        sheet.get("grain_axis", "x"), float(sheet.get("margin", 0.05)),
        float(sheet.get("spacing", 0.04)), int(sheet.get("max_sheets", 0)),
        outline_state,
        gr.update(value=outline_note, visible=bool(outline_note)),
        outline_file_update, viz_update,
        nest.get("optimizer", "Heuristic optimization (fast)"),
        int(nest.get("passes", 5)), bool(nest.get("allow_mirror", True)),
        bool(nest.get("allow_holes", True)), bool(nest.get("allow_partial", False)),
        bool(nest.get("compress", True)),
        str(nest.get("objective", "Tightest layout")),
        float(nest.get("grid_step", 0.04)), float(nest.get("sample_step", 0.015)),
        float(nest.get("weld_distance", DEFAULT_WELD_IN)),
        int(nest.get("seed", 42)),
        bool(out.get("label_parts", True)),
        bool(out.get("export_unlabeled", True)),
        bool(out.get("export_scrap", True)),
        out.get("label_mode", "raster"), out.get("label_font", "sans-serif"),
        out.get("label_color", "#0000ff"),
        out.get("outline_color", "#0000ff"), out.get("cut_color", "#ff0000"),
        out.get("cut_stroke_mode", "Hairline"), float(out.get("stroke_px", 1.0)),
        float(out.get("stroke_in", 0.005)), float(out.get("kerf", 0.0)),
        bool(out.get("merge_cuts", False)),
        bool(out.get("debug_overlay", False)),
    )

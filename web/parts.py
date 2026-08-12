from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core import BalsaNestError, PartRequest, load_part

from .previews import PREVIEW_SAMPLE_STEP, part_preview_datauri


def _file_key(path: str) -> tuple[str, int]:
    """Identity of a file independent of where it is cached: Gradio copies
    uploaded/programmatically-set files into fresh temp paths, so the same
    part can reappear under a new path (e.g. after Load job) and must not be
    treated as a second part."""
    p = Path(path)
    try:
        return (p.name, p.stat().st_size)
    except OSError:
        return (p.name, -1)


def sync_parts(file_list: Optional[list[str]], parts: list[dict]):
    """Sync the parts list with the upload component, keeping per-part settings
    (quantity, grain, ...) for files that were already loaded.

    Dropping a second batch of files onto a populated upload component can
    REPLACE its value instead of appending, silently losing earlier parts. So:
    if the new value contains files we have not seen, treat it as an addition
    and merge with the existing list; only a subset of known files (the user
    removed one with its X) rebuilds from the component value. Returns
    (parts, upload_component_update) so the merged list is pushed back into
    the component."""
    import gradio as gr

    file_list = [str(f) for f in (file_list or [])]
    existing = [p["path"] for p in parts]
    # Translate re-cached copies of known files back to their original paths.
    by_key = {}
    for p in parts:
        by_key.setdefault(_file_key(p["path"]), p["path"])
    file_list = [by_key.get(_file_key(f), f) for f in file_list]
    if file_list and not set(file_list) <= set(existing):
        merged = existing + [f for f in file_list if f not in existing]
    else:
        merged = file_list  # a removal (or a cleared component)

    by_path = {p["path"]: p for p in parts}
    out: list[dict] = []
    for f in merged:
        if f in by_path:
            out.append(by_path[f])
            continue
        entry: dict[str, Any] = {
            "path": f,
            "name": Path(f).stem,
            "suffix": Path(f).suffix.lower(),
            "qty": 1,
            "grain": "free",
            "angle": 0.0,
            "units": "auto",
            "error": None,
            "notes": [],
            "width": 0.0,
            "height": 0.0,
            "preview": None,
        }
        try:
            part = load_part(
                PartRequest(Path(f), 1, grain="free"), PREVIEW_SAMPLE_STEP
            )
            entry.update(
                width=part.base_width_in,
                height=part.base_height_in,
                notes=list(part.notes),
                preview=part_preview_datauri(part),
            )
        except BalsaNestError as exc:
            entry["error"] = str(exc)
        except Exception as exc:  # unexpected importer failure: show, don't crash
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out.append(entry)

    # Push the merged list back into the component only when it differs, so
    # this handler does not re-trigger itself forever.
    files_update = gr.update(value=merged) if merged != file_list else gr.update()
    return out, files_update


def reload_with_units(parts: list[dict], idx: int) -> list[dict]:
    """Re-import one part after a units override change (DXF only)."""
    p = dict(parts[idx])
    units = None if p["units"] == "auto" else p["units"]
    try:
        part = load_part(
            PartRequest(Path(p["path"]), 1, grain="free", units=units),
            PREVIEW_SAMPLE_STEP,
        )
        p.update(
            width=part.base_width_in,
            height=part.base_height_in,
            notes=list(part.notes),
            preview=part_preview_datauri(part),
            error=None,
        )
    except BalsaNestError as exc:
        p["error"] = str(exc)
    parts = list(parts)
    parts[idx] = p
    return parts

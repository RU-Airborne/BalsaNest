"""Turn a JSON job file into a :class:`JobSpec`.

Also handles the optional machine defaults file (``balsanest_defaults.json``),
per-machine/shop settings that rarely change (sheet size, colours, stroke
style, label conventions) live there once, and every job config only carries
what is specific to that job. Job values always win over defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import BalsaNestError
from .models import JobSpec, OutputOptions, PartRequest, SheetSpec

DEFAULTS_FILENAME = "balsanest_defaults.json"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge: ``override`` wins, nested dicts merge key-by-key."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_defaults(*directories: Path) -> dict[str, Any]:
    """Load ``balsanest_defaults.json`` from the first directory that has one.

    Job-specific keys (``parts``, ``output``) are ignored if present -- the
    defaults file describes the machine and shop conventions, not a job."""
    for directory in directories:
        path = directory / DEFAULTS_FILENAME
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    defaults = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                raise BalsaNestError(f"Could not read {path}: {exc}")
            if not isinstance(defaults, dict):
                raise BalsaNestError(f"{path} must contain a JSON object.")
            defaults.pop("parts", None)
            defaults.pop("output", None)
            return defaults
    return {}


def output_options_from_config(config: dict[str, Any]) -> OutputOptions:
    labels_cfg = config.get("labels", {})
    if isinstance(labels_cfg, bool):
        labels_cfg = {"enabled": labels_cfg}
    d = OutputOptions()
    cut_stroke_raw = config.get("cut_stroke", d.cut_stroke)
    if not (isinstance(cut_stroke_raw, str) and cut_stroke_raw.lower() == "hairline"):
        cut_stroke_raw = float(cut_stroke_raw)
    return OutputOptions(
        label_parts=bool(labels_cfg.get("enabled", d.label_parts)),
        label_min_font_in=float(labels_cfg.get("min_font_in", d.label_min_font_in)),
        label_max_font_in=float(labels_cfg.get("max_font_in", d.label_max_font_in)),
        label_align_bands=bool(labels_cfg.get("align_bands", d.label_align_bands)),
        label_max_lines=int(labels_cfg.get("max_lines", d.label_max_lines)),
        label_color=str(labels_cfg.get("color", d.label_color)),
        label_mode=str(labels_cfg.get("mode", d.label_mode)).lower(),
        label_outline_color=str(labels_cfg.get("outline_color", d.label_outline_color)),
        cut_color=str(config.get("cut_color", d.cut_color)),
        cut_stroke=cut_stroke_raw,
        group_labels_with_parts=bool(
            config.get("group_labels_with_parts", d.group_labels_with_parts)
        ),
        merge_common_cuts=bool(config.get("merge_common_cuts", d.merge_common_cuts)),
        debug_borders=bool(config.get("debug_borders", d.debug_borders)),
        debug_color=str(config.get("debug_color", d.debug_color)),
    )


def config_to_specs(config: dict[str, Any], config_dir: Path) -> JobSpec:
    sheet_cfg = config.get("sheet", {})
    max_sheets_raw = sheet_cfg.get("max_sheets")

    sample_step = float(config.get("sample_step", 0.015))
    if sample_step <= 0:
        raise BalsaNestError("sample_step must be > 0.")

    # Optional non-rectangular stock: an outline drawing whose largest closed
    # contour becomes the sheet shape (its size overrides width/height).
    boundary = None
    outline_raw = sheet_cfg.get("outline_file")
    if outline_raw:
        from .importer import load_sheet_boundary

        outline_path = Path(outline_raw)
        if not outline_path.is_absolute():
            outline_path = (config_dir / outline_path).resolve()
        boundary, sheet_w, sheet_h = load_sheet_boundary(outline_path, sample_step)
    else:
        sheet_w = float(sheet_cfg["width"])
        sheet_h = float(sheet_cfg["height"])

    sheet = SheetSpec(
        width=sheet_w,
        height=sheet_h,
        grain_axis=str(sheet_cfg.get("grain_axis", "x")).lower(),
        margin=float(sheet_cfg.get("margin", 0.05)),
        spacing=float(sheet_cfg.get("spacing", 0.04)),
        grid_step=float(sheet_cfg.get("grid_step", 0.04)),
        passes=int(sheet_cfg.get("passes", 8)),
        max_sheets=int(max_sheets_raw) if max_sheets_raw is not None else None,
        allow_mirror=bool(sheet_cfg.get("allow_mirror", True)),
        compact=bool(sheet_cfg.get("compact", True)),
        allow_nesting_in_holes=bool(sheet_cfg.get("allow_nesting_in_holes", True)),
        min_hole_area=float(sheet_cfg.get("min_hole_area", 0.02)),
        boundary=boundary,
    )
    sheet.validate()

    requests: list[PartRequest] = []
    for raw in config.get("parts", []):
        file_path = Path(raw["file"])
        if not file_path.is_absolute():
            file_path = (config_dir / file_path).resolve()
        rotations = raw.get("rotations")
        rotations_tuple = tuple(float(v) for v in rotations) if rotations is not None else None
        units = raw.get("units")
        req = PartRequest(
            file=file_path,
            quantity=int(raw.get("quantity", 1)),
            grain=str(raw.get("grain", "free")).lower(),
            grain_angle_deg=float(raw.get("grain_angle_deg", 0.0)),
            rotations=rotations_tuple,
            name=raw.get("name"),
            units=str(units).lower() if units is not None else None,
        )
        req.validate()
        requests.append(req)

    if not requests:
        raise BalsaNestError("Config must contain at least one part.")

    seed = int(config.get("seed", 42))

    output_raw = Path(config.get("output", "laser_nest.svg"))
    if not output_raw.is_absolute():
        output_raw = (config_dir / output_raw).resolve()

    return JobSpec(
        sheet=sheet,
        requests=requests,
        sample_step=sample_step,
        seed=seed,
        output=output_raw,
        options=output_options_from_config(config),
        allow_partial=bool(config.get("allow_partial", False)),
    )


def load_config(path: Path) -> JobSpec:
    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    # Machine defaults (if any) sit underneath the job config: the job file
    # only needs to carry what differs from the shop's standard setup.
    defaults = load_defaults(path.parent, Path.cwd())
    if defaults:
        config = deep_merge(defaults, config)
    return config_to_specs(config, path.parent.resolve())

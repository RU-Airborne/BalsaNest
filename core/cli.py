"""Terminal CLI."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Sequence

from .capacity import preflight_capacity
from .config import (
    DEFAULTS_FILENAME,
    load_config,
    load_defaults,
    output_options_from_config,
)
from .constants import DEFAULT_WELD_IN
from .errors import BalsaNestError
from .holes import detect_nestings
from .importer import load_part
from .models import JobSpec, LoadedPart, PartRequest, SheetSpec
from .output import save_outputs
from .packing import optimize_layout
from .models import placement_bounds
from .variants import allowed_rotations, make_items


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw if raw else (default or "")


def ask_float(prompt: str, default: float) -> float:
    while True:
        try:
            return float(ask(prompt, f"{default:g}"))
        except ValueError:
            print("Please enter a number.")


def ask_int(prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        try:
            value = int(ask(prompt, str(default)))
            if value < minimum:
                raise ValueError
            return value
        except ValueError:
            print(f"Please enter an integer >= {minimum}.")


def ask_choice(prompt: str, choices: Sequence[str], default: str) -> str:
    normalized = {c.lower(): c.lower() for c in choices}
    while True:
        value = ask(prompt + " (" + "/".join(choices) + ")", default).lower()
        if value in normalized:
            return value
        print("Choose one of: " + ", ".join(choices))


def ask_bool(prompt: str, default: bool) -> bool:
    default_str = "y" if default else "n"
    while True:
        value = ask(prompt + " (y/n)", default_str).lower()
        if value in {"y", "yes", "true", "1"}:
            return True
        if value in {"n", "no", "false", "0"}:
            return False
        print("Please answer y or n.")


def ask_optional_int(prompt: str, minimum: int = 1) -> Optional[int]:
    while True:
        raw = ask(prompt, "unlimited").lower()
        if raw in {"", "unlimited", "none", "0"}:
            return None
        try:
            value = int(raw)
            if value < minimum:
                raise ValueError
            return value
        except ValueError:
            print(f"Enter an integer >= {minimum}, or 'unlimited'.")


def interactive_specs() -> JobSpec:
    print("\nBalsaNest")
    print("------------------------------------")
    print("All sheet dimensions and spacing values below are in inches.\n")

    # Machine/shop defaults, if the user keeps a balsanest_defaults.json in the
    # working directory: they become the wizard's suggested answers, so a run
    # on the usual machine is just Enter-Enter-Enter.
    defaults = load_defaults(Path.cwd())
    if defaults:
        print(f"(defaults loaded from {DEFAULTS_FILENAME}, press Enter to accept them)\n")
    d_sheet = defaults.get("sheet", {})

    width = ask_float("Sheet width / X dimension", float(d_sheet.get("width", 32.0)))
    height = ask_float("Sheet height / Y dimension", float(d_sheet.get("height", 18.0)))
    grain_axis = ask_choice("Sheet grain axis", ["x", "y"], str(d_sheet.get("grain_axis", "x")).lower())
    margin = ask_float("Edge margin", float(d_sheet.get("margin", 0.05)))
    spacing = ask_float("Minimum spacing between parts", float(d_sheet.get("spacing", 0.04)))
    grid_step = ask_float("Fallback search grid step", float(d_sheet.get("grid_step", 0.04)))
    passes = ask_int("Optimization passes", int(d_sheet.get("passes", 8)))
    max_sheets = ask_optional_int("Max sheets allowed (blank/unlimited to auto-add sheets)")
    allow_mirror = ask_bool(
        "Allow flipping/mirroring parts for tighter nesting (grain preserved)",
        bool(d_sheet.get("allow_mirror", True)),
    )
    allow_holes = ask_bool(
        "Nest small parts inside larger parts' scrap cut-outs when they fit",
        bool(d_sheet.get("allow_nesting_in_holes", True)),
    )
    sheet = SheetSpec(
        width=width,
        height=height,
        grain_axis=grain_axis,
        margin=margin,
        spacing=spacing,
        grid_step=grid_step,
        passes=passes,
        max_sheets=max_sheets,
        allow_mirror=allow_mirror,
        allow_nesting_in_holes=allow_holes,
    )
    sheet.validate()

    count = ask_int("How many different part files (SVG, DXF, or PDF)?", 1)
    requests: list[PartRequest] = []

    print(
        "\nGrain rule meaning:\n"
        "  parallel      source part grain/reference axis aligns with sheet grain\n"
        "  perpendicular source part grain/reference axis is 90° to sheet grain\n"
        "  free          rotations 0/90/180/270 are allowed\n"
        "\nThe source part grain/reference axis defaults to horizontal (0°).\n"
    )

    for idx in range(count):
        print(f"Part {idx + 1}/{count}")
        while True:
            raw_path = ask("  Part file path (.svg, .dxf, or .pdf)")
            file_path = Path(raw_path).expanduser().resolve()
            if file_path.exists():
                break
            print(f"  File not found: {file_path}")

        qty = ask_int("  Quantity", 1)
        grain = ask_choice("  Grain rule", ["parallel", "perpendicular", "free"], "parallel")
        grain_angle = 0.0
        if grain != "free":
            grain_angle = ask_float("  Part grain/reference axis angle in the SVG (0 = horizontal)", 0.0)

        req = PartRequest(file=file_path, quantity=qty, grain=grain, grain_angle_deg=grain_angle)
        req.validate()
        requests.append(req)
        print()

    sample_step = ask_float(
        "Curve sampling step used for collision geometry (smaller = more accurate)",
        float(defaults.get("sample_step", 0.015)),
    )
    seed = ask_int("Optimization random seed", int(defaults.get("seed", 42)), minimum=0)

    # Laser conventions (colours, stroke style, label mode) come from the
    # defaults file.
    base_options = output_options_from_config(defaults)
    label_parts = ask_bool("Engrave each part's file name as a label", base_options.label_parts)
    debug_borders = ask_bool(
        "Add debug overlay (part bboxes, margin band, scrap holes, pockets + legend; not for cutting)",
        base_options.debug_borders,
    )
    options = replace(base_options, label_parts=label_parts, debug_borders=debug_borders)

    customize = ask_bool(
        f"Customize laser colours/strokes (cut colour, stroke width, label mode)? "
        f"Save lasting values in {DEFAULTS_FILENAME} to skip this",
        False,
    )
    if customize:
        cut_color = ask("  Cut colour (hex)", options.cut_color)
        while True:
            stroke_raw = ask("  Cut stroke ('hairline' or a width in px)", str(options.cut_stroke))
            if stroke_raw.lower() == "hairline":
                cut_stroke: Any = "hairline"
                break
            try:
                cut_stroke = float(stroke_raw)
                break
            except ValueError:
                print("  Enter 'hairline' or a number (px).")
        label_mode = ask_choice("  Label mode", ["raster", "outline"], options.label_mode)
        if label_mode == "outline":
            outline_color = ask("  Label outline colour (hex)", options.label_outline_color)
            options = replace(
                options,
                cut_color=cut_color,
                cut_stroke=cut_stroke,
                label_mode=label_mode,
                label_outline_color=outline_color,
            )
        else:
            label_color = ask("  Label colour (hex)", options.label_color)
            options = replace(
                options,
                cut_color=cut_color,
                cut_stroke=cut_stroke,
                label_mode=label_mode,
                label_color=label_color,
            )

    output = Path(ask("Output SVG name/path", "laser_nest.svg")).expanduser().resolve()

    return JobSpec(
        sheet=sheet,
        requests=requests,
        sample_step=sample_step,
        seed=seed,
        output=output,
        options=options,
        weld_distance=float(defaults.get("weld_distance", DEFAULT_WELD_IN)),
    )


def print_job(sheet: SheetSpec, requests: Sequence[PartRequest]) -> None:
    print("\nJob")
    print("---")
    cap = "unlimited" if sheet.max_sheets is None else str(sheet.max_sheets)
    print(
        f"Sheet: {sheet.width:g} × {sheet.height:g} in | "
        f"grain={sheet.grain_axis.upper()} | margin={sheet.margin:g} in | "
        f"spacing={sheet.spacing:g} in | mirror={'on' if sheet.allow_mirror else 'off'} | "
        f"hole-nesting={'on' if sheet.allow_nesting_in_holes else 'off'} | "
        f"max_sheets={cap}"
    )
    for req in requests:
        rotations = f" custom rotations={list(req.rotations)}" if req.rotations is not None else ""
        print(
            f"  {req.quantity:>3} × {req.file.name} | grain={req.grain} | "
            f"part-axis={req.grain_angle_deg:g}°{rotations}"
        )


def print_loaded_part(part: LoadedPart, sheet: SheetSpec) -> None:
    rotations = allowed_rotations(part, sheet)
    print(
        f"  {part.display_name}: "
        f"{part.base_width_in:.4f} × {part.base_height_in:.4f} in, "
        f"allowed rotations={','.join(f'{a:g}°' for a in rotations)}"
    )
    for note in part.notes:
        print(f"    NOTE: {note}")


def run_job(job: JobSpec) -> list[Path]:
    sheet = job.sheet
    print_job(sheet, job.requests)

    print("\nLoading and validating SVG geometry...")
    parts: list[LoadedPart] = []
    for req in job.requests:
        part = load_part(req, job.sample_step, job.weld_distance)
        parts.append(part)
        print_loaded_part(part, sheet)

    items = make_items(parts, sheet)

    for warning in preflight_capacity(parts, items, sheet):
        print(f"\nWARNING: {warning}")

    print(f"\nNesting {len(items)} physical parts with {sheet.passes} optimization passes...")
    result = optimize_layout(items, sheet, seed=job.seed)

    if result.unplaced:
        counts: dict[str, int] = {}
        for item in result.unplaced:
            counts[item.part.display_name] = counts.get(item.part.display_name, 0) + 1
        breakdown = ", ".join(f"{n}× {name}" for name, n in counts.items())
        message = (
            f"{len(result.unplaced)} of {len(items)} parts could not be placed "
            f"within the allowed {len(result.sheets)} sheet(s): {breakdown}. "
            "Increase max_sheets, use a larger sheet, or reduce the quantity."
        )
        if not job.allow_partial:
            raise BalsaNestError(message)
        print(f"\nWARNING: {message}\n(Continuing because partial output was allowed.)")

    print(f"Best layout: {len(result.sheets)} sheet(s), score={result.score}")
    for idx, layout in enumerate(result.sheets, start=1):
        placements = layout.placements
        line = f"  Sheet {idx}: {len(placements)} parts"
        if placements:
            xs0 = min(placement_bounds(p)[0] for p in placements)
            ys0 = min(placement_bounds(p)[1] for p in placements)
            xs1 = max(placement_bounds(p)[2] for p in placements)
            ys1 = max(placement_bounds(p)[3] for p in placements)
            line += f", footprint {xs1 - xs0:.2f}×{ys1 - ys0:.2f} in"
        print(line)

        nestings = detect_nestings(layout)
        for child, parent in nestings:
            print(f"    -> nested {child.item.uid} inside cut-out of {parent.item.uid}")
        if nestings:
            print(
                "      (verify those cut-outs are through-cut scrap, not engraved "
                "features, before cutting.)"
            )

    files, label_warnings = save_outputs(result, sheet, job.output, job.options)
    if label_warnings:
        print(
            "\nWARNING: no label engraved on part(s): "
            + ", ".join(sorted(label_warnings))
            + " (too small to hold a legible name inside the outline)."
        )

    print("\nGenerated:")
    for file in files:
        print(f"  {file}")

    print(
        "\nLayers in each SVG: red = cut, black = raster engrave (labels), "
        "blue dashed = debug only (delete before cutting)."
    )
    print(
        "Before cutting: open the generated SVG in Inkscape, verify a known "
        "dimension with the measurement tool, and confirm your laser software "
        "imports it at 1:1 scale."
    )
    return files


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Grain-aware automatic nesting of SVG parts on balsa sheets."
    )
    p.add_argument("--config", type=Path, help="JSON job config. If omitted, an interactive wizard is used.")
    p.add_argument("--output", type=Path, help="Override output path from config.")
    p.add_argument("--dry-run", action="store_true", help="Load/validate geometry but do not nest or write.")
    p.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Add the debug overlay layer: part bounding boxes, edge-margin band, "
            "scrap cut-outs, concave pockets, plus a colour legend (never cut)."
        ),
    )
    p.add_argument("--no-labels", action="store_true", help="Do not engrave part-name raster labels.")
    p.add_argument("--no-mirror", action="store_true", help="Disable mirrored/flipped part orientations.")
    p.add_argument("--no-hole-nesting", action="store_true", help="Do not place small parts inside scrap cut-outs.")
    p.add_argument("--max-sheets", type=int, help="Hard cap on the number of stock sheets. Overrides config.")
    p.add_argument("--allow-partial", action="store_true", help="Write whatever fits instead of erroring.")
    p.add_argument(
        "--weld",
        type=float,
        metavar="INCHES",
        help=(
            "Distance across which drawn lines count as the same cut, repairing "
            "exports whose every contour is drawn twice (3D exports of tapered "
            "parts). Default 0.02 in; 0 disables the repair."
        ),
    )
    return p


def apply_overrides(job: JobSpec, args: argparse.Namespace) -> JobSpec:
    if args.output:
        job.output = args.output.expanduser().resolve()
    if args.allow_partial:
        job.allow_partial = True
    if args.weld is not None:
        if args.weld < 0:
            raise BalsaNestError("--weld must be >= 0.")
        job.weld_distance = args.weld

    sheet_overrides: dict[str, Any] = {}
    if args.max_sheets is not None:
        sheet_overrides["max_sheets"] = args.max_sheets
    if args.no_mirror:
        sheet_overrides["allow_mirror"] = False
    if args.no_hole_nesting:
        sheet_overrides["allow_nesting_in_holes"] = False
    if sheet_overrides:
        job.sheet = replace(job.sheet, **sheet_overrides)
        job.sheet.validate()

    option_overrides: dict[str, Any] = {}
    if args.no_labels:
        option_overrides["label_parts"] = False
    if args.debug:
        option_overrides["debug_borders"] = True
    if option_overrides:
        job.options = replace(job.options, **option_overrides)
    return job


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        if args.config:
            job = load_config(args.config.expanduser().resolve())
        else:
            job = interactive_specs()

        job = apply_overrides(job, args)

        if args.dry_run:
            print_job(job.sheet, job.requests)
            print("\nValidating SVG geometry...")
            parts = []
            for req in job.requests:
                part = load_part(req, job.sample_step, job.weld_distance)
                parts.append(part)
                print_loaded_part(part, job.sheet)
            for warning in preflight_capacity(parts, make_items(parts, job.sheet), job.sheet):
                print(f"\nWARNING: {warning}")
            print("\nDry run successful.")
            return 0

        run_job(job)
        return 0

    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except BalsaNestError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"\nUNEXPECTED ERROR: {type(exc).__name__}: {exc}\n"
            "If this is caused by an SVG that BalsaNest should support, keep the "
            "source file and traceback for debugging.",
            file=sys.stderr,
        )
        raise

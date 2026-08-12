from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

import gradio as gr

from core import (
    BalsaNestError,
    OutputOptions,
    PartRequest,
    SheetSpec,
    ga_generations,
    heuristic_passes,
    layout_summary,
    load_part,
    load_sheet_boundary,
    make_items,
    polish_layout,
    preflight_capacity,
    save_outputs,
    save_scrap_outlines,
)
from core.holes import detect_nestings

from .fonts import label_width_ratio
from .previews import empty_sheet_viz, file_url, sheets_html


def messages_html(messages: list[str]) -> str:
    """Warnings in red and notes in amber, so they stand out from the page."""
    import html as html_mod

    if not messages:
        return ""
    rows = []
    for m in messages:
        if m.startswith("WARNING"):
            color = "#ff7b72"
        elif m.startswith("NOTE"):
            color = "#ffb454"
        else:
            color = "var(--body-text-color)"
        rows.append(
            f'<div style="color:{color};font-size:14px;line-height:1.45;'
            f'margin:3px 0">&bull; {html_mod.escape(m)}</div>'
        )
    return "".join(rows)


def print_buttons_html(files: list) -> str:
    """One Print button per cut sheet: opens the SVG and fires the browser
    print dialog, so a machine driven by a print-driver laser can cut straight
    from the browser without the Inkscape stop."""
    sheets = [
        Path(f) for f in files
        if str(f).endswith(".svg") and "scrap" not in Path(f).name
    ]
    if not sheets:
        return ""
    buttons = "".join(
        '<button class="print-btn" onclick="'
        "var w=window.open('" + file_url(f) + "');"
        "if(w){w.addEventListener('load',function(){w.print();});}\">"
        f"Print {f.name}</button>"
        for f in sheets
    )
    return (
        '<div class="print-row">' + buttons + "</div>"
        '<div class="print-note">Make sure in the print dialog set the scale '
        "to 100% and turn off any fit to page option.</div>"
    )


def build_viz(result, sheet, options, out_dir: Path, view: Optional[str] = None):
    """Browser-preview copies of a layout: thick high-contrast strokes
    (downloadable files keep the real laser strokes). Rulers are added as
    browser-side HTML around the image, not baked into the SVG.

    ``view`` limits rendering to just "clean" or "debug" -- used by streamed
    interim updates so each frame costs one SVG write, not two."""
    import uuid

    summary = layout_summary(result, sheet)
    viz_opts = replace(options, cut_stroke=2.5, draw_boundary=True)
    # Fresh paths every render: interim streamed frames and the final render
    # would otherwise reuse the same file URL, and the browser can keep
    # serving a stale cached image for one of the two views.
    tag = uuid.uuid4().hex[:8]
    viz: dict = {}
    if view in (None, "clean"):
        # The clean preview never shows the overlay, regardless of the
        # "include the debug overlay in the downloaded file" option.
        clean_files, _ = save_outputs(
            result, sheet, out_dir / f"preview_{tag}" / "nest.svg",
            replace(viz_opts, debug_borders=False),
        )
        viz["clean"] = sheets_html(clean_files, summary)
    if view in (None, "debug"):
        dbg_files, _ = save_outputs(
            result, sheet, out_dir / f"preview_dbg_{tag}" / "nest.svg",
            replace(viz_opts, debug_borders=True),
        )
        viz["debug"] = sheets_html(dbg_files, summary)
    return summary, viz


def run_nest(
    parts: list[dict],
    sheet_w: float,
    sheet_h: float,
    grain_axis: str,
    margin: float,
    spacing: float,
    max_sheets: float,
    outline: Optional[dict],
    optimizer: str,
    passes: float,
    allow_mirror: bool,
    allow_holes: bool,
    allow_partial: bool,
    grid_step: float,
    sample_step: float,
    seed: float,
    label_parts: bool,
    export_unlabeled: bool,
    export_scrap: bool,
    label_mode: str,
    label_font: str,
    label_color: str,
    outline_color: str,
    cut_color: str,
    cut_stroke_mode: str,
    stroke_px: float,
    stroke_in: float,
    kerf: float,
    merge_cuts: bool,
    debug_overlay: bool,
    debug_view: bool,
    stop_flag: Optional[dict] = None,
):
    usable = [p for p in parts if not p["error"]]
    if not usable:
        raise gr.Error("Upload at least one valid part file (SVG, DXF, or PDF).")

    messages: list[str] = []
    try:
        boundary = None
        if outline:
            if outline.get("kind") == "file":
                boundary, sheet_w, sheet_h = load_sheet_boundary(
                    Path(outline["path"]), float(sample_step)
                )
            else:
                from shapely import wkt as shp_wkt

                boundary = shp_wkt.loads(outline["wkt"])
                sheet_w, sheet_h = outline["w"], outline["h"]
            messages.append(
                f"NOTE: using a custom sheet outline on a "
                f"{float(sheet_w):.2f} x {float(sheet_h):.2f} in sheet; "
                f"parts are only placed inside it."
            )

        sheet = SheetSpec(
            width=float(sheet_w),
            height=float(sheet_h),
            grain_axis=grain_axis,
            margin=float(margin),
            spacing=float(spacing),
            grid_step=float(grid_step),
            passes=int(passes),
            max_sheets=None if int(max_sheets) <= 0 else int(max_sheets),
            allow_mirror=bool(allow_mirror),
            allow_nesting_in_holes=bool(allow_holes),
            boundary=boundary,
        )
        sheet.validate()

        if cut_stroke_mode.lower().startswith("hairline"):
            cut_stroke: Any = "hairline"
        elif "(in)" in cut_stroke_mode:
            cut_stroke = float(stroke_in) * 96.0  # inches -> output px
        else:
            cut_stroke = float(stroke_px)
        options = OutputOptions(
            label_parts=bool(label_parts),
            label_mode=label_mode,
            label_font=label_font,
            label_font_ratio=label_width_ratio(label_font),
            label_color=label_color,
            label_outline_color=outline_color,
            cut_color=cut_color,
            cut_stroke=cut_stroke,
            kerf_in=float(kerf),
            merge_common_cuts=bool(merge_cuts),
            debug_borders=bool(debug_overlay),
        )
        if float(kerf) > 0 and float(spacing) < float(kerf):
            messages.append(
                "WARNING: the part spacing is smaller than the kerf, so "
                "neighbouring cut lines will overlap after compensation. "
                "Set the spacing to at least the kerf value."
            )
        if merge_cuts:
            if float(kerf) > 0 and abs(float(spacing) - float(kerf)) > 1e-9:
                messages.append(
                    "NOTE: with kerf compensation, common cut lines only "
                    "coincide when the part spacing equals the kerf. Set the "
                    "spacing to the kerf value to merge shared edges."
                )
            elif float(kerf) == 0 and float(spacing) > 0:
                messages.append(
                    "NOTE: merging common cut lines only has an effect when "
                    "the part spacing is 0. With a gap between parts no lines "
                    "coincide."
                )
        if bool(label_parts) and label_font != "sans-serif":
            messages.append(
                f"NOTE: labels use the font {label_font}. A computer that "
                "opens the SVG without that font installed will substitute a "
                "different one, which can change how the labels fit. Keep the "
                "generic sans-serif font if the file must work everywhere."
            )

        requests = []
        for p in usable:
            requests.append(
                PartRequest(
                    file=Path(p["path"]),
                    quantity=int(p["qty"]),
                    grain=p["grain"],
                    grain_angle_deg=float(p["angle"]),
                    name=p["name"],
                    units=None if p["units"] == "auto" else p["units"],
                )
            )
            requests[-1].validate()

        yield (
            gr.update(), "*Loading part geometry...*",
            gr.update(), gr.update(), gr.update(),
            gr.update(visible=False),  # hide print buttons from earlier runs
            gr.update(visible=False),  # hide the summary download too
        )
        loaded = []
        for req in requests:
            part = load_part(req, float(sample_step))
            loaded.append(part)
            for note in part.notes:
                messages.append(f"NOTE ({part.display_name}): {note}")

        items = make_items(loaded, sheet)
        for warning in preflight_capacity(loaded, items, sheet):
            messages.append(f"WARNING: {warning}")

        use_ga = optimizer.lower().startswith("genetic")
        out_dir = Path(tempfile.mkdtemp(prefix="balsanest_web_"))
        flags = stop_flag if isinstance(stop_flag, dict) else {}
        flags["stop"] = False
        flags["debug"] = bool(debug_view)  # updated live by the debug toggle

        def live_view(viz: dict) -> str:
            key = "debug" if flags.get("debug") else "clean"
            body = viz.get(key) or viz.get("clean") or viz.get("debug") or ""
            return viz.get("banner", "") + body

        def banner_html(text: str) -> str:
            return f'<div class="evolve-banner">{text}</div>'

        # Shown under early-status banners so the canvas never disappears.
        blank_canvas_html = empty_sheet_viz(boundary, sheet.width, sheet.height)

        def status(text: str):
            return (
                banner_html(text) + blank_canvas_html,
                messages_html(messages),
                gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(),
            )

        def step_update(best, banner_text):
            # Interim frames: render only the active view, without labels --
            # a fraction of the cost of the final render, so streaming stays
            # snappy on large jobs.
            view = "debug" if flags.get("debug") else "clean"
            interim_opts = replace(options, label_parts=False)
            summary_g, viz_g = build_viz(best, sheet, interim_opts, out_dir, view=view)
            utils = [
                s["approx_part_area_utilization_percent"] for s in summary_g["sheets"]
            ]
            viz_g["banner"] = banner_html(
                f"{banner_text} &middot; {len(best.sheets)} sheet(s) &middot; "
                f"~{max(utils) if utils else 0:.1f}% material used"
            )
            return (
                live_view(viz_g),
                messages_html(messages),
                gr.update(),
                gr.update(),
                viz_g,
                gr.update(),
                gr.update(),
            )

        if use_ga and len(items) > 2:
            # Live evolution: show every generation's best layout until the
            # user presses "Stop evolving" (the generation cap as a backstop).
            gen_iter = ga_generations(items, sheet, seed=int(seed))
            best = None
            gen = 0
            max_gens = max(1, int(passes))
            yield status(
                "EVOLVING &mdash; evaluating the starting population (several "
                "complete layouts; this first step takes the longest)..."
            )
            while gen < max_gens:
                best = next(gen_iter)
                gen += 1
                yield step_update(
                    best,
                    f"EVOLVING &mdash; generation {gen} / {max_gens} &middot; "
                    f"press <b>Stop evolving</b> to keep this layout",
                )
                if flags.get("stop"):
                    break
            yield (
                gr.update(), "*Polishing the best layout...*",
                gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(),
            )
            result = polish_layout(best, sheet)
            messages.append(f"Genetic algorithm ran {gen} generation(s).")
        else:
            if use_ga:
                messages.append(
                    "NOTE: with 2 or fewer parts there is nothing to evolve -- "
                    "used the heuristic optimizer instead."
                )
            total = max(1, int(sheet.passes))
            best = None
            yield status(f"OPTIMIZING &mdash; computing pass 1 / {total}...")
            for i, best in heuristic_passes(items, sheet, seed=int(seed)):
                yield step_update(
                    best, f"OPTIMIZING &mdash; pass {i + 1} / {total}"
                )
            yield (
                gr.update(), "*Polishing the best layout...*",
                gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(),
            )
            result = polish_layout(best, sheet)

        if result.unplaced:
            counts: dict[str, int] = {}
            for item in result.unplaced:
                counts[item.part.display_name] = counts.get(item.part.display_name, 0) + 1
            breakdown = ", ".join(f"{n}x {name}" for name, n in counts.items())
            msg = (
                f"{len(result.unplaced)} of {len(items)} parts could not be placed: "
                f"{breakdown}. Increase max sheets, use a larger sheet, or reduce quantity."
            )
            if not allow_partial:
                raise gr.Error(msg)
            messages.append(f"WARNING: {msg}")

        if float(kerf) > 0:
            from shapely.geometry import Polygon as ShpPolygon

            def feature_counts(geom) -> tuple[int, int]:
                polys = (
                    [geom]
                    if isinstance(geom, ShpPolygon)
                    else [
                        g
                        for g in getattr(geom, "geoms", [])
                        if isinstance(g, ShpPolygon) and not g.is_empty
                    ]
                )
                return len(polys), sum(len(p.interiors) for p in polys)

            damaged = set()
            for layout in result.sheets:
                for p in layout.placements:
                    before = feature_counts(p.geometry)
                    after = feature_counts(p.geometry.buffer(float(kerf) / 2.0))
                    if after[0] != before[0] or after[1] < before[1]:
                        damaged.add(p.item.part.display_name)
            if damaged:
                messages.append(
                    "WARNING: kerf compensation closed or merged features "
                    "narrower than the kerf on: "
                    + ", ".join(sorted(damaged))
                    + ". Holes or slots thinner than the kerf cannot be cut "
                    "and their cut lines were dropped."
                )

        files, label_warnings = save_outputs(result, sheet, out_dir / "nest.svg", options)
        if bool(label_parts) and bool(export_unlabeled):
            # Cut-only twins of every sheet, for re-cutting a part without
            # sitting through the engraving pass again. The summary JSON is
            # identical, so only the SVGs are added.
            plain_files, _ = save_outputs(
                result, sheet, out_dir / "nest_cuts_only.svg",
                replace(options, label_parts=False),
            )
            files = files + [f for f in plain_files if f.suffix == ".svg"]
        if bool(export_scrap):
            scrap_files = save_scrap_outlines(result, sheet, out_dir / "nest_scrap.svg")
            if scrap_files:
                files = files + scrap_files
        if label_warnings:
            messages.append(
                "WARNING: no label engraved on: "
                + ", ".join(sorted(label_warnings))
                + " (too small to hold a legible name)."
            )
        for layout in result.sheets:
            for child, parent in detect_nestings(layout):
                messages.append(
                    f"NOTE: nested {child.item.uid} inside a cut-out of {parent.item.uid} "
                    f"-- verify that cut-out is through-cut scrap before cutting."
                )

        summary, viz = build_viz(result, sheet, options, out_dir)

        summary_path = next(
            (f for f in files if str(f).endswith(".json")), None
        )
        panel_files = [str(f) for f in files if not str(f).endswith(".json")]
        yield (
            live_view(viz), messages_html(messages),
            panel_files, summary, viz,
            gr.update(value=print_buttons_html(files), visible=True),
            gr.update(value=str(summary_path), visible=True)
            if summary_path else gr.update(visible=False),
        )

    except gr.Error:
        raise
    except BalsaNestError as exc:
        raise gr.Error(str(exc)) from exc
    except Exception as exc:
        raise gr.Error(f"Unexpected error: {type(exc).__name__}: {exc}") from exc

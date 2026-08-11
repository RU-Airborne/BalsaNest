from __future__ import annotations

from pathlib import Path

import gradio as gr

from core import load_defaults, output_options_from_config

from .assets import (
    GRAIN_RULE_HTML,
    LABEL_STYLE_HTML,
    OR_DIVIDER_HTML,
    SHEET_GRAIN_HTML,
)
from .nesting import run_nest
from .parts import reload_with_units, sync_parts
from .previews import empty_sheet_viz, grid_canvas_file
from .sheets import set_outline_from_drawing, set_outline_from_file


def build_ui() -> gr.Blocks:
    defaults = load_defaults(Path.cwd())
    d_sheet = defaults.get("sheet", {})
    d_opts = output_options_from_config(defaults)

    with gr.Blocks(title="BalsaNest") as demo:
        parts_state = gr.State([])
        viz_state = gr.State({})
        outline_state = gr.State(None)
        stop_state = gr.State({})

        # ---------------- top: visualizer ----------------
        with gr.Row(elem_id="header-row"):
            gr.Markdown("# BalsaNest\nAll dimensions are in **inches**.")
            stop_ga_btn = gr.Button(
                "Stop evolving", size="md", scale=0, min_width=140,
                variant="stop", visible=False,
            )
            nest_btn = gr.Button(
                "Start", variant="primary", scale=0, size="md", min_width=160
            )

        gr.Markdown("## Visualizer", elem_classes=["section-title"])
        result_html = gr.HTML(
            value=empty_sheet_viz(
                None,
                float(d_sheet.get("width", 32.0)),
                float(d_sheet.get("height", 18.0)),
            ),
            label="Nested sheets",
            elem_id="visualizer",
        )
        viz_debug = gr.Checkbox(
            value=True,
            label="Show the debug overlay in the preview above",
            info="Draws each part's bounding box, the edge margin, scrap cutouts "
            "and pockets that can hold smaller parts.",
        )
        messages_md = gr.Markdown()
        gr.Markdown("## Output", elem_classes=["section-title"])
        with gr.Row():
            files_out = gr.Files(
                label="Download your laser-ready files (SVG sheets + JSON summary)",
                scale=1,
            )
            with gr.Column(scale=1):
                with gr.Accordion("Layout summary json", open=False):
                    summary_json = gr.JSON()

        # ---------------- middle: settings, three columns ----------------
        gr.Markdown("## Settings", elem_classes=["section-title"])
        with gr.Row(equal_height=False):
            with gr.Column():
                with gr.Accordion("Sheet of material", open=True):
                    with gr.Row():
                        sheet_w = gr.Number(
                            value=float(d_sheet.get("width", 32.0)),
                            label="Width (in)", minimum=0.1,
                        )
                        sheet_h = gr.Number(
                            value=float(d_sheet.get("height", 18.0)),
                            label="Height (in)", minimum=0.1,
                        )
                    with gr.Accordion("Custom sheet shape (optional)", open=True):
                        gr.Markdown(
                            "For non-rectangular stock: draw the shape by hand, "
                            "or upload an outline drawing. Parts are then only "
                            "placed inside that outline.",
                        )
                        draw_open_btn = gr.Button(
                            "Draw the sheet shape...", size="sm", variant="primary"
                        )
                        gr.HTML(OR_DIVIDER_HTML, padding=False)
                        with gr.Group():
                            outline_file = gr.File(
                                label="Upload an outline drawing (.svg / .dxf / .pdf)",
                                file_count="single",
                                file_types=[".svg", ".dxf", ".pdf"],
                                type="filepath",
                            )
                            outline_html = gr.HTML("", padding=False)
                        clear_outline_btn = gr.Button(
                            "Clear custom shape", size="sm"
                        )
                    with gr.Group():
                        grain_axis = gr.Radio(
                            ["x", "y"], value=str(d_sheet.get("grain_axis", "x")).lower(),
                            label="Grain direction of the sheet",
                            info="The direction the wood grain runs across the sheet.",
                        )
                        gr.HTML(SHEET_GRAIN_HTML, padding=False)
                    max_sheets = gr.Number(
                        value=0, label="Maximum number of sheets", precision=0,
                        info="Stop with an error if the parts do not fit on this "
                        "many sheets. Leave at 0 to add as many sheets as needed.",
                    )
                    margin = gr.Slider(
                        0.0, 1.0, value=float(d_sheet.get("margin", 0.05)),
                        step=0.01, label="Edge margin (in)",
                        info="A border around the sheet edge where no parts are "
                        "placed.",
                    )
                    spacing = gr.Slider(
                        0.0, 0.5, value=float(d_sheet.get("spacing", 0.04)),
                        step=0.01, label="Spacing between parts (in)",
                        info="The minimum gap kept between neighbouring parts.",
                    )

            with gr.Column():
                with gr.Accordion("Nesting algorithm settings", open=True):
                    optimizer = gr.Radio(
                        ["Heuristic optimization (fast)", "Genetic algorithm (slow but tighter)"],
                        value="Heuristic optimization (fast)",
                        label="Optimizer",
                        info="The heuristic tries several sensible packing orders "
                        "and keeps the best -- done in seconds. The genetic "
                        "algorithm keeps breeding better layouts and shows each "
                        "generation live in the preview until you press 'Stop "
                        "evolving' next to the Nest button.",
                    )
                    passes = gr.Slider(
                        1, 24, value=int(d_sheet.get("passes", 8)), step=1,
                        label="Optimization passes",
                        info="How many different layouts the heuristic optimizer "
                        "tries before keeping the best one. The genetic algorithm "
                        "ignores this and runs until stopped.",
                    )
                    allow_mirror = gr.Checkbox(
                        value=bool(d_sheet.get("allow_mirror", True)),
                        label="Allow parts to be flipped (mirrored)",
                        info="Lets a part be turned over to its mirror image when "
                        "that packs tighter. Turn this off if your parts have a "
                        "front face that must stay up.",
                    )
                    allow_holes = gr.Checkbox(
                        value=bool(d_sheet.get("allow_nesting_in_holes", True)),
                        label="Nest small parts inside cut-outs",
                        info="Places small parts inside the scrap area of bigger "
                        "parts' holes and cut-outs to save material.",
                    )
                    allow_partial = gr.Checkbox(
                        value=False,
                        label="Keep a partial result if not everything fits",
                        info="If some parts cannot fit, still produce the sheets "
                        "that do fit instead of stopping with an error.",
                    )
                    with gr.Accordion("Advanced finetuning", open=False):
                        grid_step = gr.Slider(
                            0.01, 0.25, value=float(d_sheet.get("grid_step", 0.04)),
                            step=0.01, label="Fallback grid step (in)",
                            info="Backup search grid spacing. Smaller = more "
                            "thorough, slower.",
                        )
                        sample_step = gr.Slider(
                            0.005, 0.05,
                            value=float(defaults.get("sample_step", 0.015)),
                            step=0.005, label="Curve sampling step (in)",
                            info="How finely curves are approximated for overlap "
                            "checks. Smaller = more accurate, slower.",
                        )
                        seed = gr.Number(
                            value=int(defaults.get("seed", 42)),
                            label="Random seed", precision=0,
                            info="Change to explore different random layouts; the "
                            "same seed always gives the same result.",
                        )

            with gr.Column():
                with gr.Accordion("Output & laser settings", open=True):
                    label_parts_cb = gr.Checkbox(
                        value=d_opts.label_parts,
                        label="Engrave each part's name on it",
                        info="Engraves the part's file name onto the part itself. "
                        "To keep engraving fast, labels are always horizontal and "
                        "slide onto shared rows with nearby labels, so the laser "
                        "makes fewer slow vertical moves; labels on opposite ends "
                        "of the sheet are kept on separate rows so the head never "
                        "sweeps the whole board, and long names wrap onto "
                        "multiple lines.",
                    )
                    debug_overlay = gr.Checkbox(
                        value=False,
                        label="Include the debug overlay in the downloaded file",
                        info="Adds the same reference overlay as the preview toggle "
                        "to the downloaded SVG, on its own layer. Delete that layer "
                        "before actually cutting.",
                    )
                    with gr.Group():
                        label_mode = gr.Radio(
                            ["raster", "outline"], value=d_opts.label_mode,
                            label="Label engraving style",
                            info="How the part names are engraved.",
                        )
                        gr.HTML(LABEL_STYLE_HTML, padding=False)
                    with gr.Row():
                        cut_color = gr.ColorPicker(
                            value=d_opts.cut_color, label="Cut colour",
                            info="Colour of the cut lines.",
                        )
                        label_color = gr.ColorPicker(
                            value=d_opts.label_color, label="Raster colour",
                            info="Colour of the raster-engraved name labels.",
                        )
                        outline_color = gr.ColorPicker(
                            value=d_opts.label_outline_color,
                            label="Outline colour",
                            info="Label colour when the engraving style is "
                            "outline.",
                        )
                    merge_cuts = gr.Checkbox(
                        value=d_opts.merge_common_cuts,
                        label="Merge common cut lines",
                        info="When two parts touch (set the part spacing to 0), an "
                        "edge they share is cut once instead of twice, saving "
                        "cutting time. Only exactly overlapping lines are merged.",
                    )
                    default_hairline = d_opts.cut_stroke == "hairline"
                    cut_stroke_mode = gr.Radio(
                        ["Hairline", "Fixed width (px)", "Fixed width (in)"],
                        value="Hairline" if default_hairline else "Fixed width (px)",
                        label="Cut line style",
                    )
                    stroke_px = gr.Slider(
                        0.1, 3.0,
                        value=1.0 if default_hairline else float(d_opts.cut_stroke),
                        step=0.1, label="Cut line width (px)",
                        info="The line width used for the cut lines. Some laser "
                        "software ignores line width entirely.",
                        visible=not default_hairline,
                    )
                    stroke_in = gr.Slider(
                        0.001, 0.05, value=0.005, step=0.001,
                        label="Cut line width (in)",
                        info="The line width used for the cut lines, in inches.",
                        visible=False,
                    )
                    cut_stroke_mode.change(
                        lambda m: (
                            gr.update(visible=m == "Fixed width (px)"),
                            gr.update(visible=m == "Fixed width (in)"),
                        ),
                        cut_stroke_mode, [stroke_px, stroke_in],
                        show_progress="hidden",
                    )

        # Floating drawing window: its contents are rendered on demand, so the
        # canvas always mounts while visible (a canvas created inside a hidden
        # container stays blank).
        draw_open = gr.State(False)

        @gr.render(inputs=[draw_open, sheet_w, sheet_h], triggers=[draw_open.change])
        def render_draw_modal(open_, w, h):
            if not open_:
                return
            w = max(float(w or 1.0), 0.5)
            h = max(float(h or 1.0), 0.5)
            cw = 880
            ch = int(round(cw * h / w))
            if ch > 540:
                ch = 540
                cw = int(round(ch * w / h))
            with gr.Group(elem_id="draw-modal"):
                with gr.Row(elem_id="draw-modal-head"):
                    gr.Markdown(
                        f"### Draw the sheet shape\nSelect the **draw tool** and "
                        f"paint the usable material -- whatever you paint becomes "
                        f"the sheet (any colour that stands out from the white "
                        f"paper). The canvas is **{w:g} x {h:g} in** with a "
                        f"1-inch grid; the numbers along the top and left edges "
                        f"count inches from the top-left corner."
                    )
                    close_btn = gr.Button("Close", size="sm", scale=0, min_width=90)
                editor = gr.Sketchpad(
                    label=f"Sheet canvas ({w:g} x {h:g} in -- painted = usable material)",
                    type="numpy",
                    canvas_size=(cw, ch),
                    value=grid_canvas_file(w, h, cw, ch),
                    brush=gr.Brush(default_size=40, default_color="#c9a06c",
                                   colors=["#c9a06c"]),
                    interactive=True,
                )
                use_btn = gr.Button(
                    "Use drawing as sheet shape", variant="primary", size="sm"
                )

            close_btn.click(lambda: False, None, draw_open, show_progress="hidden")
            # The trash button wipes the grid background; restore it so the
            # user can immediately redraw.
            editor.clear(
                lambda: grid_canvas_file(w, h, cw, ch),
                None, editor, show_progress="hidden",
            )
            use_btn.click(
                set_outline_from_drawing,
                [editor, sheet_w, sheet_h],
                [outline_state, outline_html, sheet_w, sheet_h, draw_open, result_html],
                show_progress="minimal",
            )

        # ---------------- bottom: upload + part cards ----------------
        gr.Markdown("## Parts", elem_classes=["section-title"])
        files = gr.File(
            label="Drop your part drawings here (.svg, .dxf, or .pdf)",
            file_count="multiple",
            file_types=[".svg", ".dxf", ".pdf"],
            type="filepath",
        )

        @gr.render(inputs=parts_state)
        def render_parts(parts):
            if not parts:
                return

            gr.HTML(GRAIN_RULE_HTML)

            def _set(field, i):
                def fn(value, parts):
                    parts = [dict(x) for x in parts]
                    parts[i][field] = value
                    return parts
                return fn

            def _set_units(i):
                def fn(value, parts):
                    parts = [dict(x) for x in parts]
                    parts[i]["units"] = value
                    return reload_with_units(parts, i)
                return fn

            for start in range(0, len(parts), 2):
                with gr.Row(equal_height=True):
                    for idx in range(start, min(start + 2, len(parts))):
                        p = parts[idx]
                        with gr.Column():
                            with gr.Group(elem_classes=["part-card"]):
                                with gr.Row():
                                    if p["preview"]:
                                        gr.HTML(
                                            f'<img src="{p["preview"]}" style="width:130px;'
                                            f'height:130px;object-fit:contain;background:#fff;'
                                            f'border-radius:8px"/>',
                                            elem_classes=["part-thumb"],
                                            min_width=140,
                                        )
                                    with gr.Column():
                                        if p["error"]:
                                            gr.Markdown(
                                                f"**{p['name']}**\n\n"
                                                f"<span style='color:#f66'>ERROR: {p['error']}</span>"
                                            )
                                            continue
                                        gr.Markdown(
                                            f"**{p['name']}** &nbsp;&mdash;&nbsp; "
                                            f"{p['width']:.3f} &times; {p['height']:.3f} in"
                                            + "".join(
                                                f"\n\n<span style='color:#fa3'>NOTE: {n}</span>"
                                                for n in p["notes"]
                                            )
                                        )
                                        qty = gr.Number(
                                            value=p["qty"], minimum=1, precision=0,
                                            label="Quantity", interactive=True,
                                        )
                                        with gr.Row():
                                            grain = gr.Radio(
                                                ["parallel", "perpendicular", "free"],
                                                value=p["grain"],
                                                label="Grain alignment",
                                                interactive=True,
                                            )
                                            angle = gr.Number(
                                                value=p["angle"],
                                                label="Grain direction in the drawing (deg)",
                                                info="The direction the part's grain reference "
                                                "runs in your drawing file, in degrees. 0 means "
                                                "horizontal, which is the usual convention.",
                                                visible=p["grain"] != "free",
                                                interactive=True,
                                            )
                                            if p["suffix"] == ".dxf":
                                                units = gr.Dropdown(
                                                    ["auto", "in", "mm", "cm"],
                                                    value=p["units"],
                                                    label="Drawing units",
                                                    info="Only change this if the size shown "
                                                    "above looks wrong: pick the units the DXF "
                                                    "file was drawn in.",
                                                    interactive=True,
                                                )

                                if not p["error"]:
                                    qty.change(
                                        _set("qty", idx), [qty, parts_state], parts_state,
                                        show_progress="hidden",
                                    )
                                    grain.input(
                                        _set("grain", idx), [grain, parts_state], parts_state,
                                        show_progress="hidden",
                                    )
                                    angle.change(
                                        _set("angle", idx), [angle, parts_state], parts_state,
                                        show_progress="hidden",
                                    )
                                    if p["suffix"] == ".dxf":
                                        units.input(
                                            _set_units(idx), [units, parts_state], parts_state,
                                            show_progress="hidden",
                                        )

        files.change(
            sync_parts, [files, parts_state], [parts_state, files],
            show_progress="minimal",
        )

        outline_file.change(
            set_outline_from_file,
            [outline_file, sheet_w, sheet_h],
            [outline_state, outline_html, sheet_w, sheet_h, result_html],
            show_progress="minimal",
        )
        draw_open_btn.click(
            lambda: True, None, draw_open, show_progress="hidden"
        )
        clear_outline_btn.click(
            lambda w, h: (None, "", gr.update(value=None), empty_sheet_viz(None, w, h)),
            [sheet_w, sheet_h],
            [outline_state, outline_html, outline_file, result_html],
            show_progress="hidden",
        )

        # Keep the empty-canvas visualizer in sync with the sheet dimensions
        # (only while nothing has been nested and no custom shape is active).
        def refresh_empty_canvas(w, h, viz, outline):
            if viz or outline:
                return gr.update()
            return empty_sheet_viz(None, w, h)

        for dim in (sheet_w, sheet_h):
            dim.change(
                refresh_empty_canvas,
                [sheet_w, sheet_h, viz_state, outline_state],
                result_html,
                show_progress="hidden",
            )

        # "Stop evolving" only exists while a GA run is in flight.
        nest_btn.click(
            lambda o: gr.update(visible=o.lower().startswith("genetic")),
            optimizer, stop_ga_btn, show_progress="hidden",
        )
        nest_evt = nest_btn.click(
            run_nest,
            inputs=[
                parts_state,
                sheet_w, sheet_h, grain_axis, margin, spacing, max_sheets,
                outline_state, optimizer, passes, allow_mirror, allow_holes, allow_partial,
                grid_step, sample_step, seed,
                label_parts_cb, label_mode, label_color, outline_color,
                cut_color, cut_stroke_mode, stroke_px, stroke_in, merge_cuts,
                debug_overlay, viz_debug, stop_state,
            ],
            outputs=[result_html, messages_md, files_out, summary_json, viz_state],
            show_progress="hidden",
        )
        nest_evt.then(
            lambda: gr.update(visible=False), None, stop_ga_btn,
            show_progress="hidden",
        )

        def optimizer_ui(o, passes_val):
            ga = o.lower().startswith("genetic")
            if ga:
                return gr.update(
                    label="Maximum generations", maximum=40, value=20,
                    info="The genetic algorithm evolves until you press "
                    "'Stop evolving', or stops on its own after this many "
                    "generations.",
                )
            return gr.update(
                label="Optimization passes", maximum=24,
                value=min(int(passes_val), 24),
                info="How many different layouts the heuristic optimizer "
                "tries before keeping the best one.",
            )

        optimizer.change(
            optimizer_ui, [optimizer, passes], passes, show_progress="hidden"
        )

        def request_stop(flag):
            flag = flag if isinstance(flag, dict) else {}
            flag["stop"] = True
            return flag

        stop_ga_btn.click(
            request_stop, stop_state, stop_state, show_progress="hidden"
        )

        def pick_viz(viz, debug, flags):
            # Record the live preference so a running nest's next streamed
            # update keeps the same view (the toggle never fights the stream).
            if isinstance(flags, dict):
                flags["debug"] = bool(debug)
            if not viz:
                return gr.update()
            key = "debug" if debug else "clean"
            body = viz.get(key) or viz.get("clean") or viz.get("debug") or ""
            return viz.get("banner", "") + body

        viz_debug.change(
            pick_viz, [viz_state, viz_debug, stop_state], result_html,
            show_progress="hidden",
        )

    return demo

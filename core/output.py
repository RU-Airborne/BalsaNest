"""Write SVGsm and the JSON placement summary."""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from .constants import EPS, INKSCAPE_NS, PX_PER_INCH, SODIPODI_NS, SVG_NS
from .holes import (
    detect_nestings,
    placement_cavity_regions,
    placement_scrap_holes,
)
from .labels import LabelPlanner, LabelSpec
from .models import (
    LayoutResult,
    OutputOptions,
    Placement,
    SheetLayout,
    SheetSpec,
    placement_bounds,
)
from .svg_geometry import transform_path_for_placement


def safe_xml_id(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    if not text or not re.match(r"[A-Za-z_]", text[0]):
        text = "_" + text
    return text


class SvgSheetWriter:
    """Renders a single ``SheetLayout`` to an SVG file."""

    def __init__(self, options: OutputOptions) -> None:
        self.options = options
        self.label_planner = LabelPlanner(options)

    def _cut_stroke_style(self, color: str) -> str:
        """Stroke style for laser-relevant vector lines. 'hairline' emits an
        Inkscape hairline (~0.001 in wide) so print-driver laser workflows
        recognise the line as a cut. A number sets an explicit px width."""
        cs = self.options.cut_stroke
        if isinstance(cs, str) and cs.lower() == "hairline":
            # 0.096 px == 0.001 in at 96 px/in. -inkscape-stroke keeps it a
            # true hairline on screen and when printed from Inkscape.
            return (
                f"fill:none;stroke:{color};stroke-opacity:1;"
                f"stroke-width:0.096;-inkscape-stroke:hairline"
            )
        return f"fill:none;stroke:{color};stroke-opacity:1;stroke-width:{float(cs):g}"

    def _layer(self, root: ET.Element, label: str, layer_id: str) -> ET.Element:
        return ET.SubElement(
            root,
            f"{{{SVG_NS}}}g",
            {
                "id": safe_xml_id(layer_id),
                f"{{{INKSCAPE_NS}}}groupmode": "layer",
                f"{{{INKSCAPE_NS}}}label": label,
            },
        )

    def write(
        self,
        output_path: Path,
        layout: SheetLayout,
        sheet: SheetSpec,
        sheet_number: int,
        total_sheets: int,
    ) -> list[str]:
        """Write the sheet SVG and return any label warnings (parts left unlabelled)."""
        options = self.options
        width_px = sheet.width * PX_PER_INCH
        height_px = sheet.height * PX_PER_INCH

        root = ET.Element(
            f"{{{SVG_NS}}}svg",
            {
                "width": f"{sheet.width:.8g}in",
                "height": f"{sheet.height:.8g}in",
                "viewBox": f"0 0 {width_px:.8f} {height_px:.8f}",
                "version": "1.1",
            },
        )

        # Make Inkscape open the document showing inches at 1:1 scale, matching
        # the usual laser-shop document setup.
        ET.SubElement(
            root,
            f"{{{SODIPODI_NS}}}namedview",
            {
                "id": "namedview_balsanest",
                f"{{{INKSCAPE_NS}}}document-units": "in",
                "units": "in",
            },
        )

        metadata = ET.SubElement(root, f"{{{SVG_NS}}}metadata")
        metadata.text = json.dumps(
            {
                "generator": "BalsaNest",
                "sheet": {
                    "width_in": sheet.width,
                    "height_in": sheet.height,
                    "grain_axis": sheet.grain_axis,
                    "margin_in": sheet.margin,
                    "spacing_in": sheet.spacing,
                    "sheet_number": sheet_number,
                    "sheet_count": total_sheets,
                },
            },
            separators=(",", ":"),
        )

        if options.draw_boundary:
            # Preview renders only: usable material in wood colour, blocked
            # space in near-black. Laser exports never get a background.
            self._write_preview_background(root, sheet, width_px, height_px)

        warnings: list[str] = []
        label_pairs: dict[int, LabelSpec] = {}
        if options.label_parts:
            pairs, warnings = self.label_planner.plan_pairs(layout)
            label_pairs = {id(placement): spec for placement, spec in pairs}

        if options.group_labels_with_parts:
            # One group per part holding its cut paths AND its label, so
            # selecting/moving the part in Inkscape brings the label along.
            # Colours still separate operations for the laser software.
            self._write_parts_layer(root, layout, label_pairs)
        else:
            self._write_cut_layer(root, layout)
            if label_pairs:
                layer = self._layer(root, "Labels", "raster_labels")
                for placement in layout.placements:
                    spec = label_pairs.get(id(placement))
                    if spec is not None:
                        self._emit_label(layer, spec)

        if sheet.boundary is not None and options.draw_boundary:
            self._write_boundary_layer(root, sheet)

        if options.draw_rulers:
            self._write_rulers_layer(root, sheet, width_px, height_px)

        if options.debug_borders:
            self._write_debug_layer(root, layout, sheet, width_px, height_px)

        ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
        return warnings

    def _write_rulers_layer(
        self, root: ET.Element, sheet: SheetSpec, width_px: float, height_px: float
    ) -> None:
        """Inch rulers along the top and left edges: major tick + number every
        inch, minor tick every half inch. Reference only, never cut."""
        layer = self._layer(root, "Rulers (reference, do not cut)", "rulers")
        color = "#777777"
        major = 0.16 * PX_PER_INCH
        minor = 0.09 * PX_PER_INCH
        font_px = 0.11 * PX_PER_INCH

        segs: list[str] = []
        v = 0.5
        while v <= sheet.width + 1e-9:
            px = v * PX_PER_INCH
            length = major if abs(v - round(v)) < 1e-9 else minor
            segs.append(f"M {px:.2f},0 V {length:.2f}")
            v += 0.5
        v = 0.5
        while v <= sheet.height + 1e-9:
            py = v * PX_PER_INCH
            length = major if abs(v - round(v)) < 1e-9 else minor
            segs.append(f"M 0,{py:.2f} H {length:.2f}")
            v += 0.5
        segs.append(f"M 0,0 H {width_px:.2f}")
        segs.append(f"M 0,0 V {height_px:.2f}")
        ET.SubElement(
            layer,
            f"{{{SVG_NS}}}path",
            {
                "d": " ".join(segs),
                "fill": "none",
                "stroke": color,
                "stroke-width": "1",
                "vector-effect": "non-scaling-stroke",
            },
        )

        zero = ET.SubElement(
            layer,
            f"{{{SVG_NS}}}text",
            {
                "x": f"{major + 2.5:.2f}",
                "y": f"{major + font_px:.2f}",
                "font-size": f"{font_px:.2f}",
                "font-family": "sans-serif",
                "fill": color,
                "stroke": "none",
            },
        )
        zero.text = "0"
        for i in range(1, int(sheet.width + 1e-9) + 1):
            text = ET.SubElement(
                layer,
                f"{{{SVG_NS}}}text",
                {
                    "x": f"{i * PX_PER_INCH + 2.5:.2f}",
                    "y": f"{major + font_px:.2f}",
                    "font-size": f"{font_px:.2f}",
                    "font-family": "sans-serif",
                    "fill": color,
                    "stroke": "none",
                },
            )
            text.text = f"{i}"
        for i in range(1, int(sheet.height + 1e-9) + 1):
            text = ET.SubElement(
                layer,
                f"{{{SVG_NS}}}text",
                {
                    "x": f"{major + 2.5:.2f}",
                    "y": f"{i * PX_PER_INCH - 2.5:.2f}",
                    "font-size": f"{font_px:.2f}",
                    "font-family": "sans-serif",
                    "fill": color,
                    "stroke": "none",
                },
            )
            text.text = f"{i}"

    def _write_preview_background(
        self, root: ET.Element, sheet: SheetSpec, width_px: float, height_px: float
    ) -> None:
        """Preview-only background: the sheet's usable material drawn as wood,
        everything outside a custom shape (and inside its blocked holes) in
        near-black: available space reads at a glance."""
        layer = self._layer(root, "Preview background (not part of the job)", "preview_bg")
        ET.SubElement(
            layer,
            f"{{{SVG_NS}}}rect",
            {
                "x": "0", "y": "0",
                "width": f"{width_px:.2f}", "height": f"{height_px:.2f}",
                "fill": "#1c1c22",
            },
        )
        if sheet.boundary is not None:
            ET.SubElement(
                layer,
                f"{{{SVG_NS}}}path",
                {
                    "d": self._poly_to_path_px(sheet.boundary),
                    "fill": "#ecdcc0",
                    "fill-rule": "evenodd",
                    "stroke": "none",
                },
            )
        else:
            ET.SubElement(
                layer,
                f"{{{SVG_NS}}}rect",
                {
                    "x": "0", "y": "0",
                    "width": f"{width_px:.2f}", "height": f"{height_px:.2f}",
                    "fill": "#ecdcc0",
                },
            )

    def _write_boundary_layer(self, root: ET.Element, sheet: SheetSpec) -> None:
        """Reference outline of a non-rectangular stock sheet, dashed grey so
        no laser convention treats it as a cut. Delete the layer before cutting
        if the laser software objects to extra geometry."""
        layer = self._layer(
            root, "Sheet outline (reference, do not cut)", "sheet_outline"
        )
        ET.SubElement(
            layer,
            f"{{{SVG_NS}}}path",
            {
                "d": self._poly_to_path_px(sheet.boundary),
                "fill": "none",
                "stroke": "#888888",
                "stroke-width": "1",
                "stroke-dasharray": "5 4",
                "vector-effect": "non-scaling-stroke",
            },
        )

    # --- layers --------------------------------------------------------------

    # Coincidence quantum for common-line merging: 0.25 px ~ 0.0026 in.
    _MERGE_QUANTUM_PX = 0.25

    def _dedupe_common_segments(self, transformed: Any, seen: set) -> list[Any]:
        """Drop segments already emitted elsewhere on this sheet (common-line
        cutting), then regroup the survivors into continuous subpaths.

        A segment's identity is its quantized endpoints (direction-insensitive)
        plus its midpoint, so a straight line and a curve between the same two
        points never merge. Only exactly coincident segments merge. A long edge
        overlapping two shorter collinear edges is left untouched."""
        from svgpathtools import Path as SvgPath

        q = self._MERGE_QUANTUM_PX

        def qpt(z: complex) -> tuple[int, int]:
            return (round(z.real / q), round(z.imag / q))

        kept: list[Any] = []
        for seg in transformed:
            a, b = qpt(seg.start), qpt(seg.end)
            m = qpt(seg.point(0.5))
            key = (a, b, m) if a <= b else (b, a, m)
            if key in seen:
                continue
            seen.add(key)
            kept.append(seg)

        groups: list[Any] = []
        current: list[Any] = []
        for seg in kept:
            if current and abs(current[-1].end - seg.start) > 1e-6:
                groups.append(SvgPath(*current))
                current = []
            current.append(seg)
        if current:
            groups.append(SvgPath(*current))
        return groups

    @staticmethod
    def _bbox_area(path_obj: Any) -> float:
        xmin, xmax, ymin, ymax = path_obj.bbox()
        return (xmax - xmin) * (ymax - ymin)

    def _holes_first(self, transformed: Any) -> Any:
        """Reorder a path's subpaths smallest first. Interior holes are then cut
        before the outer boundary frees the part to shift."""
        from svgpathtools import Path as SvgPath

        try:
            subs = [s for s in transformed.continuous_subpaths() if len(s)]
        except Exception:
            return transformed
        if len(subs) <= 1:
            return transformed
        subs.sort(key=self._bbox_area)
        return SvgPath(*[seg for sub in subs for seg in sub])

    @staticmethod
    def _travel_order(placements: list[Any]) -> list[Any]:
        """Nearest neighbour tour over part centres starting at the sheet
        origin. Lasers that cut in document order waste less time moving
        the head between parts."""
        items = []
        for p in placements:
            minx, miny, maxx, maxy = p.geometry.bounds
            items.append(((0.5 * (minx + maxx), 0.5 * (miny + maxy)), p))
        ordered: list[Any] = []
        cur = (0.0, 0.0)
        while items:
            j = min(
                range(len(items)),
                key=lambda k: (items[k][0][0] - cur[0]) ** 2
                + (items[k][0][1] - cur[1]) ** 2,
            )
            center, placement = items.pop(j)
            ordered.append(placement)
            cur = center
        return ordered

    def _kerf_paths(
        self, group: ET.Element, placement: Placement, seen: Optional[set] = None
    ) -> None:
        """Kerf-compensated cut paths: the beam centreline is offset half a
        kerf outside the part (and inside its holes), so the finished piece
        matches the drawing. Drawn from the sampled outline. Curves are
        faceted at the curve sampling step.

        With ``seen`` (common-line merging), coincident offset segments are
        emitted once. Two neighbouring offset outlines coincide when the part
        spacing equals the kerf: each grows half a kerf into the gap and both
        beam centrelines land on the gap's midline."""
        from shapely.geometry import Polygon
        from svgpathtools import Line as SvgLine, Path as SvgPath

        half = 0.5 * float(self.options.kerf_in)
        geom = placement.geometry.buffer(half, join_style="round")
        polys = (
            [geom]
            if isinstance(geom, Polygon)
            else [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
        )
        rings = [
            r
            for poly in polys
            if not poly.is_empty
            for r in [*poly.interiors, poly.exterior]
        ]
        # Smallest enclosed area first: holes cut before their outline.
        rings.sort(key=lambda r: Polygon(r).area)
        style = self._cut_stroke_style(self.options.cut_color)
        for ring in rings:
            if seen is not None:
                pts = [
                    complex(x * PX_PER_INCH, y * PX_PER_INCH)
                    for x, y in ring.coords
                ]
                segs = [
                    SvgLine(a, b)
                    for a, b in zip(pts, pts[1:])
                    if abs(a - b) > 1e-9
                ]
                pieces = self._dedupe_common_segments(SvgPath(*segs), seen)
                for piece in pieces:
                    if len(piece) == 0:
                        continue
                    ET.SubElement(
                        group,
                        f"{{{SVG_NS}}}path",
                        {"d": piece.d(), "style": style},
                    )
                continue
            coords = [
                f"{x * PX_PER_INCH:.3f},{y * PX_PER_INCH:.3f}"
                for x, y in ring.coords
            ]
            ET.SubElement(
                group,
                f"{{{SVG_NS}}}path",
                {"d": "M " + " L ".join(coords) + " Z", "style": style},
            )

    def _part_group(
        self, parent: ET.Element, placement: Placement, seen: Optional[set] = None
    ) -> ET.Element:
        group = ET.SubElement(
            parent,
            f"{{{SVG_NS}}}g",
            {
                "id": safe_xml_id(placement.item.uid),
                "data-source": placement.item.part.request.file.name,
                "data-angle-deg": f"{placement.variant.angle_deg:.8g}",
                "data-mirrored": "1" if placement.variant.mirrored else "0",
            },
        )
        if self.options.kerf_in and self.options.kerf_in > 0:
            self._kerf_paths(group, placement, seen)
            return group
        transformed_paths = [
            transform_path_for_placement(path, placement)
            for path in placement.item.part.paths
            if len(path) > 0
        ]
        # Smallest contours first: any hole is cut before the outline that
        # contains it.
        transformed_paths.sort(key=self._bbox_area)
        for transformed in transformed_paths:
            transformed = self._holes_first(transformed)
            if seen is not None:
                pieces = self._dedupe_common_segments(transformed, seen)
            else:
                pieces = [transformed]
            for piece in pieces:
                if len(piece) == 0:
                    continue
                ET.SubElement(
                    group,
                    f"{{{SVG_NS}}}path",
                    {
                        "d": piece.d(),
                        "style": self._cut_stroke_style(self.options.cut_color),
                    },
                )
        return group

    def _write_parts_layer(
        self, root: ET.Element, layout: SheetLayout, label_pairs: dict[int, LabelSpec]
    ) -> None:
        # No sheet border here: a laser program may treat it as a cut.
        parts_layer = self._layer(root, "Parts (red cut + labels)", "parts")
        seen: Optional[set] = set() if self.options.merge_common_cuts else None
        for placement in self._travel_order(layout.placements):
            group = self._part_group(parts_layer, placement, seen)
            spec = label_pairs.get(id(placement))
            if spec is not None:
                self._emit_label(group, spec)

    def _write_cut_layer(self, root: ET.Element, layout: SheetLayout) -> None:
        cut_layer = self._layer(root, "Cut (red)", "cut")
        seen: Optional[set] = set() if self.options.merge_common_cuts else None
        for placement in self._travel_order(layout.placements):
            self._part_group(cut_layer, placement, seen)

    def _emit_label(self, parent: ET.Element, spec: LabelSpec) -> None:
        font_px = spec.font_in * PX_PER_INCH
        line_height_px = spec.font_in * spec.line_spacing * PX_PER_INCH
        x_px = spec.center_x_in * PX_PER_INCH

        n = len(spec.lines)
        total_h_px = n * line_height_px
        top_px = spec.center_y_in * PX_PER_INCH - total_h_px / 2.0
        # Baseline of the first line: half a line down from the top, then the
        # usual ~0.34em drop from a line's centre to its baseline.
        first_baseline_px = top_px + line_height_px / 2.0 + 0.34 * font_px

        if self.options.label_mode == "outline":
            # Blue hairline outline engrave: far faster than rastering.
            text_style = self._cut_stroke_style(self.options.label_outline_color)
        else:
            # Black filled text: raster engrave.
            text_style = f"fill:{self.options.label_color};stroke:none"

        text_el = ET.SubElement(
            parent,
            f"{{{SVG_NS}}}text",
            {
                "x": f"{x_px:.4f}",
                "y": f"{first_baseline_px:.4f}",
                "font-size": f"{font_px:.4f}",
                "font-family": self.options.label_font,
                "text-anchor": "middle",
                "style": text_style,
            },
        )
        for i, line in enumerate(spec.lines):
            tspan = ET.SubElement(
                text_el,
                f"{{{SVG_NS}}}tspan",
                {"x": f"{x_px:.4f}", "dy": "0" if i == 0 else f"{line_height_px:.4f}"},
            )
            tspan.text = line

    @staticmethod
    def _poly_to_path_px(poly: Any) -> str:
        """Serialize a shapely Polygon (with holes) to an SVG path in output px,
        for even-odd filling on the debug layer."""

        def ring(coords) -> str:
            pts = [f"{x * PX_PER_INCH:.3f},{y * PX_PER_INCH:.3f}" for x, y in coords]
            return "M " + " L ".join(pts) + " Z"

        parts = [ring(poly.exterior.coords)]
        parts += [ring(interior.coords) for interior in poly.interiors]
        return " ".join(parts)

    def _write_debug_layer(
        self,
        root: ET.Element,
        layout: SheetLayout,
        sheet: SheetSpec,
        width_px: float,
        height_px: float,
    ) -> None:
        """Non-cutting inspection layer:
        blue    sheet outline + per-part bounding boxes
        orange  unusable edge-margin band
        green   scrap cut-outs the nester may fill (hole-nesting)
        purple  concave pockets the nester may pack into
        """
        opts = self.options
        debug_layer = self._layer(root, "Debug (do not cut)", "debug")

        # Sheet outline (blue dashed).
        ET.SubElement(
            debug_layer,
            f"{{{SVG_NS}}}rect",
            {
                "x": "0",
                "y": "0",
                "width": f"{width_px:.4f}",
                "height": f"{height_px:.4f}",
                "fill": "none",
                "stroke": opts.debug_color,
                "stroke-width": "1",
                "stroke-dasharray": "6 4",
                "vector-effect": "non-scaling-stroke",
            },
        )

        # Edge-margin band (orange): sheet rect minus usable rect, even-odd.
        m_px = sheet.margin * PX_PER_INCH
        if sheet.margin > 0:
            margin_d = (
                f"M 0,0 H {width_px:.3f} V {height_px:.3f} H 0 Z "
                f"M {m_px:.3f},{m_px:.3f} H {width_px - m_px:.3f} "
                f"V {height_px - m_px:.3f} H {m_px:.3f} Z"
            )
            ET.SubElement(
                debug_layer,
                f"{{{SVG_NS}}}path",
                {
                    "d": margin_d,
                    "fill": opts.debug_margin_color,
                    "fill-opacity": "0.15",
                    "fill-rule": "evenodd",
                    "stroke": opts.debug_margin_color,
                    "stroke-width": "1",
                    "stroke-dasharray": "2 3",
                    "vector-effect": "non-scaling-stroke",
                },
            )

        for placement in layout.placements:
            # Scrap cut-outs usable for nesting (green fill).
            for hole in placement_scrap_holes(placement, sheet):
                ET.SubElement(
                    debug_layer,
                    f"{{{SVG_NS}}}path",
                    {
                        "d": self._poly_to_path_px(hole),
                        "fill": opts.debug_hole_color,
                        "fill-opacity": "0.20",
                        "fill-rule": "evenodd",
                        "stroke": opts.debug_hole_color,
                        "stroke-width": "1",
                        "stroke-dasharray": "4 3",
                        "vector-effect": "non-scaling-stroke",
                    },
                )
            # Concave pockets usable for packing (purple fill).
            for region in placement_cavity_regions(placement, sheet):
                ET.SubElement(
                    debug_layer,
                    f"{{{SVG_NS}}}path",
                    {
                        "d": self._poly_to_path_px(region),
                        "fill": opts.debug_cavity_color,
                        "fill-opacity": "0.15",
                        "fill-rule": "evenodd",
                        "stroke": opts.debug_cavity_color,
                        "stroke-width": "1",
                        "stroke-dasharray": "4 3",
                        "vector-effect": "non-scaling-stroke",
                    },
                )

        # Part bounding boxes (blue) on top.
        for placement in layout.placements:
            min_x, min_y, max_x, max_y = placement_bounds(placement)
            ET.SubElement(
                debug_layer,
                f"{{{SVG_NS}}}rect",
                {
                    "x": f"{min_x * PX_PER_INCH:.4f}",
                    "y": f"{min_y * PX_PER_INCH:.4f}",
                    "width": f"{(max_x - min_x) * PX_PER_INCH:.4f}",
                    "height": f"{(max_y - min_y) * PX_PER_INCH:.4f}",
                    "fill": "none",
                    "stroke": opts.debug_color,
                    "stroke-width": "1",
                    "vector-effect": "non-scaling-stroke",
                },
            )

        self._write_debug_legend(debug_layer, layout, sheet, width_px, height_px)

    def _write_debug_legend(
        self,
        debug_layer: ET.Element,
        layout: SheetLayout,
        sheet: SheetSpec,
        width_px: float,
        height_px: float,
    ) -> None:
        """Colour legend so the debug layer is self-explaining. Placed just
        past the used footprint (or under the sheet if it is full) so it does
        not sit on top of parts."""
        opts = self.options
        entries = [
            (opts.debug_color, "part bbox / sheet outline"),
            (opts.debug_margin_color, "edge margin (unusable)"),
            (opts.debug_hole_color, "scrap cut-out (nestable)"),
            (opts.debug_cavity_color, "concave pocket (packable)"),
        ]

        font_px = 0.12 * PX_PER_INCH
        row_px = 0.18 * PX_PER_INCH
        swatch_px = 0.12 * PX_PER_INCH
        pad_px = 0.06 * PX_PER_INCH
        legend_w_px = 2.1 * PX_PER_INCH
        legend_h_px = len(entries) * row_px + 2 * pad_px

        # Prefer the free area right of the used footprint, falling back to below
        # it, else pin inside the top-left margin corner.
        used_max_x = max((placement_bounds(p)[2] for p in layout.placements), default=0.0)
        used_max_y = max((placement_bounds(p)[3] for p in layout.placements), default=0.0)
        x0 = used_max_x * PX_PER_INCH + 2 * pad_px
        y0 = pad_px
        if x0 + legend_w_px > width_px:
            x0 = pad_px
            y0 = used_max_y * PX_PER_INCH + 2 * pad_px
            if y0 + legend_h_px > height_px:
                x0, y0 = pad_px, pad_px

        group = ET.SubElement(debug_layer, f"{{{SVG_NS}}}g", {"id": "debug_legend"})
        ET.SubElement(
            group,
            f"{{{SVG_NS}}}rect",
            {
                "x": f"{x0:.2f}",
                "y": f"{y0:.2f}",
                "width": f"{legend_w_px:.2f}",
                "height": f"{legend_h_px:.2f}",
                "fill": "#ffffff",
                "fill-opacity": "0.85",
                "stroke": opts.debug_color,
                "stroke-width": "1",
                "vector-effect": "non-scaling-stroke",
            },
        )
        for i, (color, label) in enumerate(entries):
            row_y = y0 + pad_px + i * row_px
            ET.SubElement(
                group,
                f"{{{SVG_NS}}}rect",
                {
                    "x": f"{x0 + pad_px:.2f}",
                    "y": f"{row_y:.2f}",
                    "width": f"{swatch_px:.2f}",
                    "height": f"{swatch_px:.2f}",
                    "fill": color,
                    "fill-opacity": "0.5",
                    "stroke": color,
                    "stroke-width": "1",
                    "vector-effect": "non-scaling-stroke",
                },
            )
            text_el = ET.SubElement(
                group,
                f"{{{SVG_NS}}}text",
                {
                    "x": f"{x0 + pad_px + swatch_px + pad_px:.2f}",
                    "y": f"{row_y + swatch_px * 0.85:.2f}",
                    "font-size": f"{font_px:.2f}",
                    "font-family": "sans-serif",
                    "fill": color,
                    "stroke": "none",
                },
            )
            text_el.text = label


def layout_summary(result: LayoutResult, sheet: SheetSpec) -> dict[str, Any]:
    unplaced_counts: dict[str, int] = {}
    for item in result.unplaced:
        unplaced_counts[item.part.display_name] = unplaced_counts.get(item.part.display_name, 0) + 1

    summary: dict[str, Any] = {
        "sheet_count": len(result.sheets),
        "score": list(result.score),
        "sheet_size_in": [sheet.width, sheet.height],
        "unplaced_count": len(result.unplaced),
        "unplaced_by_part": unplaced_counts,
        "sheets": [],
    }

    for idx, layout in enumerate(result.sheets, start=1):
        nested_in = {child.item.uid: parent.item.uid for child, parent in detect_nestings(layout)}

        placements = []
        raw_part_area = 0.0
        for p in layout.placements:
            raw_part_area += float(p.item.part.geometry.area)
            placements.append(
                {
                    "id": p.item.uid,
                    "source": str(p.item.part.request.file),
                    "angle_deg": p.variant.angle_deg,
                    "mirrored": p.variant.mirrored,
                    "x_in": p.x,
                    "y_in": p.y,
                    "bounds_in": list(map(float, placement_bounds(p))),
                    "nested_in_cutout_of": nested_in.get(p.item.uid),
                }
            )

        usable_area = max((sheet.width - 2 * sheet.margin) * (sheet.height - 2 * sheet.margin), EPS)
        used_bbox = None
        if layout.placements:
            xs0 = min(placement_bounds(p)[0] for p in layout.placements)
            ys0 = min(placement_bounds(p)[1] for p in layout.placements)
            xs1 = max(placement_bounds(p)[2] for p in layout.placements)
            ys1 = max(placement_bounds(p)[3] for p in layout.placements)
            used_bbox = [round(xs1 - xs0, 4), round(ys1 - ys0, 4)]

        summary["sheets"].append(
            {
                "sheet": idx,
                "part_count": len(layout.placements),
                "approx_part_area_utilization_percent": round(100.0 * raw_part_area / usable_area, 2),
                "used_footprint_in": used_bbox,
                "nested_in_cutouts": len(nested_in),
                "placements": placements,
            }
        )

    return summary


def write_sheet_svg(
    output_path: Path,
    layout: SheetLayout,
    sheet: SheetSpec,
    sheet_number: int,
    total_sheets: int,
    options: OutputOptions = OutputOptions(),
) -> list[str]:
    """Functional wrapper: write one sheet, return label warnings."""
    return SvgSheetWriter(options).write(output_path, layout, sheet, sheet_number, total_sheets)


def save_scrap_outlines(
    result: LayoutResult, sheet: SheetSpec, base: Path
) -> list[Path]:
    """One SVG per sheet tracing the leftover (uncut) material, drawn at the
    sheet's page size so it can be uploaded straight back as a custom sheet
    shape for the next job.

    Placed parts are subtracted with their interior holes filled: once cut,
    the scrap inside a part's cutout falls out of the sheet and is not
    reusable stock. A morphological opening drops slivers thinner than about
    0.06 in, which no part could use anyway."""
    from shapely.geometry import Polygon
    from shapely.geometry import box as shp_box
    from shapely.ops import unary_union

    def islands(geom: Any) -> list[Any]:
        if isinstance(geom, Polygon):
            return [] if geom.is_empty else [geom]
        return [
            g
            for g in getattr(geom, "geoms", [])
            if isinstance(g, Polygon) and not g.is_empty
        ]

    base = base.resolve()
    base.parent.mkdir(parents=True, exist_ok=True)
    stem = base.with_suffix("") if base.suffix else base

    files: list[Path] = []
    for i, layout in enumerate(result.sheets, start=1):
        region = (
            sheet.boundary
            if sheet.boundary is not None
            else shp_box(0.0, 0.0, sheet.width, sheet.height)
        )
        used = [
            Polygon(g.exterior)
            for placement in layout.placements
            for g in islands(placement.geometry)
        ]
        scrap = region.difference(unary_union(used).buffer(0.01)) if used else region
        scrap = scrap.buffer(-0.03).buffer(0.03)
        pieces = [g for g in islands(scrap) if g.area > 0.05]
        if not pieces:
            continue

        def ring(coords: Any) -> str:
            return (
                "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in coords) + " Z"
            )

        d = " ".join(
            ring(r.coords)
            for poly in pieces
            for r in [poly.exterior, *poly.interiors]
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{sheet.width:.8g}in" height="{sheet.height:.8g}in" '
            f'viewBox="0 0 {sheet.width:.8g} {sheet.height:.8g}">'
            f'<path d="{d}" fill="#c9a06c" fill-rule="evenodd" stroke="none"/>'
            "</svg>"
        )
        if len(result.sheets) == 1:
            path = stem.with_suffix(".svg")
        else:
            path = stem.with_name(f"{stem.name}_sheet_{i:02d}.svg")
        path.write_text(svg, encoding="utf-8")
        files.append(path)
    return files


def save_outputs(
    result: LayoutResult,
    sheet: SheetSpec,
    output: Path,
    options: OutputOptions = OutputOptions(),
) -> tuple[list[Path], list[str]]:
    """Write every sheet SVG plus the JSON summary.

    Returns (files, label_warnings). ``label_warnings`` lists distinct part names
    that could not hold a legible label on any sheet."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = SvgSheetWriter(options)
    files: list[Path] = []
    label_warnings: list[str] = []

    if len(result.sheets) == 1:
        svg_path = output if output.suffix.lower() == ".svg" else output.with_suffix(".svg")
        label_warnings += writer.write(svg_path, result.sheets[0], sheet, 1, 1)
        files.append(svg_path)
        summary_path = svg_path.with_name(svg_path.stem + "_summary.json")
    else:
        base = output.with_suffix("") if output.suffix else output
        for i, layout in enumerate(result.sheets, start=1):
            svg_path = base.with_name(f"{base.name}_sheet_{i:02d}.svg")
            label_warnings += writer.write(svg_path, layout, sheet, i, len(result.sheets))
            files.append(svg_path)
        summary_path = base.with_name(base.name + "_summary.json")

    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(layout_summary(result, sheet), f, indent=2)
    files.append(summary_path)

    # Distinct, order-preserving.
    seen: set[str] = set()
    unique_warnings = [w for w in label_warnings if not (w in seen or seen.add(w))]
    return files, unique_warnings

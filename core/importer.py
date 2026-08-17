"""Load a part file (SVG, DXF, or PDF) into a physically scaled :class:`LoadedPart`.

Each format has its own importer class with the same ``load(request)``
interface; all converge on svgpathtools paths and share :func:`_finalize_part`,
so the rest of the pipeline never knows the source format.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from shapely.affinity import translate as shp_translate
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from .constants import DEFAULT_WELD_IN
from .errors import BalsaNestError
from .models import LoadedPart, PartRequest
from .svg_geometry import (
    build_collision_geometry,
    duplicated_line_stats,
    geometry_to_paths,
    import_svgpathtools,
    sampled_polylines,
    source_scale_from_svg,
    weld_polylines_to_geometry,
)

DOUBLED_LINE_FRACTION = 0.10


def _solid_islands(geometry: Any) -> list[Any]:
    """Disconnected solid components of a part's collision geometry."""
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return [g for g in geometry.geoms if not g.is_empty]
    if isinstance(geometry, GeometryCollection):
        out: list[Any] = []
        for g in geometry.geoms:
            out.extend(_solid_islands(g))
        return out
    return []


def _islands_congruent(a: Any, b: Any, tol: float) -> bool:
    """True when two islands are translated copies of the same shape."""
    ab, bb = a.bounds, b.bounds
    if abs((ab[2] - ab[0]) - (bb[2] - bb[0])) > tol:
        return False
    if abs((ab[3] - ab[1]) - (bb[3] - bb[1])) > tol:
        return False
    if abs(a.area - b.area) > max(tol, 0.01 * max(a.area, b.area)):
        return False
    a0 = shp_translate(a, -ab[0], -ab[1])
    b0 = shp_translate(b, -bb[0], -bb[1])
    return a0.hausdorff_distance(b0) <= tol


def _filter_paths_to_bbox(
    paths: list[Any],
    bbox_in: tuple[float, float, float, float],
    vb_min_x: float,
    vb_min_y: float,
    units_to_in: float,
    pad: float = 0.05,
) -> list[Any]:
    """Keep only the subpaths whose centre falls inside ``bbox_in`` (inches,
    viewBox-relative). Used to drop the duplicate copies of a multi-view
    export while keeping every contour of the retained copy."""
    from svgpathtools import Path as SvgPath

    minx, miny, maxx, maxy = bbox_in
    kept: list[Any] = []
    for path in paths:
        try:
            subs = path.continuous_subpaths()
        except Exception:
            subs = [path]
        segs: list[Any] = []
        for sub in subs:
            if len(sub) == 0:
                continue
            x0, x1, y0, y1 = sub.bbox()
            cx = ((x0 + x1) / 2.0 - vb_min_x) * units_to_in
            cy = ((y0 + y1) / 2.0 - vb_min_y) * units_to_in
            if minx - pad <= cx <= maxx + pad and miny - pad <= cy <= maxy + pad:
                segs.extend(list(sub))
        if segs:
            kept.append(SvgPath(*segs))
    return kept


def dedupe_identical_islands(
    geometry: Any,
    paths: list[Any],
    vb_min_x: float,
    vb_min_y: float,
    units_to_in: float,
    tol: float,
) -> tuple[Any, list[Any], Optional[str]]:
    """Collapse a multi-view export down to a single copy.

    SolidWorks drawings are often exported with the same part drawn two or more
    times (one per view). Nesting such a file as-is pins the copies rigidly at
    their drawn offsets -- e.g. two supports forced 13 in apart, spanning the
    whole sheet. When every disconnected island is a translated copy of the
    same shape, keep only the origin-most one; quantities then count single
    physical pieces. Islands that genuinely differ are left untouched (they may
    be a deliberate multi-piece file), with a note so the user can split them.
    """
    islands = _solid_islands(geometry)
    if len(islands) < 2:
        return geometry, paths, None

    ref = max(islands, key=lambda g: g.area)
    if all(_islands_congruent(ref, g, tol) for g in islands):
        keep = min(islands, key=lambda g: (g.bounds[0], g.bounds[1]))
        kb = keep.bounds
        kept_paths = _filter_paths_to_bbox(
            paths, kb, vb_min_x, vb_min_y, units_to_in
        )
        if kept_paths:
            note = (
                f"file contains {len(islands)} identical disconnected copies of the "
                f"same piece (a multi-view export); using one copy -- 'quantity' now "
                f"counts single pieces. Re-export with a single view to silence this."
            )
            return keep, kept_paths, note

    note = (
        f"file contains {len(islands)} disconnected pieces that differ; they are "
        f"nested as one rigid group at their drawn offsets. If they are separate "
        f"parts, export them as separate files."
    )
    return geometry, paths, note


def repair_doubled_contours(
    geometry: Any,
    paths: list[Any],
    vb_min_x: float,
    vb_min_y: float,
    units_to_in: float,
    sample_step_in: float,
    weld_in: float,
) -> tuple[Any, list[Any], Optional[str]]:
    """Collapse a drawing whose every contour is drawn twice down to one.

    A tapered part -- a wing or stabiliser rib between two different stations --
    has two different faces, so a drawing made from its 3D body carries both
    outlines, a few thousandths of an inch apart. Every contour is doubled: the
    outline, and each lightening hole. The even-odd rule then reads the pair as
    a contour with another contour just inside it, i.e. a hairline ring of
    material enclosing a hole the size of the whole part, and every real cut-out
    inverts with it. Nesting fills the "hole" that is actually solid balsa.

    Rebuilding from the raw strokes with anything within ``weld_in`` welded
    together recovers the single contour set. Cut paths are replaced by the
    welded ones too, so the laser stops cutting each line twice and the packed
    geometry and the emitted paths cannot disagree.

    Only drawings that are genuinely doubled are touched, and the rebuild is
    kept only if it still spans the same extents -- an open-contour part whose
    strokes are all thinner than the weld would otherwise vanish."""
    if weld_in <= 0:
        return geometry, paths, None

    polylines = sampled_polylines(
        paths, vb_min_x, vb_min_y, units_to_in, sample_step_in
    )
    if len(polylines) < 2:
        return geometry, paths, None

    fraction, max_gap = duplicated_line_stats(polylines, weld_in)
    if fraction < DOUBLED_LINE_FRACTION:
        return geometry, paths, None

    welded = weld_polylines_to_geometry(polylines, weld_in)
    if welded.is_empty:
        return geometry, paths, None

    before, after = geometry.bounds, welded.bounds
    if max(abs(before[i] - after[i]) for i in range(4)) > weld_in:
        return geometry, paths, None

    welded_paths = geometry_to_paths(welded, vb_min_x, vb_min_y, units_to_in)
    if not welded_paths:
        return geometry, paths, None

    note = (
        f"{fraction * 100:.0f}% of the drawn lines retraced other lines, indicating "
        f"that each contour was drawn twice. This is typically caused by a 3D export "
        f"of a tapered part, with one outline per face. BalsaNest is repairing this "
        f"by welding contours up to {max_gap:.4f} in apart into a single cut line "
        f"(weld distance {weld_in:g} in), following the outermost edge of each pair "
        f"so each edge is cut only once. In most cases, this repair is sufficient for "
        f"laser cutting. The weld distance can be adjusted under Laser Settings → "
        f"Duplicate Line Welding Tolerance (in). To completely avoid the issue, fix "
        f"the duplicated contours in the drawing and re-export the part without "
        f"duplicate lines."
    )
    return welded, welded_paths, note


class SvgPartImporter:
    """Reads one SVG per :class:`PartRequest`. Stateless apart from the sampling
    step, so a single instance can load an entire job."""

    def __init__(
        self, sample_step_in: float, weld_in: float = DEFAULT_WELD_IN
    ) -> None:
        if sample_step_in <= 0:
            raise BalsaNestError("sample_step must be > 0.")
        self.sample_step_in = sample_step_in
        self.weld_in = weld_in

    def load(self, req: PartRequest) -> LoadedPart:
        req.validate()
        try:
            tree = ET.parse(req.file)
        except ET.ParseError as exc:
            raise BalsaNestError(f"Could not parse SVG XML {req.file}: {exc}") from exc

        root = tree.getroot()
        vb_min_x, vb_min_y, units_to_in = source_scale_from_svg(root, req.file)

        Document = import_svgpathtools()
        try:
            doc = Document(str(req.file))
            paths = list(doc.paths())
        except Exception as exc:
            raise BalsaNestError(
                f"svgpathtools could not flatten geometry from {req.file}: {exc}"
            ) from exc

        if not paths:
            raise BalsaNestError(f"{req.file}: no supported vector geometry found.")

        return _finalize_part(
            req,
            paths,
            vb_min_x,
            vb_min_y,
            units_to_in,
            self.sample_step_in,
            [],
            req.weld_distance if req.weld_distance is not None else self.weld_in,
        )


def _finalize_part(
    req: PartRequest,
    paths: list[Any],
    vb_min_x: float,
    vb_min_y: float,
    units_to_in: float,
    sample_step_in: float,
    notes: list[str],
    weld_in: float = DEFAULT_WELD_IN,
) -> LoadedPart:
    """Shared tail of every importer: collision geometry, doubled-contour
    repair, multi-view dedupe, exact vector bounding boxes, and normalization to
    the part's lower-left."""
    geometry = build_collision_geometry(
        paths, vb_min_x, vb_min_y, units_to_in, sample_step_in
    )

    # Tapered parts exported from 3D: every contour drawn once per face.
    geometry, paths, weld_note = repair_doubled_contours(
        geometry, paths, vb_min_x, vb_min_y, units_to_in, sample_step_in, weld_in
    )
    if weld_note:
        notes = notes + [weld_note]

    # Multi-view exports: collapse identical disconnected copies to one.
    geometry, paths, dedupe_note = dedupe_identical_islands(
        geometry, paths, vb_min_x, vb_min_y, units_to_in, tol=max(0.02, sample_step_in)
    )
    if dedupe_note:
        notes = notes + [dedupe_note]

    # The path libraries' curve-aware bounding boxes give the physical
    # origin/size. The shapely geometry is only sampled for collision and can
    # miss a Bezier extremum, which must not move the actual laser path.
    exact_boxes = []
    for path_obj in paths:
        if len(path_obj) == 0:
            continue
        xmin, xmax, ymin, ymax = path_obj.bbox()
        exact_boxes.append((xmin, xmax, ymin, ymax))
    if not exact_boxes:
        raise BalsaNestError(f"{req.file}: no measurable vector paths found.")

    exact_min_x_user = min(b[0] for b in exact_boxes)
    exact_max_x_user = max(b[1] for b in exact_boxes)
    exact_min_y_user = min(b[2] for b in exact_boxes)
    exact_max_y_user = max(b[3] for b in exact_boxes)

    exact_min_x_in = (exact_min_x_user - vb_min_x) * units_to_in
    exact_max_x_in = (exact_max_x_user - vb_min_x) * units_to_in
    exact_min_y_in = (exact_min_y_user - vb_min_y) * units_to_in
    exact_max_y_in = (exact_max_y_user - vb_min_y) * units_to_in

    normalized = shp_translate(geometry, xoff=-exact_min_x_in, yoff=-exact_min_y_in)

    display_name = req.name or req.file.stem
    return LoadedPart(
        request=req,
        display_name=display_name,
        paths=paths,
        geometry=normalized,
        viewbox_min_x=vb_min_x,
        viewbox_min_y=vb_min_y,
        source_units_to_inch=units_to_in,
        base_min_x_in=exact_min_x_in,
        base_min_y_in=exact_min_y_in,
        base_width_in=exact_max_x_in - exact_min_x_in,
        base_height_in=exact_max_y_in - exact_min_y_in,
        notes=notes,
    )


# DXF $INSUNITS codes -> inches per drawing unit.
_DXF_INSUNITS_TO_INCH = {
    1: 1.0,          # inches
    2: 12.0,         # feet
    4: 1.0 / 25.4,   # millimetres
    5: 1.0 / 2.54,   # centimetres
    6: 39.3700787,   # metres
}

_UNIT_NAME_TO_INCH = {"in": 1.0, "mm": 1.0 / 25.4, "cm": 1.0 / 2.54}


class DxfPartImporter:
    """Reads a DXF drawing (e.g. exported directly from SolidWorks) into a
    :class:`LoadedPart`, so the SolidWorks -> Inkscape conversion step can be
    skipped entirely.

    Entities (LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE, POLYLINE, SPLINE, ...)
    are flattened to fine polylines via ezdxf and re-expressed as svgpathtools
    paths, after which the entire SVG pipeline (stitching, hole detection,
    dedupe, exact bboxes, output transforms) applies unchanged. DXF is y-up and
    SVG is y-down, so y is negated to keep the drawing visually identical."""

    def __init__(
        self, sample_step_in: float, weld_in: float = DEFAULT_WELD_IN
    ) -> None:
        if sample_step_in <= 0:
            raise BalsaNestError("sample_step must be > 0.")
        self.sample_step_in = sample_step_in
        self.weld_in = weld_in

    def load(self, req: PartRequest) -> LoadedPart:
        req.validate()
        try:
            import ezdxf
            from ezdxf import path as ezpath
        except ImportError as exc:
            raise BalsaNestError(
                "DXF input requires the ezdxf package.\n"
                "Install dependencies with:\n"
                "    python -m pip install -r requirements.txt"
            ) from exc

        try:
            doc = ezdxf.readfile(str(req.file))
        except Exception as exc:
            raise BalsaNestError(f"Could not read DXF {req.file}: {exc}") from exc

        notes: list[str] = []
        if req.units is not None:
            units_to_in = _UNIT_NAME_TO_INCH[req.units]
        else:
            insunits = int(doc.header.get("$INSUNITS", 0) or 0)
            if insunits in _DXF_INSUNITS_TO_INCH:
                units_to_in = _DXF_INSUNITS_TO_INCH[insunits]
            else:
                units_to_in = 1.0
                notes.append(
                    "DXF file declares no units ($INSUNITS=0); assuming INCHES. "
                    "If the part size below looks wrong, set \"units\": \"mm\" "
                    "(or \"cm\"/\"in\") on this part in the job config."
                )

        from svgpathtools import Line as SvgLine, Path as SvgPath

        # Flatten curves finely enough that collision sampling sees no error.
        flatten_tol_units = max(self.sample_step_in / 2.0, 1e-4) / units_to_in

        def iter_geometric(entities, depth=0):
            """Yield drawable entities, expanding block INSERTs (which
            ``make_path`` cannot handle as containers) up to a sane depth."""
            for entity in entities:
                if entity.dxftype() == "INSERT" and depth < 8:
                    try:
                        yield from iter_geometric(entity.virtual_entities(), depth + 1)
                    except Exception:
                        continue
                else:
                    yield entity

        paths: list[Any] = []
        for entity in iter_geometric(doc.modelspace()):
            try:
                p = ezpath.make_path(entity)
            except Exception:
                continue  # non-geometric entity (text, dimension, ...)
            try:
                vertices = list(p.flattening(distance=flatten_tol_units))
            except Exception:
                continue
            if len(vertices) < 2:
                continue
            segs = []
            for a, b in zip(vertices, vertices[1:]):
                za = complex(a.x, -a.y)  # y-flip: DXF y-up -> SVG y-down
                zb = complex(b.x, -b.y)
                if abs(za - zb) > 1e-12:
                    segs.append(SvgLine(za, zb))
            if segs:
                paths.append(SvgPath(*segs))

        if not paths:
            raise BalsaNestError(
                f"{req.file}: no usable geometry found in the DXF modelspace."
            )

        return _finalize_part(
            req,
            paths,
            0.0,
            0.0,
            units_to_in,
            self.sample_step_in,
            notes,
            req.weld_distance if req.weld_distance is not None else self.weld_in,
        )


# PDF user space is defined as 72 points per inch.
_PDF_POINTS_TO_INCH = 1.0 / 72.0


class PdfPartImporter:
    """Reads a PDF drawing (e.g. exported directly from SolidWorks) into a
    :class:`LoadedPart`.

    Vector drawing commands (lines, cubic Beziers, rects, quads) are extracted
    with PyMuPDF and re-expressed as svgpathtools paths, after which the SVG
    pipeline (stitching, hole detection, dedupe, exact bboxes, output
    transforms) applies unchanged. PDF user space is 72 points per inch, so
    physical scale is exact for 1:1 exports. PyMuPDF reports coordinates
    y-down like SVG, so no axis flip is needed.

    Text is never part of the extracted geometry, which silently drops the
    SolidWorks text watermark ("SOLIDWORKS Educational Product..."). Vector
    watermarks are caught by two extra filters: invisible white-filled
    background rectangles are skipped, and text-sized shapes confined to the
    bottom edge of the page are dropped when real geometry exists above them.
    """

    # Vector-watermark heuristics (inches).
    _WATERMARK_BAND_IN = 0.75   # strip above the bottom page edge to inspect
    _WATERMARK_MAX_H_IN = 0.35  # taller shapes in the band are real geometry

    def __init__(
        self, sample_step_in: float, weld_in: float = DEFAULT_WELD_IN
    ) -> None:
        if sample_step_in <= 0:
            raise BalsaNestError("sample_step must be > 0.")
        self.sample_step_in = sample_step_in
        self.weld_in = weld_in

    def load(self, req: PartRequest) -> LoadedPart:
        req.validate()
        try:
            import pymupdf
        except ImportError as exc:
            raise BalsaNestError(
                "PDF input requires the pymupdf package.\n"
                "Install dependencies with:\n"
                "    python -m pip install -r requirements.txt"
            ) from exc

        try:
            doc = pymupdf.open(str(req.file))
        except Exception as exc:
            raise BalsaNestError(f"Could not read PDF {req.file}: {exc}") from exc

        notes: list[str] = []
        try:
            if doc.page_count < 1:
                raise BalsaNestError(f"{req.file}: PDF contains no pages.")
            if doc.page_count > 1:
                notes.append(
                    f"PDF has {doc.page_count} pages; using page 1 only. Export "
                    f"one part per single-page PDF."
                )
            page = doc[0]
            page_height_pt = float(page.rect.height)
            paths = self._drawings_to_paths(page.get_drawings())
        finally:
            doc.close()

        if not paths:
            raise BalsaNestError(
                f"{req.file}: no usable vector geometry found. The PDF must "
                f"contain vector lines/curves (not a scanned or rasterized image)."
            )

        paths = self._drop_bottom_watermark(paths, page_height_pt, notes)

        return _finalize_part(
            req,
            paths,
            0.0,
            0.0,
            _PDF_POINTS_TO_INCH,
            self.sample_step_in,
            notes,
            req.weld_distance if req.weld_distance is not None else self.weld_in,
        )

    @staticmethod
    def _drawings_to_paths(drawings: list[dict]) -> list[Any]:
        from svgpathtools import CubicBezier, Line as SvgLine, Path as SvgPath

        def z(p: Any) -> complex:
            return complex(p.x, p.y)

        paths: list[Any] = []
        for drawing in drawings:
            # Fill-only white shapes are invisible ink (typically the page
            # background rectangle) -- not cut geometry.
            if drawing.get("type") == "f":
                fill = drawing.get("fill")
                if fill is not None and all(c >= 0.99 for c in fill):
                    continue
            segs: list[Any] = []
            for item in drawing.get("items", []):
                op = item[0]
                if op == "l":
                    a, b = z(item[1]), z(item[2])
                    if abs(a - b) > 1e-12:
                        segs.append(SvgLine(a, b))
                elif op == "c":
                    segs.append(
                        CubicBezier(z(item[1]), z(item[2]), z(item[3]), z(item[4]))
                    )
                elif op == "re":
                    r = item[1]
                    corners = [
                        complex(r.x0, r.y0),
                        complex(r.x1, r.y0),
                        complex(r.x1, r.y1),
                        complex(r.x0, r.y1),
                    ]
                    for a, b in zip(corners, corners[1:] + corners[:1]):
                        if abs(a - b) > 1e-12:
                            segs.append(SvgLine(a, b))
                elif op == "qu":
                    q = item[1]
                    corners = [z(q.ul), z(q.ur), z(q.lr), z(q.ll)]
                    for a, b in zip(corners, corners[1:] + corners[:1]):
                        if abs(a - b) > 1e-12:
                            segs.append(SvgLine(a, b))
            if segs:
                paths.append(SvgPath(*segs))
        return paths

    def _drop_bottom_watermark(
        self, paths: list[Any], page_height_pt: float, notes: list[str]
    ) -> list[Any]:
        """Drop text-sized vector shapes confined to the bottom edge of the
        page (vectorized SolidWorks watermarks), but only when real geometry
        exists elsewhere so a small legitimate part is never discarded."""
        band_top_pt = page_height_pt - self._WATERMARK_BAND_IN / _PDF_POINTS_TO_INCH
        max_h_pt = self._WATERMARK_MAX_H_IN / _PDF_POINTS_TO_INCH

        kept: list[Any] = []
        dropped = 0
        for path in paths:
            xmin, xmax, ymin, ymax = path.bbox()
            if ymin >= band_top_pt and (ymax - ymin) <= max_h_pt:
                dropped += 1
            else:
                kept.append(path)

        if dropped and kept:
            notes.append(
                f"ignored {dropped} small vector shape(s) along the bottom page "
                f"edge (SolidWorks watermark)."
            )
            return kept
        return paths


def _document_size_in(file: Path) -> Optional[tuple[float, float]]:
    """Physical size of the drawing's page/canvas in inches: the SVG viewBox
    (or width/height attributes) or the PDF page. None when the format has no
    document bounds (DXF) or they cannot be determined."""
    suffix = file.suffix.lower()
    if suffix == ".dxf":
        return None
    if suffix == ".pdf":
        try:
            import pymupdf

            doc = pymupdf.open(str(file))
            try:
                if doc.page_count < 1:
                    return None
                rect = doc[0].rect
                w = float(rect.width) * _PDF_POINTS_TO_INCH
                h = float(rect.height) * _PDF_POINTS_TO_INCH
            finally:
                doc.close()
            return (w, h) if w > 0 and h > 0 else None
        except Exception:
            return None
    from .svg_geometry import (
        parse_svg_length_inches,
        parse_viewbox,
        source_scale_from_svg,
    )

    try:
        root = ET.parse(file).getroot()
    except ET.ParseError:
        return None
    viewbox = parse_viewbox(root)
    if viewbox is not None:
        _, _, units_to_in = source_scale_from_svg(root, file)
        _, _, vb_w, vb_h = viewbox
        w, h = vb_w * units_to_in, vb_h * units_to_in
        return (w, h) if w > 0 and h > 0 else None
    w = parse_svg_length_inches(root.get("width"))
    h = parse_svg_length_inches(root.get("height"))
    if w and h and w > 0 and h > 0:
        return w, h
    return None


def load_sheet_boundary(
    file: Path,
    sample_step_in: float,
    units: Optional[str] = None,
    weld_in: float = DEFAULT_WELD_IN,
) -> tuple[Any, float, float]:
    """Load a stock-sheet outline drawing (SVG/DXF/PDF) into a shapely polygon
    for :attr:`SheetSpec.boundary`.

    The file is imported exactly like a part; the largest closed region becomes
    the sheet outline (interior rings are kept as blocked areas -- defects or
    pre-existing holes in the stock). Returns (polygon, width_in, height_in).

    When the file declares a page/canvas size (SVG viewBox, PDF page), that
    size becomes the sheet width/height and the outline keeps its drawn
    position on the page, so laser alignment matches the original document.
    DXF has no page, so the sheet falls back to the outline's bounding box
    with the polygon normalized to start at (0, 0)."""
    req = PartRequest(file=Path(file), quantity=1, grain="free", units=units)
    part = load_part(req, sample_step_in, weld_in)
    islands = _solid_islands(part.geometry)
    if not islands:
        raise BalsaNestError(
            f"{file}: no closed outline found for the sheet shape. The file must "
            f"contain one closed contour enclosing the usable material."
        )
    boundary = max(islands, key=lambda g: g.area)

    doc_size = _document_size_in(Path(file))
    if doc_size is not None:
        doc_w, doc_h = doc_size
        # Undo the part-loader normalization to restore the page position.
        placed = shp_translate(
            boundary, xoff=part.base_min_x_in, yoff=part.base_min_y_in
        )
        bx0, by0, bx1, by1 = placed.bounds
        tol = 1e-6
        if bx0 < -tol or by0 < -tol or bx1 > doc_w + tol or by1 > doc_h + tol:
            from shapely.geometry import box as shp_box

            clipped = placed.intersection(shp_box(0.0, 0.0, doc_w, doc_h))
            clipped_islands = _solid_islands(clipped)
            placed = (
                max(clipped_islands, key=lambda g: g.area)
                if clipped_islands
                else None
            )
        if placed is not None and not placed.is_empty:
            return placed, doc_w, doc_h

    minx, miny, maxx, maxy = boundary.bounds
    boundary = shp_translate(boundary, xoff=-minx, yoff=-miny)
    return boundary, maxx - minx, maxy - miny


def load_part(
    req: PartRequest, sample_step_in: float, weld_in: float = DEFAULT_WELD_IN
) -> LoadedPart:
    """Load a part file, dispatching on its extension (.svg, .dxf or .pdf)."""
    suffix = req.file.suffix.lower()
    if suffix == ".dxf":
        return DxfPartImporter(sample_step_in, weld_in).load(req)
    if suffix == ".pdf":
        return PdfPartImporter(sample_step_in, weld_in).load(req)
    return SvgPartImporter(sample_step_in, weld_in).load(req)

"""SVG <-> shapely geometry helpers."""

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from shapely import make_valid
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import unary_union

from .constants import EPS, LENGTH_RE, PX_PER_INCH, UNIT_TO_INCH
from .errors import BalsaNestError
from .models import LoadedPart, Placement


def normalize_angle(angle: float) -> float:
    value = angle % 360.0
    if abs(value - 360.0) < 1e-9 or abs(value) < 1e-9:
        return 0.0
    return round(value, 9)


def dedupe_angles(angles: Iterable[float]) -> list[float]:
    result: list[float] = []
    for angle in angles:
        a = normalize_angle(angle)
        if not any(abs(a - existing) < 1e-7 for existing in result):
            result.append(a)
    return result


def parse_svg_length_inches(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    match = LENGTH_RE.match(raw)
    if not match:
        raise BalsaNestError(f"Unsupported SVG length value: {raw!r}")
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "%":
        raise BalsaNestError(
            "Percentage root width/height is ambiguous for physical-scale nesting."
        )
    if unit not in UNIT_TO_INCH:
        raise BalsaNestError(f"Unsupported SVG unit {unit!r} in {raw!r}")
    return value * UNIT_TO_INCH[unit]


def parse_viewbox(root: ET.Element) -> Optional[tuple[float, float, float, float]]:
    raw = root.get("viewBox") or root.get("viewbox")
    if not raw:
        return None
    pieces = re.split(r"[\s,]+", raw.strip())
    if len(pieces) != 4:
        raise BalsaNestError(f"Invalid SVG viewBox: {raw!r}")
    x, y, w, h = map(float, pieces)
    if w <= 0 or h <= 0:
        raise BalsaNestError(f"Invalid SVG viewBox dimensions: {raw!r}")
    return x, y, w, h


def source_scale_from_svg(root: ET.Element, source: Path) -> tuple[float, float, float]:
    """
    Return (viewbox_min_x, viewbox_min_y, inches_per_root_user_unit).

    If a viewBox is present and physical width/height are present, those define
    the scale. If no physical dimensions are present, CSS px = 1/96 in is used.
    Non-uniform root scaling is rejected because it would silently distort the
    CAD drawing.
    """
    viewbox = parse_viewbox(root)
    width_in = parse_svg_length_inches(root.get("width"))
    height_in = parse_svg_length_inches(root.get("height"))

    if viewbox is None:
        return 0.0, 0.0, 1.0 / PX_PER_INCH

    min_x, min_y, vb_w, vb_h = viewbox
    scales = []
    if width_in is not None:
        scales.append(("x", width_in / vb_w))
    if height_in is not None:
        scales.append(("y", height_in / vb_h))

    if not scales:
        return min_x, min_y, 1.0 / PX_PER_INCH

    scale = scales[0][1]
    if len(scales) == 2:
        sx = scales[0][1]
        sy = scales[1][1]
        rel = abs(sx - sy) / max(abs(sx), abs(sy), EPS)
        if rel > 1e-3:
            raise BalsaNestError(
                f"{source}: physical width/height and viewBox imply non-uniform "
                f"scale ({sx:.8g} in/unit vs {sy:.8g} in/unit). "
                "Re-export the drawing at uniform 1:1 scale."
            )
        scale = (sx + sy) / 2.0

    return min_x, min_y, scale


def import_svgpathtools():
    try:
        from svgpathtools import Document
    except ImportError as exc:
        raise BalsaNestError(
            "BalsaNest requires svgpathtools to read SVG geometry.\n"
            "Install dependencies with:\n"
            "    python -m pip install -r requirements.txt"
        ) from exc
    return Document


def mirror_path_x(path: Any) -> Any:
    """Reflect a flattened svgpathtools Path across the vertical axis (x -> -x).

    svgpathtools cannot non-uniformly scale Arc segments, so we reflect each
    segment analytically instead. This keeps the mirror exact (no curve
    sampling) and matches the shapely collision geometry, which is mirrored the
    same way. Reflection reverses orientation: Arc sweep flags flip and the
    Arc x-axis rotation negates.
    """
    from svgpathtools import Arc, CubicBezier, Line, Path as SvgPath, QuadraticBezier

    def r(z: complex) -> complex:
        return complex(-z.real, z.imag)

    segments = []
    for seg in path:
        if isinstance(seg, Line):
            segments.append(Line(r(seg.start), r(seg.end)))
        elif isinstance(seg, QuadraticBezier):
            segments.append(QuadraticBezier(r(seg.start), r(seg.control), r(seg.end)))
        elif isinstance(seg, CubicBezier):
            segments.append(
                CubicBezier(r(seg.start), r(seg.control1), r(seg.control2), r(seg.end))
            )
        elif isinstance(seg, Arc):
            segments.append(
                Arc(
                    r(seg.start),
                    seg.radius,
                    -seg.rotation,
                    seg.large_arc,
                    not seg.sweep,
                    r(seg.end),
                )
            )
        else:  # pragma: no cover - defensive
            raise BalsaNestError(
                f"Cannot mirror unsupported segment type: {type(seg).__name__}"
            )
    return SvgPath(*segments)


def sample_subpath_points(
    subpath: Any,
    viewbox_min_x: float,
    viewbox_min_y: float,
    units_to_inch: float,
    sample_step_in: float,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if len(subpath) == 0:
        return points

    for seg_index, segment in enumerate(subpath):
        try:
            length_user = float(segment.length(error=1e-5))
        except TypeError:
            length_user = float(segment.length())
        except Exception:
            length_user = abs(segment.end - segment.start)

        length_in = max(length_user * units_to_inch, 0.0)
        samples = max(1, int(math.ceil(length_in / sample_step_in)))

        if seg_index == 0:
            z = segment.point(0.0)
            points.append(
                (
                    (z.real - viewbox_min_x) * units_to_inch,
                    (z.imag - viewbox_min_y) * units_to_inch,
                )
            )

        for i in range(1, samples + 1):
            t = i / samples
            z = segment.point(t)
            points.append(
                (
                    (z.real - viewbox_min_x) * units_to_inch,
                    (z.imag - viewbox_min_y) * units_to_inch,
                )
            )

    return points


def polygonal_only(geom: Any) -> Any:
    if geom is None or geom.is_empty:
        return GeometryCollection()
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, GeometryCollection):
        polys = [
            g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon)) and not g.is_empty
        ]
        return unary_union(polys) if polys else GeometryCollection()
    return GeometryCollection()


def sampled_polylines(
    paths: Sequence[Any],
    viewbox_min_x: float,
    viewbox_min_y: float,
    units_to_inch: float,
    sample_step_in: float,
) -> list[list[tuple[float, float]]]:
    """Every continuous subpath of every path, flattened to points in inches.
    """
    lines: list[list[tuple[float, float]]] = []
    for path in paths:
        try:
            subpaths = path.continuous_subpaths()
        except Exception:
            subpaths = [path]
        for subpath in subpaths:
            if len(subpath) == 0:
                continue
            pts = sample_subpath_points(
                subpath, viewbox_min_x, viewbox_min_y, units_to_inch, sample_step_in
            )
            if len(pts) >= 2:
                lines.append(pts)
    return lines


def duplicated_line_stats(
    polylines: Sequence[Sequence[tuple[float, float]]], weld_in: float
) -> tuple[float, float]:
    """How much of the drawn length merely retraces other drawn length, and how
    far apart the widest such pair runs. Returns (fraction, max_gap_inches).
    """
    lines = [LineString(p) for p in polylines if len(p) >= 2]
    total = sum(line.length for line in lines)
    if total <= 0:
        return 0.0, 0.0

    # A stroke may poke a little way out of the cover without being genuine
    # geometry: the two copies splay apart at the ends of the taper.
    slack = 2.0 * weld_in
    duplicated = 0.0
    max_gap = 0.0
    kept: list[Any] = []
    cover: Any = None
    for line in sorted(lines, key=lambda g: g.length, reverse=True):
        if cover is not None and line.difference(cover).length <= slack:
            duplicated += line.length
            kept_geom = MultiLineString(kept) if len(kept) > 1 else kept[0]
            fused = [
                d
                for d in (kept_geom.distance(Point(c)) for c in line.coords)
                if d <= weld_in
            ]
            if fused:
                max_gap = max(max_gap, max(fused))
            continue
        kept.append(line)
        buffered = line.buffer(weld_in)
        cover = buffered if cover is None else cover.union(buffered)
    return duplicated / total, max_gap


def weld_polylines_to_geometry(
    polylines: Sequence[Sequence[tuple[float, float]]], weld_in: float
) -> Any:
    """Rebuild part geometry from raw strokes, welding everything closer
    together than ``weld_in`` into a single cut line.
    """
    lines = [LineString(p) for p in polylines if len(p) >= 2]
    if not lines:
        return GeometryCollection()

    radius = weld_in / 2.0
    band = unary_union(lines).buffer(radius, join_style="mitre", mitre_limit=5.0)
    components = (
        [band] if isinstance(band, Polygon) else list(getattr(band, "geoms", []))
    )

    regions: list[Any] = []
    for component in components:
        if not isinstance(component, Polygon) or component.is_empty:
            continue
        region = Polygon(component.exterior).buffer(
            -radius, join_style="mitre", mitre_limit=5.0
        )
        region = polygonal_only(region)
        if not region.is_empty and region.area > EPS:
            regions.append(region)

    occupied: Any = GeometryCollection()
    for region in sorted(regions, key=lambda g: g.area, reverse=True):
        occupied = region if occupied.is_empty else occupied.symmetric_difference(region)
        if not occupied.is_valid:
            occupied = make_valid(occupied)
    return polygonal_only(occupied)


def geometry_to_paths(
    geometry: Any,
    viewbox_min_x: float,
    viewbox_min_y: float,
    units_to_inch: float,
) -> list[Any]:
    """One closed svgpathtools path per ring of a shapely geometry, expressed
    back in the source drawing's user units.

    Used after a weld. The cut paths reaching the laser are then the same single
    contours the nester packed, not the doubled strokes they were rebuilt
    from."""
    from svgpathtools import Line, Path as SvgPath

    def to_user(x: float, y: float) -> complex:
        return complex(x / units_to_inch + viewbox_min_x, y / units_to_inch + viewbox_min_y)

    polys: list[Any] = []
    if isinstance(geometry, Polygon):
        polys = [geometry]
    elif isinstance(geometry, (MultiPolygon, GeometryCollection)):
        polys = [g for g in geometry.geoms if isinstance(g, Polygon)]

    paths: list[Any] = []
    for poly in polys:
        if poly.is_empty:
            continue
        for ring in [poly.exterior, *poly.interiors]:
            points = [to_user(x, y) for x, y in ring.coords]
            segments = [
                Line(a, b) for a, b in zip(points, points[1:]) if abs(a - b) > 1e-12
            ]
            if segments:
                paths.append(SvgPath(*segments))
    return paths


def stitch_open_contours(
    polylines: Sequence[Sequence[tuple[float, float]]], tol: float
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """Chain open polylines whose endpoints (nearly) coincide into closed rings.

    CAD exports (SolidWorks via Inkscape in particular) often draw a single part
    outline as dozens of tiny disconnected subpaths separated by ``m 0,0``
    moveto commands. Individually each fragment is an open line, so without
    stitching the part would register as a hairline instead of solid material --
    breaking collision checks, hole detection and labels. Returns
    (closed_rings, leftover_open_chains)."""
    remaining = [list(p) for p in polylines if len(p) >= 2]
    closed: list[list[tuple[float, float]]] = []
    still_open: list[list[tuple[float, float]]] = []

    while remaining:
        chain = remaining.pop(0)
        extended = True
        while extended:
            extended = False
            if len(chain) >= 4 and math.dist(chain[0], chain[-1]) <= tol:
                break  # ring is complete
            for i, other in enumerate(remaining):
                if math.dist(chain[-1], other[0]) <= tol:
                    chain += other[1:]
                elif math.dist(chain[-1], other[-1]) <= tol:
                    chain += other[-2::-1]
                elif math.dist(chain[0], other[-1]) <= tol:
                    chain = other[:-1] + chain
                elif math.dist(chain[0], other[0]) <= tol:
                    chain = other[::-1][:-1] + chain
                else:
                    continue
                remaining.pop(i)
                extended = True
                break

        if len(chain) >= 4 and math.dist(chain[0], chain[-1]) <= tol:
            chain[-1] = chain[0]
            closed.append(chain)
        else:
            still_open.append(chain)

    return closed, still_open


def build_collision_geometry(
    paths: Sequence[Any],
    viewbox_min_x: float,
    viewbox_min_y: float,
    units_to_inch: float,
    sample_step_in: float,
) -> Any:
    """
    Approximate SVG cut geometry as shapely geometry.

    Closed contours are XOR-combined so nested contours become holes. Open
    fragments are first stitched end-to-end into rings (segment-soup CAD
    exports). Whatever genuinely stays open is buffered by a tiny
    sampling-scale amount and unioned in.
    """
    closed_polygons: list[Any] = []
    open_polylines: list[list[tuple[float, float]]] = []

    for path in paths:
        try:
            subpaths = path.continuous_subpaths()
        except Exception:
            subpaths = [path]

        for subpath in subpaths:
            pts = sample_subpath_points(
                subpath, viewbox_min_x, viewbox_min_y, units_to_inch, sample_step_in
            )
            if len(pts) < 2:
                continue

            try:
                is_closed = bool(subpath.isclosed())
            except Exception:
                is_closed = math.dist(pts[0], pts[-1]) <= max(sample_step_in, 1e-5)

            if is_closed and len(pts) >= 3:
                if math.dist(pts[0], pts[-1]) > 1e-8:
                    pts.append(pts[0])
                poly = Polygon(pts)
                if not poly.is_valid:
                    poly = make_valid(poly)
                poly = polygonal_only(poly)
                if not poly.is_empty and poly.area > EPS:
                    closed_polygons.append(poly)
            else:
                open_polylines.append(pts)

    # Stitch fragment soup into rings before giving up on closure.
    stitch_tol = max(1e-6, min(0.005, sample_step_in * 0.5))
    stitched_rings, leftover_open = stitch_open_contours(open_polylines, stitch_tol)
    for ring in stitched_rings:
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = make_valid(poly)
        poly = polygonal_only(poly)
        if not poly.is_empty and poly.area > EPS:
            closed_polygons.append(poly)

    open_lines: list[Any] = []
    for pts in leftover_open:
        line = LineString(pts)
        if not line.is_empty and line.length > EPS:
            open_lines.append(line)

    occupied: Any = GeometryCollection()

    for poly in sorted(closed_polygons, key=lambda g: g.area, reverse=True):
        occupied = poly if occupied.is_empty else occupied.symmetric_difference(poly)
        if not occupied.is_valid:
            occupied = make_valid(occupied)

    if open_lines:
        line_geom = unary_union(open_lines)
        line_width = max(sample_step_in * 0.10, 0.0005)
        line_occupancy = line_geom.buffer(line_width, cap_style="flat", join_style="mitre")
        occupied = line_occupancy if occupied.is_empty else unary_union([occupied, line_occupancy])

    if occupied.is_empty:
        raise BalsaNestError(
            "No usable path geometry was found. Ensure the SVG contains paths, "
            "rectangles, circles, ellipses, lines, polylines, or polygons."
        )

    if not occupied.is_valid:
        occupied = make_valid(occupied)

    return occupied


def path_to_local_inches(part: LoadedPart, path: Any) -> Any:
    p = path.translated(complex(-part.viewbox_min_x, -part.viewbox_min_y))
    p = p.scaled(part.source_units_to_inch)
    p = p.translated(complex(-part.base_min_x_in, -part.base_min_y_in))
    return p


def exact_rotated_bounds(
    part: LoadedPart, angle_deg: float, mirror: bool = False
) -> tuple[float, float, float, float]:
    boxes = []
    for path in part.paths:
        if len(path) == 0:
            continue
        p = path_to_local_inches(part, path)
        if mirror:
            p = mirror_path_x(p)
        p = p.rotated(angle_deg, origin=0j)
        xmin, xmax, ymin, ymax = p.bbox()
        boxes.append((xmin, xmax, ymin, ymax))

    if not boxes:
        raise BalsaNestError(f"{part.display_name}: no measurable paths.")

    return (
        min(b[0] for b in boxes),
        min(b[2] for b in boxes),
        max(b[1] for b in boxes),
        max(b[3] for b in boxes),
    )


def transform_path_for_placement(path: Any, placement: Placement) -> Any:
    """
    Transform a flattened source path into output SVG px coordinates.

    Operations mirror the shapely collision geometry:
      source root coords -> remove viewBox origin -> convert to inches
      -> remove base geometry lower-left -> (optional) reflect -> rotate
      -> remove rotated lower-left -> translate to placement -> to 96 px/in.
    """
    part = placement.item.part
    variant = placement.variant

    p = path_to_local_inches(part, path)
    if variant.mirrored:
        p = mirror_path_x(p)
    p = p.rotated(variant.angle_deg, origin=0j)
    p = p.translated(
        complex(
            -variant.rotated_min_x + placement.x,
            -variant.rotated_min_y + placement.y,
        )
    )
    p = p.scaled(PX_PER_INCH)
    return p

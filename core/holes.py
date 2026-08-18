"""Reasoning about scrap interior cutouts of placed parts.

In this workflow every closed contour is a through-cut. An interior ring is
material that gets removed as scrap. These helpers propose such positions.
"""

from typing import Any, Iterable, Sequence

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from .constants import EPS
from .models import Placement, SheetLayout, SheetSpec, Variant, placement_bounds
from .svg_geometry import polygonal_only


def iter_hole_polygons(geom: Any) -> Iterable[Any]:
    """Yield each interior cutout of a placed part as a filled Polygon."""
    polys: list[Any] = []
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    elif isinstance(geom, GeometryCollection):
        polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
    for poly in polys:
        if isinstance(poly, MultiPolygon):
            yield from iter_hole_polygons(poly)
            continue
        for interior in poly.interiors:
            hole = Polygon(interior)
            if hole.is_valid and not hole.is_empty:
                yield hole


def placement_scrap_holes(placement: Placement, sheet: SheetSpec) -> list[Any]:
    """Cached list of a placed part's cutouts, each shrunk by the required
    spacing so anything fitting inside keeps clear of the hole's edge."""
    cache = getattr(placement, "_scrap_holes_cache", None)
    if cache is not None:
        return cache
    holes: list[Any] = []
    for hole in iter_hole_polygons(placement.geometry):
        if hole.area < sheet.min_hole_area:
            continue
        usable = polygonal_only(hole.buffer(-sheet.spacing))
        if not usable.is_empty and usable.area >= sheet.min_hole_area:
            holes.append(usable)
    placement._scrap_holes_cache = holes
    return holes


def hole_candidate_seeds(
    variant: Variant, placed: Sequence[Placement], sheet: SheetSpec
) -> list[tuple[float, float]]:
    """Lower-left seed positions for dropping a small part into a bigger part's
    scrap cut-out. Feasibility (real spacing to the hole edge) is confirmed later
    by the normal collision test. Here we only propose plausible spots.

    Besides the hole's own corners, contact lines against parts already nested
    in the same hole are proposed, so successive parts pack shoulder-to-shoulder
    (and can interlock) instead of floating wherever the first seed landed.
    Every placement's holes are considered, including placements that are
    themselves nested inside something else. Scrap-in-scrap nesting recurses
    naturally."""
    seeds: list[tuple[float, float]] = []
    for p in placed:
        for hole in placement_scrap_holes(p, sheet):
            hminx, hminy, hmaxx, hmaxy = hole.bounds
            if variant.width > (hmaxx - hminx) + EPS:
                continue
            if variant.height > (hmaxy - hminy) + EPS:
                continue

            xs = {hminx, hmaxx - variant.width}
            ys = {hminy, hmaxy - variant.height}
            # Contact lines vs. parts already inside this hole's bbox.
            for q in placed:
                if q is p:
                    continue
                qb = placement_bounds(q)
                if (
                    qb[0] >= hminx - EPS
                    and qb[1] >= hminy - EPS
                    and qb[2] <= hmaxx + EPS
                    and qb[3] <= hmaxy + EPS
                ):
                    xs.update(
                        {
                            qb[2] + sheet.spacing,
                            qb[0] - variant.width - sheet.spacing,
                            qb[0],
                            qb[2] - variant.width,
                        }
                    )
                    ys.update(
                        {
                            qb[3] + sheet.spacing,
                            qb[1] - variant.height - sheet.spacing,
                            qb[1],
                            qb[3] - variant.height,
                        }
                    )

            xs = {x for x in xs if hminx - EPS <= x <= hmaxx - variant.width + EPS}
            ys = {y for y in ys if hminy - EPS <= y <= hmaxy - variant.height + EPS}
            seeds.extend((x, y) for x in xs for y in ys)

            rep = hole.representative_point()
            seeds.append((rep.x - variant.width / 2.0, rep.y - variant.height / 2.0))
    return seeds


def _filled_exterior(geom: Any) -> Any:
    """The part's outer boundary with interior holes filled (holes are handled
    separately as scrap, and cavities are indentations of the outline)."""
    if isinstance(geom, Polygon):
        return Polygon(geom.exterior)
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([Polygon(g.exterior) for g in geom.geoms])
    return geom


def placement_cavity_regions(placement: Placement, sheet: SheetSpec) -> list[Any]:
    """Concave pockets of a placed part: convex hull minus the (hole-filled)
    part, shrunk by the spacing. An arch-shaped bulkhead cutaway, an airfoil's
    underside camber, a C-channel opening. All of it is usable room that plain
    bounding-box candidate seeds never look inside. Cached per placement."""
    cache = getattr(placement, "_cavity_cache", None)
    if cache is not None:
        return cache
    regions: list[Any] = []
    try:
        filled = _filled_exterior(placement.geometry)
        pockets = filled.convex_hull.difference(filled)
        pockets = polygonal_only(pockets.buffer(-sheet.spacing))
        candidates = (
            list(pockets.geoms) if isinstance(pockets, MultiPolygon) else [pockets]
        )
        for region in candidates:
            if isinstance(region, Polygon) and not region.is_empty and region.area >= sheet.min_hole_area:
                regions.append(region)
    except Exception:
        regions = []
    placement._cavity_cache = regions
    return regions


def cavity_candidate_seeds(
    variant: Variant, placed: Sequence[Placement], sheet: SheetSpec
) -> list[tuple[float, float]]:
    """Seed positions in and around placed parts' concave pockets. Unlike scrap
    holes these are ordinary stock, connected to the rest of the sheet. They
    join the normal candidate ranking rather than winning outright. Without
    explicit seeds the search would never propose them at all.

    Corner-aligned seeds are proposed even when the variant is BIGGER than the
    pocket: that is exactly the tetris/handshake case, where a second arch-shaped
    part rotated 180° dips partially into the first part's pocket. Feasibility
    is decided later by the real collision test."""
    seeds: list[tuple[float, float]] = []
    for p in placed:
        for region in placement_cavity_regions(p, sheet):
            rminx, rminy, rmaxx, rmaxy = region.bounds
            for x in (rminx, rmaxx - variant.width):
                for y in (rminy, rmaxy - variant.height):
                    seeds.append((x, y))
            if (
                variant.width <= (rmaxx - rminx) + EPS
                and variant.height <= (rmaxy - rminy) + EPS
            ):
                rep = region.representative_point()
                seeds.append((rep.x - variant.width / 2.0, rep.y - variant.height / 2.0))
    return seeds


def detect_nestings(layout: SheetLayout) -> list[tuple[Placement, Placement]]:
    """Find parts that ended up inside another part's scrap cut-out (child's
    representative point lies within a parent's hole)."""
    parents = [(p, list(iter_hole_polygons(p.geometry))) for p in layout.placements]
    parents = [(p, holes) for p, holes in parents if holes]

    nestings: list[tuple[Placement, Placement]] = []
    for child in layout.placements:
        try:
            point = child.geometry.representative_point()
        except Exception:
            continue
        for parent, holes in parents:
            if parent is child:
                continue
            if any(hole.contains(point) for hole in holes):
                nestings.append((child, parent))
                break
    return nestings

"""The nesting engine: collision, candidate generation, compaction, greedy
multi-pass packing and scoring.

Candidate positions come from three complementary generators: bounding box
contact lines (cheap, axis-aligned), scrap-hole / concave-pocket seeds
(:mod:`.holes`) and true no-fit-polygon contact positions (:mod:`.nfp`, always
on, with a per-pair graceful fallback). All public functions operate on plain
shapely geometry so this module can still be replaced wholesale behind the
same :class:`Nester` interface without disturbing import/output.
"""

from __future__ import annotations

import math
import random
from typing import Any, Optional, Sequence

from shapely.affinity import translate as shp_translate
from shapely.geometry import MultiPolygon, Polygon
from shapely.prepared import prep

from .constants import EPS
from .holes import (
    cavity_candidate_seeds,
    hole_candidate_seeds,
    placement_cavity_regions,
)
from .models import (
    Item,
    LayoutResult,
    Placement,
    SheetLayout,
    SheetSpec,
    placement_bounds,
)
from .nfp import nfp_candidate_seeds


# --- collision ---------------------------------------------------------------

def sheet_usable_region(sheet: SheetSpec) -> Optional[tuple[Any, Any]]:
    """(region, prepared_region) of a polygonal sheet's margin-shrunk interior,
    cached on the (frozen) SheetSpec. None for plain rectangular sheets."""
    if sheet.boundary is None:
        return None
    cached = getattr(sheet, "_usable_region_cache", None)
    if cached is None:
        region = sheet.boundary
        if sheet.margin > 0:
            region = region.buffer(-sheet.margin, join_style="mitre")
        if region.is_empty:
            raise_msg = "Sheet margin leaves no usable area inside the sheet outline."
            from .errors import BalsaNestError
            raise BalsaNestError(raise_msg)
        cached = (region, prep(region))
        object.__setattr__(sheet, "_usable_region_cache", cached)
    return cached


def geometry_fits_sheet(geom: Any, sheet: SheetSpec, tolerance: float = 1e-7) -> bool:
    min_x, min_y, max_x, max_y = geom.bounds
    in_bbox = (
        min_x >= sheet.margin - tolerance
        and min_y >= sheet.margin - tolerance
        and max_x <= sheet.width - sheet.margin + tolerance
        and max_y <= sheet.height - sheet.margin + tolerance
    )
    if not in_bbox and sheet.boundary is None:
        return False
    region_info = sheet_usable_region(sheet)
    if region_info is None:
        return in_bbox
    return bool(region_info[1].contains(geom))


def bbox_farther_than_spacing(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    spacing: float,
) -> bool:
    a_minx, a_miny, a_maxx, a_maxy = a
    b_minx, b_miny, b_maxx, b_maxy = b
    return (
        a_maxx + spacing <= b_minx
        or b_maxx + spacing <= a_minx
        or a_maxy + spacing <= b_miny
        or b_maxy + spacing <= a_miny
    )


def _filled_geometry(geom: Any) -> Any:
    """The part with its interior holes filled in (outer boundary only). Used to
    treat cut-outs as solid when hole-nesting is disabled, so a part can never
    incidentally settle inside another part's cut-out."""
    if isinstance(geom, Polygon):
        return Polygon(geom.exterior)
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([Polygon(g.exterior) for g in geom.geoms])
    return geom


def placement_clearance_zone(placement: Placement, spacing: float, fill_holes: bool = False) -> Any:
    """Cached, prepared, spacing-inflated version of a placed part. A candidate
    is too close iff it intersects this zone. ``prep`` + ``intersects`` is far
    faster than repeated polygon ``distance`` on airfoil-dense strips. When
    ``fill_holes`` is set, cut-outs are treated as solid."""
    key = (spacing, fill_holes)
    cache = getattr(placement, "_clearance_cache", None)
    if cache is not None and cache[0] == key:
        return cache[1]
    base = _filled_geometry(placement.geometry) if fill_holes else placement.geometry
    inflate = max(spacing - 1e-6, 0.0)
    zone = base.buffer(inflate, quad_segs=2, join_style="mitre") if inflate > 0.0 else base
    prepared = prep(zone)
    placement._clearance_cache = (key, prepared)
    return prepared


def is_collision_free(
    candidate: Any, placed: Sequence[Placement], spacing: float, fill_holes: bool = False
) -> bool:
    cb = candidate.bounds
    for placement in placed:
        pb = placement_bounds(placement)
        if bbox_farther_than_spacing(cb, pb, spacing):
            continue
        zone = placement_clearance_zone(placement, spacing, fill_holes)
        if zone.intersects(candidate):
            return False
    return True


# --- candidate positions -----------------------------------------------------

def placement_key_from_bounds(
    bounds: tuple[float, float, float, float],
    sheet: SheetSpec,
    union: Optional[tuple[float, float, float, float]] = None,
) -> tuple:
    """Ranking key for a candidate position (smaller is better).

    Primary criterion (when ``union`` -- the bbox of everything already placed
    on the sheet -- is given): the area of the combined bounding box after
    adding this candidate. That makes "keep the total footprint as small as
    possible" the packer's actual objective, so parts cluster into one compact
    block and the leftover stock stays in one usable piece instead of being
    split by parts scattered to opposite ends of the sheet.

    Tie-breaks: progress along the long axis, then the short axis, then tuck
    into the origin corner.
    """
    min_x, min_y, max_x, max_y = bounds
    if union is not None:
        u_min_x, u_min_y, u_max_x, u_max_y = union
        grown_w = max(max_x, u_max_x) - min(min_x, u_min_x)
        grown_h = max(max_y, u_max_y) - min(min_y, u_min_y)
        union_area = round(grown_w * grown_h, 6)
    else:
        union_area = 0.0

    if sheet.width >= sheet.height:
        return (union_area, max_x, max_y, min_y, min_x)
    return (union_area, max_y, max_x, min_x, min_y)


def placed_union_bounds(placed: Sequence[Placement]) -> Optional[tuple[float, float, float, float]]:
    """Combined bbox of everything already on the sheet (None when empty)."""
    if not placed:
        return None
    bs = [placement_bounds(p) for p in placed]
    return (
        min(b[0] for b in bs),
        min(b[1] for b in bs),
        max(b[2] for b in bs),
        max(b[3] for b in bs),
    )


def _convex_hull_pts(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain. Pure Python: called per candidate in the hot
    ranking loop, where building shapely objects is ~1000x more expensive."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _hull_area(hull: list[tuple[float, float]]) -> float:
    if len(hull) < 3:
        return 0.0
    area = 0.0
    for i in range(len(hull)):
        x0, y0 = hull[i]
        x1, y1 = hull[(i + 1) % len(hull)]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def placed_hull_coords(placed: Sequence[Placement]) -> Optional[list[tuple[float, float]]]:
    """Convex-hull vertices of everything on the sheet (None when empty),
    reduced to the combined hull so downstream per-candidate work stays tiny.
    Per-placement hulls are cached on the placement."""
    if not placed:
        return None
    pts: list[tuple[float, float]] = []
    for p in placed:
        c = getattr(p, "_hull_coords_cache", None)
        if c is None:
            try:
                c = list(p.geometry.convex_hull.exterior.coords)
            except Exception:
                b = placement_bounds(p)
                c = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
            p._hull_coords_cache = c
        pts.extend(c)
    return _convex_hull_pts(pts)


def hull_area_with(hull_pts: list[tuple[float, float]], bounds: tuple[float, float, float, float]) -> float:
    """Area of the convex hull of the placed cluster plus a candidate bbox.

    This measures how much a position bloats the cluster outline: a candidate
    tucked into an internal gap adds nothing, one sticking out past the
    frontier adds a lot. Used as the secondary ranking criterion (after the
    union-bbox area) so the packer actively fills gaps before growing."""
    min_x, min_y, max_x, max_y = bounds
    corners = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
    return _hull_area(_convex_hull_pts(hull_pts + corners))


def refined_placement_key(
    bounds: tuple[float, float, float, float],
    sheet: SheetSpec,
    union: Optional[tuple[float, float, float, float]],
    hull_pts: Optional[list[tuple[float, float]]],
) -> tuple:
    """Full ranking key: union-bbox area, then cluster convex-hull area, then
    the axis tie-breaks. Keeps the used stock a small rectangle first, and
    within that packs the parts into the tightest possible blob."""
    key = placement_key_from_bounds(bounds, sheet, union)
    if hull_pts is None:
        return (key[0], 0.0) + key[1:]
    return (key[0], round(hull_area_with(hull_pts, bounds), 6)) + key[1:]


def _cap_axis_lines(values: set[float], lower: float, upper: float, cap: int) -> list[float]:
    """Contact lines within the legal range, bounded to ``cap`` entries. Keeps
    the origin-side lines (where compaction wants parts) plus the frontier few
    (so a new shelf can start), so the candidate product cannot explode."""
    legal = sorted({round(v, 8) for v in values if lower - EPS <= v <= upper + EPS})
    if len(legal) <= cap:
        return legal
    keep_front = max(4, cap // 8)
    return legal[: cap - keep_front] + legal[-keep_front:]


def candidate_coordinates(
    variant: Any,
    placed: Sequence[Placement],
    sheet: SheetSpec,
    max_lines: int = 60,
    max_seeds: int = 400,
) -> list[tuple[float, float]]:
    xs = {sheet.margin}
    ys = {sheet.margin}

    # Polygonal sheets: the usable region's vertices are the contact corners a
    # part can tuck into (the origin corner may not even be inside the sheet).
    region_info = sheet_usable_region(sheet)
    if region_info is not None:
        region = region_info[0]
        polys = region.geoms if isinstance(region, MultiPolygon) else [region]
        for poly in polys:
            for vx, vy in poly.exterior.coords:
                xs.update({vx, vx - variant.width})
                ys.update({vy, vy - variant.height})

    for p in placed:
        min_x, min_y, max_x, max_y = placement_bounds(p)
        xs.update({min_x, max_x + sheet.spacing, min_x - variant.width - sheet.spacing, max_x - variant.width})
        ys.update({min_y, max_y + sheet.spacing, min_y - variant.height - sheet.spacing, max_y - variant.height})
        # Concave-pocket edges are contact lines too. Aligning a candidate's
        # bbox to a pocket mouth is what lets two arch-shaped parts interlock
        # ("handshake"), with one rotated 180° dipping into the other's pocket.
        for region in placement_cavity_regions(p, sheet):
            rminx, rminy, rmaxx, rmaxy = region.bounds
            xs.update({rminx, rmaxx - variant.width})
            ys.update({rminy, rmaxy - variant.height})

    max_x_allowed = sheet.width - sheet.margin - variant.width
    max_y_allowed = sheet.height - sheet.margin - variant.height

    x_lines = _cap_axis_lines(xs, sheet.margin, max_x_allowed, max_lines)
    y_lines = _cap_axis_lines(ys, sheet.margin, max_y_allowed, max_lines)

    coords = {(x, y) for x in x_lines for y in y_lines}

    w, h = variant.width, variant.height
    union = placed_union_bounds(placed)
    ordered = sorted(
        coords,
        key=lambda c: placement_key_from_bounds((c[0], c[1], c[0] + w, c[1] + h), sheet, union),
    )
    ordered = ordered[:max_seeds]

    # Guarantee a "start a fresh shelf at the frontier" seed survives the cap and
    # is always evaluated first, so a filling sheet never spuriously spills onto
    # a new sheet while its far end is still empty.
    frontier_seeds: list[tuple[float, float]] = []
    if placed:
        if sheet.width >= sheet.height:
            fx = round(max(placement_bounds(p)[2] for p in placed) + sheet.spacing, 8)
            if sheet.margin <= fx <= max_x_allowed + EPS:
                for fy in {sheet.margin, *(y for y in y_lines if y <= max_y_allowed + EPS)}:
                    frontier_seeds.append((fx, fy))
        else:
            fy = round(max(placement_bounds(p)[3] for p in placed) + sheet.spacing, 8)
            if sheet.margin <= fy <= max_y_allowed + EPS:
                for fx in {sheet.margin, *(x for x in x_lines if x <= max_x_allowed + EPS)}:
                    frontier_seeds.append((fx, fy))
        frontier_seeds.sort(
            key=lambda c: placement_key_from_bounds((c[0], c[1], c[0] + w, c[1] + h), sheet, union)
        )

    seen = set(frontier_seeds)
    return frontier_seeds + [c for c in ordered if c not in seen]


def _frange(start: float, stop: float, step: float):
    if stop < start:
        return
    n = int(math.floor((stop - start) / step))
    for i in range(n + 1):
        yield start + i * step
    if start + n * step < stop - 1e-9:
        yield stop


def grid_coordinates(variant: Any, sheet: SheetSpec):
    max_x = sheet.width - sheet.margin - variant.width
    max_y = sheet.height - sheet.margin - variant.height
    if max_x < sheet.margin - EPS or max_y < sheet.margin - EPS:
        return

    xs = list(_frange(sheet.margin, max_x, sheet.grid_step))
    ys = list(_frange(sheet.margin, max_y, sheet.grid_step))

    if sheet.width >= sheet.height:
        for x in xs:
            for y in ys:
                yield x, y
    else:
        for y in ys:
            for x in xs:
                yield x, y


# --- compaction --------------------------------------------------------------

def _compaction_steps(sheet: SheetSpec) -> list[float]:
    coarse = max(sheet.usable_width, sheet.usable_height) / 5.0
    fine = max(min(sheet.spacing, sheet.grid_step) / 3.0, 0.008)
    steps: list[float] = []
    step = coarse
    while step > fine:
        steps.append(step)
        step /= 4.0
    steps.append(fine)
    return steps


def _try_translate(geom: Any, ox: float, oy: float, placed: Sequence[Placement], sheet: SheetSpec) -> Optional[Any]:
    moved = shp_translate(geom, xoff=ox, yoff=oy)
    if not geometry_fits_sheet(moved, sheet):
        return None
    if not is_collision_free(moved, placed, sheet.spacing, fill_holes=not sheet.allow_nesting_in_holes):
        return None
    return moved


def compact_within_region(
    geom: Any, region: Any, placed: Sequence[Placement], sheet: SheetSpec
) -> Any:
    """Compact toward the origin corner while never leaving ``region``.

    Plain compaction moves in discrete jumps and only checks the destination,
    so a coarse step can tunnel straight through a thin wall -- e.g. a tile
    escaping a scrap hole through its 1-in rail. Constraining every accepted
    step to keep the part's interior point inside the containing region makes
    hole packing airtight regardless of step size."""
    if not sheet.compact:
        return geom

    fine = max(min(sheet.spacing, sheet.grid_step) / 3.0, 0.008)
    steps = [max(sheet.spacing * 2.0, 0.1), fine]
    axis_order = ("x", "y") if sheet.width >= sheet.height else ("y", "x")

    for _ in range(3):
        moved_any = False
        for axis in axis_order:
            for step in steps:
                while True:
                    ox, oy = (-step, 0.0) if axis == "x" else (0.0, -step)
                    nxt = _try_translate(geom, ox, oy, placed, sheet)
                    if nxt is None or not region.contains(nxt.representative_point()):
                        break
                    geom = nxt
                    moved_any = True
        if not moved_any:
            break
    return geom


def _variant_light_geometry(variant: Any) -> Any:
    """Vertex-light SUPERSET of the variant outline, used only inside
    compaction. Compaction translates the geometry at every slide step, which
    dominated runtime on dense CAD outlines. Because the light shape strictly
    contains the exact one, any position it can occupy is also valid for the
    exact part -- compaction merely stops ~0.02 in short of perfect contact.
    Seed feasibility and the final placement always use exact geometry."""
    cached = getattr(variant, "_light_geom", None)
    if cached is not None:
        return cached
    tol = 0.01
    g = variant.geometry
    try:
        s = g.simplify(tol, preserve_topology=True).buffer(tol, quad_segs=1)
        if s.is_empty or not s.is_valid:
            s = g
    except Exception:
        s = g
    variant._light_geom = s
    return s


def _compact_exact_via_light(
    geom: Any,
    variant: Any,
    x: float,
    y: float,
    placed: Sequence[Placement],
    sheet: SheetSpec,
    region: Optional[Any] = None,
) -> Any:
    """Compact the light superset, then shift the exact geometry by the same
    offset. Safe: superset-valid positions are exact-valid positions."""
    light0 = _variant_light_geometry(variant)
    if light0 is variant.geometry:
        # Simplification failed; fall back to compacting the exact geometry.
        if region is not None:
            return compact_within_region(geom, region, placed, sheet)
        return compact_toward_origin(geom, placed, sheet)

    lgeom = shp_translate(light0, xoff=x, yoff=y)
    if region is not None:
        moved = compact_within_region(lgeom, region, placed, sheet)
    else:
        moved = compact_toward_origin(lgeom, placed, sheet)
    ddx = moved.bounds[0] - lgeom.bounds[0]
    ddy = moved.bounds[1] - lgeom.bounds[1]
    if abs(ddx) > 1e-12 or abs(ddy) > 1e-12:
        geom = shp_translate(geom, xoff=ddx, yoff=ddy)
    return geom


def _containing_scrap_hole(geom: Any, placed: Sequence[Placement], sheet: SheetSpec) -> Optional[Any]:
    """The scrap hole (of any placed part) that contains this geometry's
    interior point, if any."""
    from .holes import placement_scrap_holes

    try:
        pt = geom.representative_point()
    except Exception:
        return None
    for q in placed:
        for hole in placement_scrap_holes(q, sheet):
            if hole.contains(pt):
                return hole
    return None


def compact_toward_origin(geom: Any, placed: Sequence[Placement], sheet: SheetSpec) -> Any:
    """Bottom-left compaction: slide the part toward the origin corner along the
    long axis first, then the short axis, using real geometry. Closes the gaps a
    coarse seed leaves and lets a flipped airfoil settle into a neighbour's
    concavity."""
    if not sheet.compact:
        return geom

    steps = _compaction_steps(sheet)
    axis_order = ("x", "y") if sheet.width >= sheet.height else ("y", "x")

    moved_any = True
    guard = 0
    while moved_any and guard < 3:
        moved_any = False
        guard += 1
        for axis in axis_order:
            for step in steps:
                while True:
                    ox, oy = (-step, 0.0) if axis == "x" else (0.0, -step)
                    nxt = _try_translate(geom, ox, oy, placed, sheet)
                    if nxt is None:
                        break
                    geom = nxt
                    moved_any = True
    return geom


# --- placement / packing -----------------------------------------------------

def _bbox_fits_sheet(
    x: float, y: float, w: float, h: float, sheet: SheetSpec, tol: float = 1e-7
) -> bool:
    return (
        x >= sheet.margin - tol
        and y >= sheet.margin - tol
        and x + w <= sheet.width - sheet.margin + tol
        and y + h <= sheet.height - sheet.margin + tol
    )


def find_placement(
    item: Item,
    placed: Sequence[Placement],
    sheet: SheetSpec,
    sheet_index: int,
    compact_top_k: int = 4,
    eval_budget: int = 2000,
) -> Optional[Placement]:
    """Best position for one item given everything already placed.

    Candidate ranking keys depend only on the candidate's bounding box, which is
    known analytically from (x, y, w, h) -- so seeds are sorted BEFORE any
    geometry work, and the part outline is translated (the expensive step for
    dense CAD outlines) only until ``compact_top_k`` feasible candidates are
    found. This is what keeps 20-part sheets in seconds instead of minutes."""
    best: Optional[Placement] = None
    best_key: Optional[tuple] = None
    best_hole: Optional[Placement] = None
    best_hole_key: Optional[tuple] = None
    evals = 0
    fill_holes = not sheet.allow_nesting_in_holes
    union = placed_union_bounds(placed)
    hull_pts = placed_hull_coords(placed)
    # NFP seeds are dense and mostly feasible; give the search room to
    # actually reach the tight interior gaps they propose.
    eval_budget = max(eval_budget, 3500)
    compact_top_k = max(compact_top_k, 6)

    for variant in item.variants:
        w, h = variant.width, variant.height
        if w > sheet.usable_width + EPS or h > sheet.usable_height + EPS:
            continue

        # A part that drops into another part's scrap cut-out consumes pure
        # waste and adds nothing to the footprint, so any feasible hole
        # placement beats every open-sheet placement outright -- it must not
        # merely compete on the origin-distance key (it would lose to open
        # space near the origin and the cut-out would stay empty).
        if sheet.allow_nesting_in_holes:
            hole_cands = sorted(
                (
                    (placement_key_from_bounds((x, y, x + w, y + h), sheet, union), x, y)
                    for x, y in hole_candidate_seeds(variant, placed, sheet)
                    if _bbox_fits_sheet(x, y, w, h, sheet)
                ),
                key=lambda t: t[0],
            )
            found = 0
            for _, x, y in hole_cands:
                if found >= compact_top_k or evals >= eval_budget:
                    break
                geom = shp_translate(variant.geometry, xoff=x, yoff=y)
                evals += 1
                if sheet.boundary is not None and not geometry_fits_sheet(geom, sheet):
                    continue
                if not is_collision_free(geom, placed, sheet.spacing):
                    continue
                found += 1
                # Compact within the hole -- constrained so the part can never
                # tunnel out through a wall -- snugging it against the hole's
                # origin-side corner / already-nested neighbours instead of
                # floating mid-hole.
                region = _containing_scrap_hole(geom, placed, sheet)
                if region is not None:
                    geom = _compact_exact_via_light(geom, variant, x, y, placed, sheet, region)
                key = placement_key_from_bounds(geom.bounds, sheet, union)
                if best_hole_key is None or key < best_hole_key:
                    best_hole_key = key
                    b = geom.bounds
                    best_hole = Placement(item, variant, sheet_index, b[0], b[1], geom)

        # Concave-pocket seeds join the normal ranking: pockets are ordinary
        # stock, but bounding-box contact candidates never propose positions
        # inside another part's hull, so an arch cavity would stay empty.
        seeds = cavity_candidate_seeds(variant, placed, sheet) + candidate_coordinates(
            variant, placed, sheet
        )
        # No-fit-polygon seeds are exact-contact positions along every placed
        # part's spacing-inflated silhouette -- the tight spots (a disc
        # nestling between two discs, mating slanted edges) that axis-aligned
        # contact lines never propose. They compete on the same ranking.
        if placed:
            seeds = nfp_candidate_seeds(variant, placed, sheet) + seeds
        cands = sorted(
            (
                (placement_key_from_bounds((x, y, x + w, y + h), sheet, union), x, y)
                for x, y in dict.fromkeys(seeds)
                if _bbox_fits_sheet(x, y, w, h, sheet)
            ),
            key=lambda t: t[0],
        )

        # Positions inside the current union bbox all tie on its area, so the
        # bbox key alone cannot tell a gap-filling position from one that
        # merely stretches the cluster sideways. Re-rank the analytic front by
        # cluster-hull growth: gap fillers add no hull area and float to the
        # top, frontier positions sink.
        if hull_pts is not None and len(cands) > 1:
            prefix = cands[:150]
            prefix.sort(
                key=lambda t: (
                    t[0][0],
                    round(hull_area_with(hull_pts, (t[1], t[2], t[1] + w, t[2] + h)), 6),
                )
                + t[0][1:]
            )
            cands = prefix + cands[150:]

        found = 0
        for _, x, y in cands:
            if found >= compact_top_k or evals >= eval_budget:
                break
            geom = shp_translate(variant.geometry, xoff=x, yoff=y)
            evals += 1
            if sheet.boundary is not None and not geometry_fits_sheet(geom, sheet):
                continue
            if not is_collision_free(geom, placed, sheet.spacing, fill_holes=fill_holes):
                continue
            found += 1
            geom = _compact_exact_via_light(geom, variant, x, y, placed, sheet)
            b = geom.bounds
            key = refined_placement_key(b, sheet, union, hull_pts)
            if best_key is None or key < best_key:
                best_key = key
                best = Placement(item, variant, sheet_index, b[0], b[1], geom)

        if found:
            continue

        # Fallback raster-like search for positions the contact candidates miss
        # (mostly relevant on a fresh sheet). Bounded by the evaluation budget.
        for x, y in grid_coordinates(variant, sheet):
            if evals >= eval_budget:
                break
            geom = shp_translate(variant.geometry, xoff=x, yoff=y)
            evals += 1
            if sheet.boundary is not None and not geometry_fits_sheet(geom, sheet):
                continue
            if not is_collision_free(geom, placed, sheet.spacing, fill_holes=fill_holes):
                continue
            geom = _compact_exact_via_light(geom, variant, x, y, placed, sheet)
            b = geom.bounds
            key = refined_placement_key(b, sheet, union, hull_pts)
            if best_key is None or key < best_key:
                best_key = key
                best = Placement(item, variant, sheet_index, b[0], b[1], geom)
                break

    # Scrap-hole placements win outright over open-sheet placements.
    if best_hole is not None:
        return best_hole
    return best


def item_metric(item: Item, mode: int, rng: random.Random) -> tuple:
    geom = item.part.geometry
    min_x, min_y, max_x, max_y = geom.bounds
    w = max_x - min_x
    h = max_y - min_y
    area = max(float(geom.area), EPS)
    envelope = w * h
    max_dim = max(w, h)
    min_dim = min(w, h)

    if mode == 0:
        return (-envelope, -area, -max_dim, item.uid)
    if mode == 1:
        return (-max_dim, -envelope, -area, item.uid)
    if mode == 2:
        return (-min_dim, -max_dim, -area, item.uid)

    jitter = rng.random()
    return (-envelope * (0.92 + 0.16 * jitter), -area, jitter, item.uid)


def score_layout(sheets: Sequence[SheetLayout], sheet: SheetSpec, unplaced_count: int = 0) -> tuple:
    # Objectives, most important first:
    #   place everything -> fewest sheets -> smallest used rectangle (so the
    #   off-cut stays one usable rectangle) -> tightest cluster (smallest
    #   convex hull, i.e. gaps filled) -> shortest run along the long stock
    #   dimension.
    used_long_total = 0.0
    bbox_area_total = 0.0
    hull_area_total = 0.0

    for layout in sheets:
        if not layout.placements:
            continue
        min_x = min(placement_bounds(p)[0] for p in layout.placements)
        min_y = min(placement_bounds(p)[1] for p in layout.placements)
        max_x = max(placement_bounds(p)[2] for p in layout.placements)
        max_y = max(placement_bounds(p)[3] for p in layout.placements)

        if sheet.width >= sheet.height:
            used_long_total += max_x - sheet.margin
        else:
            used_long_total += max_y - sheet.margin
        bbox_area_total += (max_x - min_x) * (max_y - min_y)

        pts = placed_hull_coords(layout.placements)
        if pts:
            hull_area_total += _hull_area(pts)

    return (
        unplaced_count,
        len(sheets),
        round(bbox_area_total, 8),
        round(hull_area_total, 8),
        round(used_long_total, 8),
    )


def pack_in_order(ordered: Sequence[Item], sheet: SheetSpec) -> LayoutResult:
    """Greedy packing of items in exactly the given insertion order. This is
    the evaluation primitive shared by the multi-pass heuristic (which derives
    orders from sorting metrics) and the genetic algorithm (which evolves the
    order directly)."""
    sheets: list[SheetLayout] = []
    unplaced: list[Item] = []

    for item in ordered:
        chosen: Optional[Placement] = None

        for sheet_index, layout in enumerate(sheets):
            placement = find_placement(item, layout.placements, sheet, sheet_index)
            if placement is not None:
                chosen = placement
                break

        if chosen is None:
            if sheet.max_sheets is not None and len(sheets) >= sheet.max_sheets:
                unplaced.append(item)
                continue

            new_index = len(sheets)
            layout = SheetLayout()
            placement = find_placement(item, layout.placements, sheet, new_index)
            if placement is None:
                unplaced.append(item)
                continue
            sheets.append(layout)
            chosen = placement

        sheets[chosen.sheet_index].placements.append(chosen)

    score = score_layout(sheets, sheet, len(unplaced))
    return LayoutResult(sheets=sheets, score=score, unplaced=unplaced)


def pack_once(items: Sequence[Item], sheet: SheetSpec, mode: int, seed: int) -> LayoutResult:
    rng = random.Random(seed)
    ordered = sorted(items, key=lambda item: item_metric(item, mode, rng))
    return pack_in_order(ordered, sheet)


def polish_layout(result: LayoutResult, sheet: SheetSpec, sweeps: int = 2) -> LayoutResult:
    """Reinsertion polish: repeatedly pull each placed part out and re-place it
    with full knowledge of every other part's final position.

    Greedy packing is sequential, so early parts are placed knowing nothing
    about later ones -- a part can end up stretching the footprint while a
    pocket or scrap hole that opens up later stays empty. Re-inserting each
    part (footprint-boundary parts first, since they define the union bbox)
    lets it migrate into those spots. A move is kept only when the global
    layout score strictly improves, so the pass can only tighten the nest."""
    if not result.sheets:
        return result

    for _ in range(sweeps):
        improved = False
        current = score_layout(result.sheets, sheet, len(result.unplaced))

        moves: list[tuple[int, Placement]] = []
        max_area = 0.0
        for si, layout in enumerate(result.sheets):
            for p in layout.placements:
                b = placement_bounds(p)
                area = (b[2] - b[0]) * (b[3] - b[1])
                max_area = max(max_area, area)
                moves.append((si, p))

        # Only revisit parts that can plausibly improve the layout: those on the
        # footprint boundary (they define the union bbox) or small parts (they
        # can dive into pockets/holes). Large mid-block parts have nowhere
        # better to go and re-searching them dominates polish cost.
        def worth_moving(entry: tuple[int, Placement]) -> bool:
            si, p = entry
            ub = placed_union_bounds(result.sheets[si].placements)
            if ub is None:
                return False
            b = placement_bounds(p)
            on_boundary = (
                abs(b[0] - ub[0]) < 1e-6
                or abs(b[1] - ub[1]) < 1e-6
                or abs(b[2] - ub[2]) < 1e-6
                or abs(b[3] - ub[3]) < 1e-6
            )
            area = (b[2] - b[0]) * (b[3] - b[1])
            return on_boundary or area <= 0.35 * max_area

        moves = [m for m in moves if worth_moving(m)]

        def boundary_priority(entry: tuple[int, Placement]) -> tuple:
            si, p = entry
            ub = placed_union_bounds(result.sheets[si].placements)
            b = placement_bounds(p)
            touches = 0
            if ub is not None:
                touches = (
                    int(abs(b[0] - ub[0]) < 1e-6)
                    + int(abs(b[1] - ub[1]) < 1e-6)
                    + int(abs(b[2] - ub[2]) < 1e-6)
                    + int(abs(b[3] - ub[3]) < 1e-6)
                )
            area = (b[2] - b[0]) * (b[3] - b[1])
            # Boundary parts first (they define the footprint), small ones first
            # within a tier (they relocate into pockets most easily).
            return (-touches, area, p.item.uid)

        moves.sort(key=boundary_priority)

        for si, p in moves:
            layout = result.sheets[si]
            if p not in layout.placements:
                continue
            layout.placements.remove(p)

            best_alt: Optional[tuple[tuple, Placement, int]] = None
            for sj, lay in enumerate(result.sheets):
                cand = find_placement(p.item, lay.placements, sheet, sj)
                if cand is None:
                    continue
                lay.placements.append(cand)
                sc = score_layout(result.sheets, sheet, len(result.unplaced))
                lay.placements.remove(cand)
                if best_alt is None or sc < best_alt[0]:
                    best_alt = (sc, cand, sj)

            if best_alt is not None and best_alt[0] < current:
                _, cand, sj = best_alt
                result.sheets[sj].placements.append(cand)
                current = best_alt[0]
                improved = True
            else:
                layout.placements.append(p)  # restore original spot

        # A sweep can empty a sheet entirely; drop it.
        non_empty = [l for l in result.sheets if l.placements]
        if non_empty:
            result.sheets = non_empty

        if not improved:
            break

    # The tightened layout may now have room for parts that never fit.
    still_unplaced: list[Item] = []
    for item in result.unplaced:
        placed_now = False
        for sj, lay in enumerate(result.sheets):
            cand = find_placement(item, lay.placements, sheet, sj)
            if cand is not None:
                lay.placements.append(cand)
                placed_now = True
                break
        if not placed_now:
            still_unplaced.append(item)
    result.unplaced = still_unplaced

    result.score = score_layout(result.sheets, sheet, len(result.unplaced))
    return result


def heuristic_passes(
    items: Sequence[Item], sheet: SheetSpec, seed: int = 42
):
    """Generator yielding ``(pass_index, best_so_far)`` after each heuristic
    packing pass, with the same early-stopping rule as
    :func:`optimize_layout`. Lets front-ends stream a live preview per pass.
    The caller should run :func:`polish_layout` on the final result."""
    best: Optional[LayoutResult] = None

    # Always run the distinct deterministic heuristics (modes 0-3) at least once,
    # then keep trying randomized orders only while they keep improving.
    min_passes = min(sheet.passes, 4)
    patience = 3
    since_improvement = 0

    for pass_index in range(sheet.passes):
        mode = pass_index if pass_index < 3 else 3
        result = pack_once(items, sheet, mode=mode, seed=seed + pass_index * 7919)
        if best is None or result.score < best.score:
            best = result
            since_improvement = 0
        else:
            since_improvement += 1
        yield pass_index, best
        if pass_index + 1 >= min_passes and since_improvement >= patience:
            break


def optimize_layout(
    items: Sequence[Item],
    sheet: SheetSpec,
    seed: int = 42,
    on_pass: Optional[Any] = None,
) -> LayoutResult:
    """``on_pass(pass_index, total_passes)`` reports per-pass progress."""
    best: Optional[LayoutResult] = None
    for pass_index, best in heuristic_passes(items, sheet, seed=seed):
        if on_pass is not None:
            on_pass(pass_index, sheet.passes)

    assert best is not None
    return polish_layout(best, sheet)


class Nester:
    """Object-oriented facade over the packing functions. Swap this class (or
    its ``optimize`` method) to drop in a different nesting strategy."""

    def __init__(self, sheet: SheetSpec, seed: int = 42) -> None:
        self.sheet = sheet
        self.seed = seed

    def optimize(self, items: Sequence[Item]) -> LayoutResult:
        return optimize_layout(items, self.sheet, seed=self.seed)

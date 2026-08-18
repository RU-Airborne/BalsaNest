"""No-fit-polygon candidate generation.

The NFP of a placed part A and an orbiting part B bounds the positions of B
that overlap A, so its boundary is exact contact. Ranking those positions finds
the tight spots that axis-aligned contact lines and bottom-left sliding never
propose: a disc nestling between two discs, a hypotenuse mating a hypotenuse.

Per (placed variant, incoming variant) pair, cached on the variants:

1. Reduce each outline to a vertex-light superset (holes filled, simplified,
   buffered back out by the simplification tolerance).
2. Decompose that into convex pieces, Delaunay triangles greedily merged while
   their union stays convex.
3. NFP = union over piece pairs of conv(A_i (+) -B_j). The Minkowski sum of
   two convex polygons is the convex hull of the pairwise vertex sums.
4. Inflate by the part spacing to put the boundary at minimum separation.

Seeds are the boundary vertices, densified along long edges, of the inner-fit
region minus the union of translated NFPs. Everything here works on supersets
and gets confirmed by the exact collision test afterwards, and true contact is
reached by the existing exact-geometry compaction.
"""

import math
from typing import Any, Optional, Sequence

import numpy as np
from shapely.affinity import translate as shp_translate
from shapely.geometry import GeometryCollection, MultiPoint, MultiPolygon, Polygon, box
from shapely.ops import triangulate, unary_union

from .constants import EPS
from .models import Placement, SheetSpec, Variant, placement_bounds

# Simplification of outlines before decomposition. Larger = faster but looser
# seeds (compaction recovers the slack against exact geometry afterwards). The
# tolerance is HARD-CAPPED: CAD outlines sampled at ~0.015 in carry thousands
# of vertices, and letting the tolerance grow until a vertex budget is met
# inflates the superset by whole inches. The NFPs then forbid genuinely free
# gaps and the packer degenerates to frontier placements. Slack must stay in
# the same order as the part spacing, whatever the vertex count.
_SIMPLIFY_TOL = 0.005
_SIMPLIFY_TOL_CAP = 0.02
_MAX_OUTLINE_VERTS = 80
# A merged piece may exceed its exact union by this relative area and still be
# replaced by its convex hull. A hull is always a superset of what it covers.
_CONVEX_MERGE_REL_TOL = 0.01
# Cap on |A pieces| * |B pieces| before falling back to hull-only NFPs.
_MAX_PIECE_PRODUCT = 1500
_DEFAULT_MAX_SEEDS = 900
_MAX_IFP_PIECES = 8


def _iter_polygons(geom: Any):
    if isinstance(geom, Polygon):
        if not geom.is_empty:
            yield geom
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        for g in geom.geoms:
            yield from _iter_polygons(g)


def _filled_exterior(geom: Any) -> Any:
    """Outer boundary only. Interior cut-outs are handled by the dedicated
    scrap-hole nesting logic: for open-sheet NFPs a part counts as solid."""
    polys = [Polygon(p.exterior) for p in _iter_polygons(geom)]
    if not polys:
        return geom
    if len(polys) == 1:
        return polys[0]
    return MultiPolygon(polys)


def _count_verts(geom: Any) -> int:
    return sum(len(p.exterior.coords) for p in _iter_polygons(geom))


def _light_solid(variant: Variant) -> Any:
    """Vertex-light superset of the variant outline (holes filled), cached.

    Simplification pulls the boundary inward by at most ``tol``. Buffering back
    out by slightly more than ``tol`` restores a strict superset, which keeps
    every contact position derived from it feasible for the exact outline."""
    cached = getattr(variant, "_nfp_solid", None)
    if cached is not None:
        return cached

    filled = _filled_exterior(variant.geometry)
    tol = _SIMPLIFY_TOL
    solid = filled
    while True:
        try:
            s = filled.simplify(tol, preserve_topology=True)
        except Exception:
            s = filled
        if s.is_empty or not s.is_valid:
            s = filled
        solid = s
        # Vertex count is a soft target only. The tolerance cap is what keeps
        # the superset slack bounded. A rich outline beats a fat one.
        if _count_verts(s) <= _MAX_OUTLINE_VERTS or s is filled or tol >= _SIMPLIFY_TOL_CAP:
            break
        tol = min(tol * 1.6, _SIMPLIFY_TOL_CAP)
    try:
        # Mitre with a tight limit: ordinary corners (up to 90 degrees, mitre
        # ratio sqrt(2) <= 1.5) stay exactly sharp. The later mitred spacing
        # inflation then reproduces the clearance zone's corner spikes and corner
        # seeds stay feasible. Needle-sharp corners (spar tabs) are beveled at
        # a bounded 1.5 x tol instead of the default 5 x tol. With the
        # default limit those spikes silently fattened parts by ~0.06 in.
        buffered = solid.buffer(tol * 1.05, join_style="mitre", mitre_limit=1.5)
        if not buffered.is_empty and buffered.is_valid:
            solid = buffered
        else:
            solid = filled
    except Exception:
        solid = filled

    variant._nfp_solid = solid
    return solid


def _greedy_convex_merge(pieces: list[Polygon], rel_tol: float = _CONVEX_MERGE_REL_TOL) -> list[Polygon]:
    """Merge touching pieces while the merged hull stays within ``rel_tol`` of
    the exact union area. Replacing the union with its hull leaves a small set
    of genuinely convex, slightly-inflated pieces.

    A pure-Python bounding-box prefilter keeps the pair scan cheap even for a
    few hundred triangles. Only bbox-touching pairs pay for shapely calls."""
    pieces = sorted(pieces, key=lambda p: (round(p.centroid.x, 6), round(p.centroid.y, 6)))
    merged = True
    while merged:
        merged = False
        out: list[Polygon] = []
        bounds = [p.bounds for p in pieces]
        used = [False] * len(pieces)
        for i in range(len(pieces)):
            if used[i]:
                continue
            cur = pieces[i]
            cb = bounds[i]
            for j in range(i + 1, len(pieces)):
                if used[j]:
                    continue
                b = bounds[j]
                if b[0] > cb[2] + 1e-9 or cb[0] > b[2] + 1e-9:
                    continue
                if b[1] > cb[3] + 1e-9 or cb[1] > b[3] + 1e-9:
                    continue
                if not cur.intersects(pieces[j]):
                    continue
                u = cur.union(pieces[j])
                if not isinstance(u, Polygon):
                    continue
                hull = u.convex_hull
                if hull.area <= u.area * (1.0 + rel_tol) + 1e-12:
                    cur = hull
                    cb = cur.bounds
                    used[j] = True
                    merged = True
            out.append(cur)
        pieces = out
    return pieces


def _ring_array(poly: Polygon) -> np.ndarray:
    return np.asarray(poly.exterior.coords, dtype=float)[:-1]


def _convex_pieces(variant: Variant, inflate: float = 0.0) -> list[np.ndarray]:
    """Convex decomposition of the variant's light superset outline, cached as
    vertex arrays ready for Minkowski summation.

    ``inflate`` grows the outline with the SAME buffer parameters the packer's
    clearance zone uses (mitred joins). This matters: a mitred zone demands
    sqrt(2) x spacing corner-to-corner. Inflate an NFP with a round (Euclidean)
    buffer instead and it proposes corner seeds the collision test rejects."""
    cache = getattr(variant, "_nfp_pieces", None)
    if cache is None:
        cache = {}
        variant._nfp_pieces = cache
    key = round(inflate, 9)
    if key in cache:
        return cache[key]

    solid = _light_solid(variant)
    if inflate > 0.0:
        try:
            grown = solid.buffer(inflate, quad_segs=2, join_style="mitre")
            if not grown.is_empty and grown.is_valid:
                solid = grown
        except Exception:
            pass

    pieces: list[np.ndarray] = []
    for poly in _iter_polygons(solid):
        outer = Polygon(poly.exterior)
        hull = outer.convex_hull
        if not isinstance(hull, Polygon) or hull.is_empty:
            continue
        if hull.area <= outer.area * (1.0 + _CONVEX_MERGE_REL_TOL) + 1e-12:
            pieces.append(_ring_array(hull))
            continue
        try:
            tris = [t for t in triangulate(outer) if outer.contains(t.representative_point())]
        except Exception:
            tris = []
        if not tris:
            pieces.append(_ring_array(hull))
            continue
        for part in _greedy_convex_merge(tris):
            h = part.convex_hull
            if isinstance(h, Polygon) and not h.is_empty:
                pieces.append(_ring_array(h))

    cache[key] = pieces
    return pieces


def _hull_piece(pieces: Sequence[np.ndarray]) -> list[np.ndarray]:
    hull = MultiPoint(np.concatenate(pieces)).convex_hull
    if isinstance(hull, Polygon):
        return [_ring_array(hull)]
    return list(pieces)


def _minkowski_convex(a: np.ndarray, b: np.ndarray) -> Optional[Polygon]:
    """Minkowski sum of two convex vertex sets = hull of pairwise vertex sums."""
    sums = (a[:, None, :] + b[None, :, :]).reshape(-1, 2)
    hull = MultiPoint(sums).convex_hull
    if isinstance(hull, Polygon) and not hull.is_empty:
        return hull
    return None


def nfp_for_pair(static_variant: Variant, orbit_variant: Variant, spacing: float) -> Optional[Any]:
    """NFP of ``orbit_variant`` around ``static_variant``, both in their local
    (bbox-min-at-origin) frames, inflated by ``spacing``.

    A position (x, y) for the orbiting variant collides with the static variant
    placed at its local origin iff (x, y) lies inside this region. The boundary
    is exact contact at the minimum legal separation. Cached per (static
    variant, spacing) on the orbiting variant. Returns None when the
    computation fails (callers then simply skip NFP seeds for this pair)."""
    key = (id(static_variant), round(spacing, 9))
    cache = getattr(orbit_variant, "_nfp_cache", None)
    if cache is None:
        cache = {}
        orbit_variant._nfp_cache = cache
    hit = cache.get(key)
    # The cached entry keeps a strong reference to the static variant so a
    # recycled id() can never alias a different variant.
    if hit is not None and hit[0] is static_variant:
        return hit[1]

    nfp: Optional[Any] = None
    try:
        # The spacing is folded into the static side with the clearance zone's
        # own (mitred) buffer semantics. The NFP boundary then sits exactly on
        # the edge of what is_collision_free accepts.
        a_pieces = _convex_pieces(static_variant, inflate=spacing)
        b_pieces = _convex_pieces(orbit_variant)
        if a_pieces and b_pieces:
            if len(a_pieces) * len(b_pieces) > _MAX_PIECE_PRODUCT:
                # Hull-only fallback for the more fragmented side: coarser (the
                # NFP grows to the hull) but always safe.
                if len(a_pieces) >= len(b_pieces):
                    a_pieces = _hull_piece(a_pieces)
                else:
                    b_pieces = _hull_piece(b_pieces)
                if len(a_pieces) * len(b_pieces) > _MAX_PIECE_PRODUCT:
                    a_pieces = _hull_piece(a_pieces)
                    b_pieces = _hull_piece(b_pieces)
            reflected = [-arr for arr in b_pieces]
            hulls = [
                h
                for a in a_pieces
                for b in reflected
                if (h := _minkowski_convex(a, b)) is not None
            ]
            if hulls:
                region = unary_union(hulls)
                if not region.is_empty and region.is_valid:
                    nfp = region
    except Exception:
        nfp = None

    cache[key] = (static_variant, nfp)
    return nfp


def _boundary_segments(region: Any, tol: float) -> list[np.ndarray]:
    segments: list[np.ndarray] = []
    for poly in _iter_polygons(region):
        for ring in (poly.exterior, *poly.interiors):
            try:
                simple = ring.simplify(tol)
            except Exception:
                simple = ring
            coords = np.asarray(simple.coords, dtype=float)
            if len(coords) < 2:
                continue
            for i in range(len(coords) - 1):
                segments.append(coords[i : i + 2])
    return segments


def inner_fit_region(variant: Variant, sheet: SheetSpec) -> Optional[Any]:
    if sheet.boundary is None:
        return None

    cache = getattr(variant, "_ifp_cache", None)
    if cache is not None and cache[0] is sheet:
        return cache[1]

    from .packing import sheet_usable_region
    region_info = sheet_usable_region(sheet)
    if region_info is None:
        return None
    region = region_info[0]

    pieces = _convex_pieces(variant)
    if not pieces:
        return None
    if len(pieces) > _MAX_IFP_PIECES:
        # The band only has to contain the true one. Collapsing to the hull
        # shrinks the fit region, which costs seeds and never validity.
        pieces = _hull_piece(pieces)
    reflected = [-arr for arr in pieces]

    tol = max(sheet.grid_step, 0.02)
    segments = _boundary_segments(region, tol)
    if not segments:
        return None

    bands = [
        band
        for seg in segments
        for piece in reflected
        if (band := _minkowski_convex(seg, piece)) is not None
    ]
    if not bands:
        return None

    w, h = variant.width, variant.height
    margin_box = box(
        sheet.margin,
        sheet.margin,
        sheet.width - sheet.margin - w,
        sheet.height - sheet.margin - h,
    )
    try:
        free = margin_box.difference(unary_union(bands))
    except Exception:
        return None

    keep = []
    for poly in _iter_polygons(free):
        try:
            rep = poly.representative_point()
            placed = shp_translate(variant.geometry, xoff=rep.x, yoff=rep.y)
        except Exception:
            continue
        if region.contains(placed):
            keep.append(poly)

    result = unary_union(keep) if keep else None
    if result is not None and result.is_empty:
        result = None
    variant._ifp_cache = (sheet, result)
    return result


def nfp_candidate_seeds(
    variant: Variant,
    placed: Sequence[Placement],
    sheet: SheetSpec,
    max_seeds: int = _DEFAULT_MAX_SEEDS,
) -> list[tuple[float, float]]:
    """Exact-contact candidate positions for ``variant`` given everything
    already placed: boundary points of (inner-fit rectangle minus the union of
    every placed part's spacing-inflated NFP).

    Every returned position either hugs a placed part at the minimum spacing or
    rides the sheet margin, which is where the tight spots are. Interior rings of
    the free region (positions locked inside a concave enclosure) are included
    too. Parts are laser-cut rather than slid into place, which makes an
    unreachable-by-sliding position perfectly valid."""
    if not placed:
        return []
    w, h = variant.width, variant.height
    x_lo, y_lo = sheet.margin, sheet.margin
    x_hi = sheet.width - sheet.margin - w
    y_hi = sheet.height - sheet.margin - h
    if x_hi < x_lo - EPS or y_hi < y_lo - EPS:
        return []

    forbidden = []
    for p in placed:
        nfp = nfp_for_pair(p.variant, variant, sheet.spacing)
        if nfp is None:
            continue
        b = placement_bounds(p)
        forbidden.append(shp_translate(nfp, xoff=b[0], yoff=b[1]))
    if not forbidden:
        return []

    pad = 1e-9  # keeps the box non-degenerate when the part exactly fills an axis
    ifp = box(x_lo - pad, y_lo - pad, x_hi + pad, y_hi + pad)
    fit = inner_fit_region(variant, sheet)
    if fit is not None:
        ifp = ifp.intersection(fit)
        if ifp.is_empty:
            return []
    try:
        free = ifp.difference(unary_union(forbidden))
    except Exception:
        return []
    if free.is_empty:
        return []

    step = max(sheet.grid_step, 0.03)
    verts: list[tuple[float, float]] = []
    extras: list[tuple[float, float]] = []
    # Islands first: a small disconnected free-region polygon IS a gap between
    # placed parts, exactly the spot a frontier placement would waste. Its
    # seeds must survive any capping. The big outer boundary goes last.
    polys = sorted(_iter_polygons(free), key=lambda p: p.area)
    for poly in polys:
        try:
            rep = poly.representative_point()
            # Strictly interior, hence guaranteed feasible: a safe anchor even
            # if every boundary vertex of this gap is numerically marginal.
            verts.append((round(rep.x, 8), round(rep.y, 8)))
        except Exception:
            pass
        for ring in (poly.exterior, *poly.interiors):
            coords = list(ring.coords)
            for i in range(len(coords) - 1):
                x0, y0 = coords[i]
                x1, y1 = coords[i + 1]
                verts.append((round(x0, 8), round(y0, 8)))
                dist = math.hypot(x1 - x0, y1 - y0)
                if dist > step:
                    n = min(int(dist / step), 40)
                    for k in range(1, n + 1):
                        t = k / (n + 1.0)
                        extras.append(
                            (round(x0 + (x1 - x0) * t, 8), round(y0 + (y1 - y0) * t, 8))
                        )

    # Region vertices carry the tight spots (NFP-NFP intersections, corners),
    # keep them all and spend whatever room is left on densified edge points.
    # When capping, truncate from the END so island seeds (listed first) win.
    seeds = list(dict.fromkeys(verts))
    if len(seeds) > max_seeds:
        return seeds[:max_seeds]
    room = max_seeds - len(seeds)
    if room > 0 and extras:
        seen = set(seeds)
        extras = [e for e in dict.fromkeys(extras) if e not in seen]
        if len(extras) > room:
            stride = math.ceil(len(extras) / room)
            extras = extras[::stride]
        seeds.extend(extras)
    return seeds

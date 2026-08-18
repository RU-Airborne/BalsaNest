"""Shrink-and-repair pass over a finished layout.

Greedy insertion and the reinsertion polish both move one part at a time into
somewhere already free. Both stall when the next gain needs two parts to
move together. The way out, from sparrow (Gardeyn & Wauters, 2025): pull the
used length in a little, let the parts overlap, then push them apart again.
Going through an infeasible state is what lets a cluster rearrange at once.

Overlap is measured as intersection area so there is something to descend, and
pairs that keep colliding get weighted up until one of them gives up and jumps
elsewhere. A squeeze is kept only if the result is legal and scores better, so
the pass either tightens the nest or leaves it alone.
"""

import random
from typing import Any, Callable, Optional, Sequence

from shapely.geometry import box as shp_box

from .models import LayoutResult, Placement, SheetSpec, placement_bounds
from .packing import (
    _filled_geometry,
    bbox_farther_than_spacing,
    geometry_fits_sheet,
    score_layout,
    sheet_usable_region,
    shp_translate,
)

_SHRINK_STEP = 0.004        # of the used length, per round

_MAX_SWEEPS = 60

# The last round of a run always fails, by definition, and letting it burn all
# 60 sweeps was most of what this pass cost.
_PATIENCE = 12              # sweeps without improvement before giving up

# Near samples settle a part, far ones let it abandon a fight it keeps losing.
_NEAR_SAMPLES = 8
_FAR_SAMPLES = 4

_WEIGHT_GROWTH = 1.4
_WEIGHT_CAP = 2.0

_REFINE_FLOOR = 0.001       # smallest slide worth trying, inches


class _Body:
    """One placement while it is being pushed around.

    ``zone`` is the outline grown by half the spacing with a round join. Two
    zones overlap exactly when the parts are closer than the spacing. ``shape``
    is the bare outline, and is what has to stay inside the sheet.

    Round joins, and half the spacing on each part rather than all of it on
    one. The packer's own rule is not symmetric: placement_clearance_zone grows
    one part by the full spacing with a mitred join, a mitre at a sharp corner
    runs well past the spacing, and inflating A against B is then a different
    test from inflating B against A. It calls two parts of eighteen too close
    on a layout whose real gap is 0.0433 in against a 0.04 in setting. Since a
    squeeze is only accepted at zero cost, anything built on that rule could
    never accept anything.

    The two versions before this one, for the record:

        light = _variant_light_geometry(placement.variant)
        self.zone = light.buffer(spacing * 0.6, join_style="mitre")

    too fat, since the light outline is already a superset inflated by 0.01 in.
    29 of the 153 pairs in an 18-part layout read as overlapping while sitting
    at their real, legal, just-packed positions.

        self.zone = self.shape.buffer(spacing - 1e-6, join_style="mitre")

    the packer's zone, which is where the asymmetry came in.
    """

    __slots__ = ("placement", "zone", "shape", "x", "y", "width", "height")

    def __init__(
        self, placement: Placement, spacing: float, fill_holes: bool = False
    ) -> None:
        self.placement = placement
        geom = placement.variant.geometry
        if fill_holes:
            # Hole-nesting off means cut-outs count as solid. Without this,
            # separation happily parks a small part in someone's lightening
            # hole, which is the thing that setting forbids.
            geom = _filled_geometry(geom)

        base = geom.bounds
        self.shape = shp_translate(geom, xoff=-base[0], yoff=-base[1])

        inflate = max(spacing / 2.0 - 1e-9, 0.0)
        if inflate > 0.0:
            self.zone = self.shape.buffer(inflate, quad_segs=4)
        else:
            self.zone = self.shape

        sb = self.shape.bounds
        self.width = sb[2] - sb[0]
        self.height = sb[3] - sb[1]
        self.x = placement.x
        self.y = placement.y

    def at(self, x: float, y: float) -> Any:
        return shp_translate(self.zone, xoff=x, yoff=y)

    def shape_at(self, x: float, y: float) -> Any:
        return shp_translate(self.shape, xoff=x, yoff=y)


def _pair_key(i: int, j: int) -> tuple[int, int]:
    return (i, j) if i < j else (j, i)


def _overlap_area(a: Any, b: Any) -> float:
    ab, bb = a.bounds, b.bounds
    if ab[2] <= bb[0] or bb[2] <= ab[0] or ab[3] <= bb[1] or bb[3] <= ab[1]:
        return 0.0
    try:
        return a.intersection(b).area
    except Exception:
        return 0.0


def _cost_for(
    index: int,
    geom: Any,
    shape: Any,
    geoms: list[Any],
    shapes: list[Any],
    weights: dict,
    allowed: Any,
) -> float:
    """Weighted overlap against every other part, plus any of the bare outline
    hanging outside the sheet."""
    total = 0.0
    for other in range(len(geoms)):
        if other == index:
            continue
        area = _overlap_area(geom, geoms[other])
        if area > 0.0:
            total += area * weights.get(_pair_key(index, other), 1.0)
    try:
        outside = shape.difference(allowed).area
    except Exception:
        outside = 0.0
    return total + 3.0 * outside  # leaving the sheet is not negotiable


def _refine(
    index: int,
    body: _Body,
    x: float,
    y: float,
    cost: float,
    geoms: list[Any],
    shapes: list[Any],
    weights: dict,
    allowed: Any,
    limits: tuple[float, float, float, float],
    fine: float,
) -> tuple[float, float, float]:
    """Walk a promising sample downhill along the axes.

    Sampling gets a part roughly into place and no further. The gaps worth
    closing are a spacing wide and a random draw lands in one about never.
    Stopping the descent at ``fine`` left layouts on ~0.001 sq in of overlap:
    87 percent separated, and still rejected, since only zero counts.
    """
    lo_x, lo_y, hi_x, hi_y = limits
    floor = max(fine / 64.0, _REFINE_FLOOR)
    step = fine * 8.0
    while step >= floor:
        moved = False
        for dx, dy in ((-step, 0.0), (step, 0.0), (0.0, -step), (0.0, step)):
            nx = min(max(x + dx, lo_x), hi_x)
            ny = min(max(y + dy, lo_y), hi_y)
            if nx == x and ny == y:
                continue
            trial = _cost_for(
                index, body.at(nx, ny), body.shape_at(nx, ny),
                geoms, shapes, weights, allowed,
            )
            if trial < cost - 1e-12:
                cost, x, y = trial, nx, ny
                moved = True
                break
        if not moved:
            step /= 2.0
    return cost, x, y


def _separate(
    bodies: list[_Body],
    allowed: Any,
    rng: random.Random,
    should_stop: Callable[[], bool],
    fine: float,
) -> bool:
    """Push the parts apart until nothing overlaps. False if it gives up."""
    if not bodies:
        return True
    ab = allowed.bounds
    geoms = [b.at(b.x, b.y) for b in bodies]
    shapes = [b.shape_at(b.x, b.y) for b in bodies]
    weights: dict = {}

    # Geometric ladder from a quarter of the sheet down to about one spacing.
    # A linear decay spends every sweep at sheet scale and never gets near the
    # size of the gaps it is trying to close.
    coarse = max(0.25 * max(ab[2] - ab[0], ab[3] - ab[1]), fine)
    decay = (fine / coarse) ** (1.0 / max(1, _MAX_SWEEPS - 1))
    best_total = float("inf")
    stale = 0

    for sweep in range(_MAX_SWEEPS):
        if should_stop():
            return False

        costs = [
            _cost_for(i, geoms[i], shapes[i], geoms, shapes, weights, allowed)
            for i in range(len(bodies))
        ]
        offenders = [i for i, c in enumerate(costs) if c > 1e-9]
        if not offenders:
            return True

        total = sum(costs)
        if total < best_total - 1e-12:
            best_total, stale = total, 0
        else:
            stale += 1
            if stale >= _PATIENCE:
                return False

        spread = coarse * (decay ** sweep)

        rng.shuffle(offenders)
        for i in offenders:
            body = bodies[i]
            lo_x, lo_y = ab[0], ab[1]
            hi_x = max(ab[0], ab[2] - body.width)
            hi_y = max(ab[1], ab[3] - body.height)
            limits = (lo_x, lo_y, hi_x, hi_y)

            best_x, best_y = body.x, body.y
            best = _cost_for(i, geoms[i], shapes[i], geoms, shapes, weights, allowed)

            samples = [
                (body.x + rng.gauss(0.0, spread), body.y + rng.gauss(0.0, spread))
                for _ in range(_NEAR_SAMPLES)
            ]
            samples += [
                (rng.uniform(lo_x, hi_x), rng.uniform(lo_y, hi_y))
                for _ in range(_FAR_SAMPLES)
            ]

            for sx, sy in samples:
                sx = min(max(sx, lo_x), hi_x)
                sy = min(max(sy, lo_y), hi_y)
                cost = _cost_for(
                    i, body.at(sx, sy), body.shape_at(sx, sy),
                    geoms, shapes, weights, allowed,
                )
                if cost < best - 1e-12:
                    best, best_x, best_y = cost, sx, sy

            best, best_x, best_y = _refine(
                i, body, best_x, best_y, best,
                geoms, shapes, weights, allowed, limits, fine,
            )

            if (best_x, best_y) != (body.x, body.y):
                body.x, body.y = best_x, best_y
                geoms[i] = body.at(best_x, best_y)
                shapes[i] = body.shape_at(best_x, best_y)

        # Anything still touching gets dearer, so the next sweep leans on it.
        for i in range(len(bodies)):
            for j in range(i + 1, len(bodies)):
                key = _pair_key(i, j)
                if _overlap_area(geoms[i], geoms[j]) > 0.0:
                    weights[key] = min(
                        _WEIGHT_CAP, weights.get(key, 1.0) * _WEIGHT_GROWTH
                    )
                elif key in weights:
                    weights[key] = max(1.0, weights[key] / _WEIGHT_GROWTH)

    return False


def _rebuild(
    bodies: Sequence[_Body], sheet_index: int, sheet: SheetSpec
) -> Optional[list[Placement]]:
    """Real placements at the separated positions, or None if any pair ended up
    closer than the spacing. Measured as a plain distance between outlines,
    which is what the setting means to whoever cuts the sheet."""
    placements: list[Placement] = []
    for body in bodies:
        p = body.placement
        vb = p.variant.geometry.bounds
        geom = shp_translate(
            p.variant.geometry, xoff=body.x - vb[0], yoff=body.y - vb[1]
        )
        placements.append(
            Placement(p.item, p.variant, sheet_index, body.x, body.y, geom)
        )

    limit = sheet.spacing - 1e-9
    fill_holes = not sheet.allow_nesting_in_holes
    solids = [
        _filled_geometry(p.geometry) if fill_holes else p.geometry
        for p in placements
    ]
    for i, p in enumerate(placements):
        pb = placement_bounds(p)
        for j in range(i + 1, len(placements)):
            qb = placement_bounds(placements[j])
            if bbox_farther_than_spacing(pb, qb, sheet.spacing):
                continue
            if solids[i].distance(solids[j]) < limit:
                return None
    return placements


def compress_layout(
    result: LayoutResult,
    sheet: SheetSpec,
    seed: int = 42,
    rounds: int = 10,
    should_stop: Optional[Callable[[], bool]] = None,
) -> LayoutResult:
    """Squeeze a finished layout, sheet by sheet, keeping what survives."""
    if not result.sheets:
        return result
    stop = should_stop or (lambda: False)
    long_axis_x = sheet.width >= sheet.height
    fill_holes = not sheet.allow_nesting_in_holes

    region_info = sheet_usable_region(sheet)
    base_region = (
        region_info[0]
        if region_info is not None
        else shp_box(
            sheet.margin,
            sheet.margin,
            sheet.width - sheet.margin,
            sheet.height - sheet.margin,
        )
    )

    for sheet_index, layout in enumerate(result.sheets):
        if stop() or len(layout.placements) < 2:
            continue
        rng = random.Random(seed + sheet_index * 7919)

        for _ in range(rounds):
            if stop():
                break
            current = score_layout(result.sheets, sheet, len(result.unplaced))

            bounds = [placement_bounds(p) for p in layout.placements]
            used_hi = max(b[2] if long_axis_x else b[3] for b in bounds)
            span = used_hi - sheet.margin
            if span <= 0:
                break
            limit = sheet.margin + span * (1.0 - _SHRINK_STEP)

            # Pin the short axis to what the layout already occupies. Left
            # free, the parts buy the length being asked for by spreading
            # sideways: the run shortens, the used rectangle grows, and the
            # score reads the rectangle first. Every squeeze got reverted.
            lo_short = min(b[1] if long_axis_x else b[0] for b in bounds)
            hi_short = max(b[3] if long_axis_x else b[2] for b in bounds)
            if long_axis_x:
                clip = shp_box(0.0, lo_short, limit, hi_short)
            else:
                clip = shp_box(lo_short, 0.0, hi_short, limit)
            allowed = base_region.intersection(clip)
            if allowed.is_empty:
                break

            bodies = [
                _Body(p, sheet.spacing, fill_holes) for p in layout.placements
            ]
            fine = max(sheet.spacing, sheet.grid_step, 0.01)
            if not _separate(bodies, allowed, rng, stop, fine):
                break

            rebuilt = _rebuild(bodies, sheet_index, sheet)
            if rebuilt is None:
                break
            if any(not geometry_fits_sheet(p.geometry, sheet) for p in rebuilt):
                break

            previous = layout.placements
            layout.placements = rebuilt
            if score_layout(result.sheets, sheet, len(result.unplaced)) < current:
                continue
            layout.placements = previous  # squeezed, but no tighter
            break

    result.score = score_layout(result.sheets, sheet, len(result.unplaced))
    return result

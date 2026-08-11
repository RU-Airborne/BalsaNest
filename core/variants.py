"""Grain rules -> allowed orientations -> concrete :class:`Variant` set."""

from __future__ import annotations

from typing import Any, Sequence

from shapely.affinity import rotate as shp_rotate
from shapely.affinity import scale as shp_scale
from shapely.affinity import translate as shp_translate

from .models import Item, LoadedPart, SheetSpec, Variant
from .svg_geometry import dedupe_angles, exact_rotated_bounds


def allowed_rotations(part: LoadedPart, sheet: SheetSpec) -> list[float]:
    req = part.request
    if req.rotations:
        return dedupe_angles(req.rotations)

    if req.grain == "free":
        return [0.0, 90.0, 180.0, 270.0]

    sheet_grain_angle = 0.0 if sheet.grain_axis == "x" else 90.0
    target = sheet_grain_angle
    if req.grain == "perpendicular":
        target += 90.0

    # grain_angle_deg is the direction of the desired material grain axis drawn
    # in the source part. Rotating by target-source aligns it.
    base_rotation = target - req.grain_angle_deg
    return dedupe_angles([base_rotation, base_rotation + 180.0])


def build_variants(part: LoadedPart, sheet: SheetSpec) -> list[Variant]:
    variants: list[Variant] = []
    seen_geoms: list[Any] = []

    mirror_options = (False, True) if sheet.allow_mirror else (False,)
    for mirror in mirror_options:
        for angle in allowed_rotations(part, sheet):
            base = (
                shp_scale(part.geometry, xfact=-1.0, yfact=1.0, origin=(0.0, 0.0))
                if mirror
                else part.geometry
            )
            rotated = shp_rotate(base, angle, origin=(0.0, 0.0), use_radians=False)

            # Normalize with exact vector-curve bounds, not sampled shapely bounds.
            min_x, min_y, max_x, max_y = exact_rotated_bounds(part, angle, mirror)
            normalized = shp_translate(rotated, xoff=-min_x, yoff=-min_y)

            width = max_x - min_x
            height = max_y - min_y

            # A reflection of a symmetric part can reproduce a plain rotation.
            # Bounding box and area cannot tell a shape from its mirror image, so
            # compare the actual normalized outline (Hausdorff distance) and skip
            # only genuine duplicates. Tolerance sits a bit above the sampling
            # granularity so re-parametrised copies of one shape still match.
            tol = max(0.01, 0.02 * min(width, height))
            is_duplicate = any(
                abs(g.bounds[2] - width) < tol
                and abs(g.bounds[3] - height) < tol
                and normalized.hausdorff_distance(g) <= tol
                for g in seen_geoms
            )
            if is_duplicate:
                continue
            seen_geoms.append(normalized)

            variants.append(
                Variant(
                    part=part,
                    angle_deg=angle,
                    geometry=normalized,
                    rotated_min_x=min_x,
                    rotated_min_y=min_y,
                    width=width,
                    height=height,
                    mirrored=mirror,
                )
            )
    return variants


def make_items(parts: Sequence[LoadedPart], sheet: SheetSpec) -> list[Item]:
    items: list[Item] = []
    for part in parts:
        variants = build_variants(part, sheet)
        for i in range(1, part.request.quantity + 1):
            items.append(
                Item(uid=f"{part.display_name}_{i:03d}", part=part, variants=variants)
            )
    return items

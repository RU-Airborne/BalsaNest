"""Upfront oversize checks: a job that cannot fit fails before packing."""

import math
from typing import Optional, Sequence

from .constants import EPS
from .errors import BalsaNestError
from .models import Item, LoadedPart, SheetSpec, Variant


def smallest_fitting_variant(item: Item, sheet: SheetSpec) -> Optional[Variant]:
    fitting = [
        v
        for v in item.variants
        if v.width <= sheet.usable_width + EPS and v.height <= sheet.usable_height + EPS
    ]
    if not fitting:
        return None
    return min(fitting, key=lambda v: v.width * v.height)


def preflight_capacity(
    parts: Sequence[LoadedPart], items: Sequence[Item], sheet: SheetSpec
) -> list[str]:
    """Raise if any part cannot fit one empty sheet in any allowed orientation.
    Return non-fatal warnings (over-capacity vs a sheet cap, or a silhouette
    that looks like an un-closed outline)."""
    warnings: list[str] = []

    # 1. Hard check: every distinct part must fit on one empty sheet somehow.
    per_part_items: dict[str, Item] = {}
    for item in items:
        per_part_items.setdefault(item.part.display_name, item)

    for name, item in per_part_items.items():
        if smallest_fitting_variant(item, sheet) is None:
            sizes = ", ".join(
                f"{v.width:.3f}×{v.height:.3f} in @ {v.angle_deg:g}°"
                f"{' mirrored' if v.mirrored else ''}"
                for v in item.variants
            )
            raise BalsaNestError(
                f"Part {name!r} does not fit on a "
                f"{sheet.width:g}×{sheet.height:g} in sheet "
                f"(usable {sheet.usable_width:.3f}×{sheet.usable_height:.3f} in "
                f"after {sheet.margin:g} in margins).\n"
                f"  Tried orientations: {sizes}\n"
                f"  Use a larger sheet, reduce the margin, or check the export scale."
            )

    # 2. Soft check: rough area-based capacity vs an optional sheet cap.
    if sheet.boundary is not None:
        usable_area = max(float(sheet.boundary.area), EPS)
    else:
        usable_area = max(sheet.usable_width * sheet.usable_height, EPS)
    total_min_bbox_area = 0.0
    for item in items:
        v = smallest_fitting_variant(item, sheet)
        if v is not None:
            total_min_bbox_area += v.width * v.height

    lower_bound_sheets = max(math.ceil(total_min_bbox_area / usable_area - 1e-9), 1)
    if sheet.max_sheets is not None and lower_bound_sheets > sheet.max_sheets:
        warnings.append(
            f"This job needs at least ~{lower_bound_sheets} sheet(s) by bounding-box "
            f"area alone, but max_sheets is {sheet.max_sheets}. Some parts will not "
            f"be placed. Increase max_sheets, enlarge the sheet, or cut the quantity."
        )

    # 3. Silhouette sanity: a tiny collision area usually means an un-closed
    # outline, which would let parts overlap.
    for part in parts:
        bbox_area = max(part.base_width_in * part.base_height_in, EPS)
        try:
            geom_area = float(part.geometry.area)
        except Exception:
            geom_area = 0.0
        if geom_area < 0.02 * bbox_area:
            warnings.append(
                f"Part {part.display_name!r} has almost no enclosed area "
                f"({geom_area:.4f} in² vs {bbox_area:.3f} in² bounding box). Its outline "
                f"may be open (not a closed path), so overlap checks and labels will be "
                f"unreliable. Re-export it as a closed profile if possible."
            )

    return warnings

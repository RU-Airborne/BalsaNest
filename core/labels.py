"""Raster part-name labels, guaranteed to sit inside the actual material.

For each part we find its pole of inaccessibility and anchor the text there.
The font size is then grown by binary search only as far as the whole text block
still passes an actual ``polygon.contains(block)`` test.

The size therefore adapts to the part, not just the local material width, and
long names are wrapped onto multiple lines when that yields a larger, legible
font. Parts that cannot hold even a minimum size label are skipped and reported
as warnings.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry import box as shp_box

from .constants import LABEL_CHAR_WIDTH_RATIO
from .models import OutputOptions, SheetLayout

LABEL_LINE_SPACING = 1.2  # line height as a multiple of font size


@dataclass
class LabelSpec:
    """A planned label. ``text`` is the full name (for compatibility); ``lines``
    holds the wrapped lines actually rendered."""

    text: str
    center_x_in: float
    center_y_in: float
    font_in: float
    lines: list[str] = field(default_factory=list)
    line_spacing: float = LABEL_LINE_SPACING

    def __post_init__(self) -> None:
        if not self.lines:
            self.lines = [self.text]


def _largest_polygon(geom: Any) -> Optional[Any]:
    """The biggest solid component to place text on (holes are respected because
    a Polygon carries its interiors)."""
    if isinstance(geom, Polygon):
        return geom if not geom.is_empty else None
    if isinstance(geom, MultiPolygon):
        polys = [g for g in geom.geoms if not g.is_empty]
        return max(polys, key=lambda g: g.area) if polys else None
    polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]
    return max(polys, key=lambda g: g.area) if polys else None


def _pole_of_inaccessibility(poly: Any, iterations: int = 20) -> tuple[float, float, float]:
    """Return (cx, cy, clearance) for the interior point farthest from the
    boundary. Found by binary-searching the largest inward buffer that stays
    non-empty -- robust for frames, tapered airfoils and holed ribs alike."""
    min_x, min_y, max_x, max_y = poly.bounds
    lo, hi = 0.0, 0.5 * min(max_x - min_x, max_y - min_y)
    center = poly.representative_point()
    best_r = 0.0
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        eroded = poly.buffer(-mid)
        if not eroded.is_empty:
            best_r = mid
            try:
                center = eroded.representative_point()
            except Exception:
                pass
            lo = mid
        else:
            hi = mid
    return float(center.x), float(center.y), best_r


def _wrap(text: str, n_lines: int) -> list[str]:
    """Split ``text`` into at most ``n_lines`` lines, balancing length. Splits on
    separators first; hard-splits a single long token when needed."""
    n_lines = max(1, n_lines)
    if n_lines == 1:
        return [text]

    words = [w for w in re.split(r"[\s_\-]+", text) if w]
    if len(words) >= n_lines:
        target = len(text) / n_lines
        lines: list[str] = []
        cur = ""
        for w in words:
            candidate = w if not cur else f"{cur} {w}"
            if cur and len(candidate) > target and len(lines) < n_lines - 1:
                lines.append(cur)
                cur = w
            else:
                cur = candidate
        if cur:
            lines.append(cur)
        return lines

    size = math.ceil(len(text) / n_lines)
    chunks = [text[i : i + size] for i in range(0, len(text), size)]
    return [c for c in chunks if c] or [text]


def _block(cx: float, cy: float, w: float, h: float) -> Any:
    return shp_box(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


class LabelPlanner:
    """Plans one label per placement for a sheet layout."""

    def __init__(self, options: OutputOptions) -> None:
        self.options = options

    def plan(self, layout: SheetLayout) -> tuple[list[LabelSpec], list[str]]:
        """Return (specs, warnings). ``warnings`` names parts that could not hold
        a legible label and were left blank."""
        pairs, warnings = self.plan_pairs(layout)
        return [spec for _, spec in pairs], warnings

    def plan_pairs(self, layout: SheetLayout) -> tuple[list[tuple[Any, LabelSpec]], list[str]]:
        """Like :meth:`plan`, but keeps each spec linked to its placement so the
        writer can group a part's label with its cut paths."""
        planned: list[tuple[Any, LabelSpec]] = []
        warnings: list[str] = []

        for placement in layout.placements:
            spec = self._plan_one(placement)
            if spec is None:
                warnings.append(placement.item.part.display_name)
            else:
                planned.append((placement, spec))

        if self.options.label_align_bands and planned:
            self._align_bands(planned)

        return planned, warnings

    def _plan_one(self, placement: Any) -> Optional[LabelSpec]:
        opts = self.options
        poly = _largest_polygon(placement.geometry)
        if poly is None:
            return None
        label = placement.item.part.display_name
        if not label:
            return None

        cx, cy, _clear = _pole_of_inaccessibility(poly)
        max_lines = max(1, opts.label_max_lines)

        words = [w for w in re.split(r"[\s_\-]+", label) if w]
        if len(words) <= 1:
            # Single word: keep it on one line if it fits at all -- hard-splitting
            # a word ("bul/khe/ad") reads badly. Only stack it when one line is
            # impossible, and then use the fewest lines that fit.
            chosen = self._single_word(poly, cx, cy, label, max_lines)
        else:
            chosen = self._multi_word(poly, cx, cy, label, max_lines)
        if chosen is None:
            return None
        font, lines = chosen
        return LabelSpec(
            text=label,
            center_x_in=cx,
            center_y_in=cy,
            font_in=font,
            lines=lines,
        )

    def _single_word(
        self, poly: Any, cx: float, cy: float, label: str, max_lines: int
    ) -> Optional[tuple[float, list[str]]]:
        one = self._max_font(poly, cx, cy, [label])
        if one is not None:
            return one, [label]
        for n in range(2, max_lines + 1):
            lines = _wrap(label, n)
            font = self._max_font(poly, cx, cy, lines)
            if font is not None:
                return font, lines
        return None

    def _multi_word(
        self, poly: Any, cx: float, cy: float, label: str, max_lines: int
    ) -> Optional[tuple[float, list[str]]]:
        candidates: list[tuple[int, float, list[str]]] = []
        for n in range(1, max_lines + 1):
            lines = _wrap(label, n)
            font = self._max_font(poly, cx, cy, lines)
            if font is not None:
                candidates.append((n, font, lines))
        if not candidates:
            return None
        # Prefer the fewest lines that stays within 20% of the largest achievable
        # font, so wide parts keep a clean single line and only cramped parts
        # stack onto extra (word-boundary) lines.
        best_font = max(f for _, f, _ in candidates)
        for _, font, lines in sorted(candidates, key=lambda c: c[0]):
            if font >= 0.8 * best_font:
                return font, lines
        return None

    def _block_dims(self, lines: list[str], font: float) -> tuple[float, float]:
        max_chars = max((len(line) for line in lines), default=1)
        w = max_chars * LABEL_CHAR_WIDTH_RATIO * font
        h = len(lines) * LABEL_LINE_SPACING * font
        return w, h

    def _max_font(self, poly: Any, cx: float, cy: float, lines: list[str]) -> Optional[float]:
        opts = self.options

        def fits(font: float) -> bool:
            w, h = self._block_dims(lines, font)
            return poly.contains(_block(cx, cy, w, h))

        if not fits(opts.label_min_font_in):
            return None
        if fits(opts.label_max_font_in):
            return opts.label_max_font_in

        lo, hi = opts.label_min_font_in, opts.label_max_font_in
        for _ in range(18):
            mid = 0.5 * (lo + hi)
            if fits(mid):
                lo = mid
            else:
                hi = mid
        return lo

    # Never band labels whose blocks are farther apart than this horizontally:
    # a raster pass sweeps the band's full x-span, so banding two labels on
    # opposite ends of the sheet would drag the head across the whole board on
    # every raster line -- slower than two separate short bands.
    _BAND_MAX_GAP_IN = 3.0

    def _feasible_y_interval(
        self, poly: Any, cx: float, cy: float, w: float, h: float
    ) -> tuple[float, float]:
        """How far the label centre can slide vertically (at fixed x) while the
        block stays inside the part. Labels are free to move anywhere in this
        interval -- identical parts may end up with labels at different heights
        if that lets each join a nearby neighbour's raster band."""

        def fits(y: float) -> bool:
            return poly.contains(_block(cx, y, w, h))

        if not fits(cy):
            return cy, cy
        span = poly.bounds[3] - poly.bounds[1]

        def slack(direction: float) -> float:
            ok, hi = 0.0, span
            for _ in range(14):
                mid = 0.5 * (ok + hi)
                if fits(cy + direction * mid):
                    ok = mid
                else:
                    hi = mid
            return ok

        return cy - slack(-1.0), cy + slack(+1.0)

    def _align_bands(self, planned: list[tuple[Any, LabelSpec]]) -> None:
        """Group labels onto shared horizontal raster bands to minimise engrave
        time. Lasers raster horizontally and move slowly in y, so fewer bands is
        faster -- but only when the banded labels are horizontally close, since
        each raster line sweeps the band's whole x-span.

        Each label may slide anywhere in its feasible vertical range (staying
        inside its part) to reach a band, and two labels only share a band when
        their ranges overlap AND their blocks are within ``_BAND_MAX_GAP_IN``
        horizontally. Band members are then pulled toward each other in x to
        shorten the sweep further."""
        entries = []
        for placement, spec in planned:
            poly = _largest_polygon(placement.geometry)
            if poly is None:
                entries.append(None)
                continue
            w, h = self._block_dims(spec.lines, spec.font_in)
            y_lo, y_hi = self._feasible_y_interval(
                poly, spec.center_x_in, spec.center_y_in, w, h
            )
            entries.append(
                {
                    "poly": poly,
                    "spec": spec,
                    "w": w,
                    "h": h,
                    "x0": spec.center_x_in - w / 2.0,
                    "x1": spec.center_x_in + w / 2.0,
                    "y_lo": y_lo,
                    "y_hi": y_hi,
                }
            )

        valid = [i for i, e in enumerate(entries) if e is not None]
        order = sorted(valid, key=lambda i: entries[i]["spec"].center_y_in)
        bands: list[dict[str, Any]] = []
        for i in order:
            e = entries[i]
            joined = False
            for band in bands:
                y_lo = max(band["y_lo"], e["y_lo"])
                y_hi = min(band["y_hi"], e["y_hi"])
                if y_lo > y_hi:
                    continue  # cannot reach a common height
                if e["x0"] > band["x1"]:
                    gap = e["x0"] - band["x1"]
                elif band["x0"] > e["x1"]:
                    gap = band["x0"] - e["x1"]
                else:
                    gap = 0.0
                if gap > self._BAND_MAX_GAP_IN:
                    continue  # opposite ends of the board: keep bands separate
                band["y_lo"], band["y_hi"] = y_lo, y_hi
                band["x0"] = min(band["x0"], e["x0"])
                band["x1"] = max(band["x1"], e["x1"])
                band["members"].append(i)
                joined = True
                break
            if not joined:
                bands.append(
                    {
                        "y_lo": e["y_lo"],
                        "y_hi": e["y_hi"],
                        "x0": e["x0"],
                        "x1": e["x1"],
                        "members": [i],
                    }
                )

        for band in bands:
            members = band["members"]
            mean_cy = sum(entries[i]["spec"].center_y_in for i in members) / len(members)
            band_y = min(max(mean_cy, band["y_lo"]), band["y_hi"])
            for i in members:
                e = entries[i]
                if e["poly"].contains(
                    _block(e["spec"].center_x_in, band_y, e["w"], e["h"])
                ):
                    e["spec"].center_y_in = band_y

            # Pull labels on a shared band toward each other horizontally (as
            # far as each stays inside its part) to shorten the raster sweep.
            if len(members) > 1:
                centroid_x = sum(entries[i]["spec"].center_x_in for i in members) / len(members)
                for i in members:
                    e = entries[i]
                    spec = e["spec"]
                    shift = centroid_x - spec.center_x_in
                    for frac in (1.0, 0.5, 0.25):
                        cx = spec.center_x_in + shift * frac
                        if e["poly"].contains(
                            _block(cx, spec.center_y_in, e["w"], e["h"])
                        ):
                            spec.center_x_in = cx
                            break


def build_label_specs(layout: SheetLayout, options: OutputOptions) -> tuple[list[LabelSpec], int]:
    """Backward-compatible helper: returns (specs, skipped_count)."""
    specs, warnings = LabelPlanner(options).plan(layout)
    return specs, len(warnings)

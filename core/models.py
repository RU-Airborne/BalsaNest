"""Plain dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .errors import BalsaNestError


@dataclass(frozen=True)
class SheetSpec:
    width: float
    height: float
    grain_axis: str = "x"
    margin: float = 0.05
    spacing: float = 0.04
    grid_step: float = 0.04
    passes: int = 8
    max_sheets: Optional[int] = None
    allow_mirror: bool = True
    compact: bool = True
    allow_nesting_in_holes: bool = True
    min_hole_area: float = 0.02  # in^2; ignore cut-outs smaller than this
    boundary: Optional[Any] = None

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise BalsaNestError("Sheet width and height must be > 0.")
        if self.grain_axis not in {"x", "y"}:
            raise BalsaNestError("sheet.grain_axis must be 'x' or 'y'.")
        if self.margin < 0 or self.spacing < 0:
            raise BalsaNestError("Sheet margin and spacing must be >= 0.")
        if self.grid_step <= 0:
            raise BalsaNestError("sheet.grid_step must be > 0.")
        if self.passes < 1:
            raise BalsaNestError("sheet.passes must be >= 1.")
        if self.max_sheets is not None and self.max_sheets < 1:
            raise BalsaNestError("sheet.max_sheets must be >= 1 when set.")
        if self.width <= 2 * self.margin or self.height <= 2 * self.margin:
            raise BalsaNestError("Margins consume the whole sheet.")
        if self.boundary is not None:
            if self.boundary.is_empty or not self.boundary.is_valid:
                raise BalsaNestError("sheet.boundary must be a valid, non-empty polygon.")
            bx0, by0, bx1, by1 = self.boundary.bounds
            tol = 1e-3
            if (
                abs(bx0) > tol or abs(by0) > tol
                or abs(bx1 - self.width) > tol or abs(by1 - self.height) > tol
            ):
                raise BalsaNestError(
                    "sheet.boundary must be normalized so its bounding box is "
                    "(0, 0, width, height)."
                )

    @property
    def usable_width(self) -> float:
        return self.width - 2 * self.margin

    @property
    def usable_height(self) -> float:
        return self.height - 2 * self.margin


@dataclass(frozen=True)
class OutputOptions:
    """Cosmetic / production-marking output controls (not nesting geometry).

    Defaults follow the common laser-shop convention (editable per machine):
      red hairline  = cut
      black fill    = raster engrave (the default label mode)
      blue hairline = outline engrave (optional label mode, faster than raster)
    """

    label_parts: bool = True
    label_min_font_in: float = 0.06
    label_max_font_in: float = 0.5
    label_align_bands: bool = True
    label_max_lines: int = 4
    label_color: str = "#000000"
    # "raster": black filled text (engraved by rastering).
    # "outline": hairline-stroked text (vector outline engrave -- much faster).
    label_mode: str = "raster"
    label_outline_color: str = "#0000ff"
    cut_color: str = "#ff0000"
    # "hairline" emits Inkscape hairline strokes (~0.001 in) so print-driver
    # laser workflows register the red lines as cuts. A float sets an explicit
    # stroke width in px instead (some laser software ignores width entirely).
    cut_stroke: Any = "hairline"
    # Group each part's label with its cut paths so selecting/moving the part
    # in Inkscape brings its label along.
    group_labels_with_parts: bool = True
    # Emit each coincident cut segment once (Deepnest-style common-line
    # cutting): when two placed parts share an edge (only possible with
    # spacing = 0), the shared line is cut a single time. Only exactly
    # overlapping segments merge; partial overlaps are left as-is.
    merge_common_cuts: bool = False
    # Inch rulers along the top/left edges (reference layer, never cut). Used
    # by the web UI's preview so layouts can be checked against real stock.
    draw_rulers: bool = False
    debug_borders: bool = False
    debug_color: str = "#1e90ff"        # part bounding boxes + sheet outline
    debug_margin_color: str = "#ff8c00"  # unusable edge-margin band
    debug_hole_color: str = "#2e8b57"    # scrap cut-outs usable for nesting
    debug_cavity_color: str = "#8a2be2"  # concave pockets usable for packing


@dataclass(frozen=True)
class PartRequest:
    file: Path
    quantity: int
    grain: str = "free"
    grain_angle_deg: float = 0.0
    rotations: Optional[tuple[float, ...]] = None
    name: Optional[str] = None
    units: Optional[str] = None  # DXF only: "in"/"mm"/"cm" override for unitless files

    def validate(self) -> None:
        if self.quantity < 1:
            raise BalsaNestError(f"{self.file}: quantity must be >= 1.")
        if self.grain not in {"parallel", "perpendicular", "free"}:
            raise BalsaNestError(
                f"{self.file}: grain must be parallel, perpendicular, or free."
            )
        if not self.file.exists():
            raise BalsaNestError(f"Part file does not exist: {self.file}")
        suffix = self.file.suffix.lower()
        if suffix == ".dwg":
            raise BalsaNestError(
                f"{self.file}: DWG is a proprietary binary format that cannot be "
                f"read directly. In SolidWorks, export the drawing as DXF or SVG "
                f"or PDF instead (Save As > DXF)."
            )
        if suffix not in {".svg", ".dxf", ".pdf"}:
            raise BalsaNestError(
                f"{self.file}: unsupported format {suffix!r}; use SVG, DXF, or PDF."
            )
        if self.units is not None and self.units not in {"in", "mm", "cm"}:
            raise BalsaNestError(f"{self.file}: units must be 'in', 'mm', or 'cm'.")


@dataclass
class LoadedPart:
    request: PartRequest
    display_name: str
    paths: list[Any]
    geometry: Any
    viewbox_min_x: float
    viewbox_min_y: float
    source_units_to_inch: float
    base_min_x_in: float
    base_min_y_in: float
    base_width_in: float
    base_height_in: float
    notes: list[str] = field(default_factory=list)


@dataclass
class Variant:
    part: LoadedPart
    angle_deg: float
    geometry: Any
    rotated_min_x: float
    rotated_min_y: float
    width: float
    height: float
    mirrored: bool = False


@dataclass
class Item:
    uid: str
    part: LoadedPart
    variants: list[Variant]


@dataclass
class Placement:
    item: Item
    variant: Variant
    sheet_index: int
    x: float
    y: float
    geometry: Any

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.geometry.bounds


@dataclass
class SheetLayout:
    placements: list[Placement] = field(default_factory=list)


@dataclass
class LayoutResult:
    sheets: list[SheetLayout]
    score: tuple[Any, ...]
    unplaced: list[Item] = field(default_factory=list)


@dataclass
class JobSpec:
    sheet: SheetSpec
    requests: list[PartRequest]
    sample_step: float
    seed: int
    output: Path
    options: OutputOptions = field(default_factory=OutputOptions)
    allow_partial: bool = False


def placement_bounds(placement: Placement) -> tuple[float, float, float, float]:
    """Bounds of a committed placement, computed once and cached. Shapely
    recomputes ``.bounds`` on every access, which dominated runtime when a
    filling sheet was queried thousands of times."""
    cached = getattr(placement, "_bounds_cache", None)
    if cached is None:
        cached = placement.geometry.bounds
        placement._bounds_cache = cached
    return cached

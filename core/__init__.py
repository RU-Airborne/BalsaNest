"""BalsaNest - grain-aware SVG nesting for laser cutting.

Modular layout so each concern can be maintained (or replaced) independently:

    errors / constants      shared basics
    models                  dataclasses (SheetSpec, Variant, Placement, ...)
    svg_geometry            SVG<->shapely math, mirroring, exact transforms
    importer                SvgPartImporter -> LoadedPart
    variants                grain rules -> orientation set
    holes                   scrap cut-out detection + nesting
    nfp                     no-fit-polygon exact-contact candidate positions
    packing                 Nester: collision, compaction, greedy multi-pass
    compress                shrink-and-repair pass over a finished layout
    ga                      genetic algorithm over the insertion order
    parallel                worker pool that packs a generation side by side
    capacity                up-front oversize / capacity checks
    labels                  LabelPlanner: inside-the-material raster labels
    output                  SvgSheetWriter + JSON summary
    config                  JSON job -> JobSpec
    cli                     wizard, argument parsing, run_job, main
"""

from .capacity import preflight_capacity, smallest_fitting_variant
from .compress import compress_layout
from .cli import build_arg_parser, interactive_specs, main, print_job, run_job
from .config import (
    DEFAULTS_FILENAME,
    config_to_specs,
    deep_merge,
    load_config,
    load_defaults,
    output_options_from_config,
)
from .constants import DEFAULT_WELD_IN, EPS, PX_PER_INCH
from .errors import BalsaNestError
from .holes import (
    detect_nestings,
    hole_candidate_seeds,
    iter_hole_polygons,
    placement_scrap_holes,
)
from .importer import (
    DxfPartImporter,
    PdfPartImporter,
    SvgPartImporter,
    load_part,
    load_sheet_boundary,
    repair_doubled_contours,
)
from .labels import LabelPlanner, LabelSpec, build_label_specs
from .models import (
    Item,
    JobSpec,
    LayoutResult,
    LoadedPart,
    OutputOptions,
    PartRequest,
    Placement,
    SheetLayout,
    SheetSpec,
    Variant,
    placement_bounds,
)
from .nfp import nfp_candidate_seeds, nfp_for_pair
from .output import (
    SvgSheetWriter,
    layout_summary,
    save_outputs,
    save_scrap_outlines,
    write_sheet_svg,
)
from .ga import default_population, ga_generations, optimize_layout_ga
from .parallel import OrderPool, default_workers
from .packing import (
    finish_layout,
    Nester,
    candidate_coordinates,
    compact_toward_origin,
    find_placement,
    geometry_fits_sheet,
    heuristic_passes,
    is_collision_free,
    optimize_layout,
    pack_in_order,
    pack_once,
    placement_clearance_zone,
    polish_layout,
    score_layout,
)
from .svg_geometry import (
    build_collision_geometry,
    duplicated_line_stats,
    geometry_to_paths,
    mirror_path_x,
    parse_svg_length_inches,
    sampled_polylines,
    source_scale_from_svg,
    transform_path_for_placement,
    weld_polylines_to_geometry,
)
from .variants import allowed_rotations, build_variants, make_items

__all__ = [
    "BalsaNestError",
    "EPS",
    "PX_PER_INCH",
    "DEFAULT_WELD_IN",
    # models
    "SheetSpec",
    "OutputOptions",
    "PartRequest",
    "LoadedPart",
    "Variant",
    "Item",
    "Placement",
    "SheetLayout",
    "LayoutResult",
    "JobSpec",
    "placement_bounds",
    # geometry / import
    "parse_svg_length_inches",
    "source_scale_from_svg",
    "mirror_path_x",
    "build_collision_geometry",
    "sampled_polylines",
    "duplicated_line_stats",
    "weld_polylines_to_geometry",
    "geometry_to_paths",
    "transform_path_for_placement",
    "SvgPartImporter",
    "DxfPartImporter",
    "PdfPartImporter",
    "load_part",
    "load_sheet_boundary",
    "repair_doubled_contours",
    # variants
    "allowed_rotations",
    "build_variants",
    "make_items",
    # holes
    "iter_hole_polygons",
    "placement_scrap_holes",
    "hole_candidate_seeds",
    "detect_nestings",
    # nfp
    "nfp_for_pair",
    "nfp_candidate_seeds",
    # packing
    "Nester",
    "geometry_fits_sheet",
    "is_collision_free",
    "placement_clearance_zone",
    "candidate_coordinates",
    "compact_toward_origin",
    "find_placement",
    "pack_in_order",
    "pack_once",
    "score_layout",
    "polish_layout",
    "compress_layout",
    "finish_layout",
    "optimize_layout",
    "optimize_layout_ga",
    "ga_generations",
    "default_population",
    "default_workers",
    "OrderPool",
    "heuristic_passes",
    # capacity
    "smallest_fitting_variant",
    "preflight_capacity",
    # labels
    "LabelPlanner",
    "LabelSpec",
    "build_label_specs",
    # output
    "SvgSheetWriter",
    "write_sheet_svg",
    "save_outputs",
    "save_scrap_outlines",
    "layout_summary",
    # config / cli
    "DEFAULTS_FILENAME",
    "config_to_specs",
    "deep_merge",
    "load_config",
    "load_defaults",
    "output_options_from_config",
    "interactive_specs",
    "run_job",
    "print_job",
    "build_arg_parser",
    "main",
]

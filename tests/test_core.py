import sys
from pathlib import Path

import pytest
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core as bn  # noqa: E402


def test_unit_conversion():
    assert bn.parse_svg_length_inches("1in") == pytest.approx(1.0)
    assert bn.parse_svg_length_inches("25.4mm") == pytest.approx(1.0)
    assert bn.parse_svg_length_inches("96px") == pytest.approx(1.0)


def test_grain_rotation_parallel_x():
    req = bn.PartRequest(Path("x.svg"), 1, grain="parallel", grain_angle_deg=0)
    part = bn.LoadedPart(
        request=req,
        display_name="x",
        paths=[],
        geometry=box(0, 0, 2, 1),
        viewbox_min_x=0,
        viewbox_min_y=0,
        source_units_to_inch=1,
        base_min_x_in=0,
        base_min_y_in=0,
        base_width_in=2,
        base_height_in=1,
    )
    sheet = bn.SheetSpec(10, 3, grain_axis="x")
    assert bn.allowed_rotations(part, sheet) == [0.0, 180.0]


def test_grain_rotation_perpendicular_x():
    req = bn.PartRequest(Path("x.svg"), 1, grain="perpendicular", grain_angle_deg=0)
    part = bn.LoadedPart(
        request=req,
        display_name="x",
        paths=[],
        geometry=box(0, 0, 2, 1),
        viewbox_min_x=0,
        viewbox_min_y=0,
        source_units_to_inch=1,
        base_min_x_in=0,
        base_min_y_in=0,
        base_width_in=2,
        base_height_in=1,
    )
    sheet = bn.SheetSpec(10, 3, grain_axis="x")
    assert bn.allowed_rotations(part, sheet) == [90.0, 270.0]


def test_spacing_collision():
    a = box(0, 0, 1, 1)
    item = object()
    p = type("P", (), {"geometry": a})()
    candidate_ok = box(1.2, 0, 2.2, 1)
    candidate_bad = box(1.05, 0, 2.05, 1)
    assert bn.is_collision_free(candidate_ok, [p], 0.1)
    assert not bn.is_collision_free(candidate_bad, [p], 0.1)


def test_packer_respects_sheet_and_spacing():
    sheet = bn.SheetSpec(
        width=5.0,
        height=2.0,
        grain_axis="x",
        margin=0.1,
        spacing=0.1,
        grid_step=0.1,
        passes=1,
    )
    req = bn.PartRequest(Path("dummy.svg"), 1, grain="free")
    part = bn.LoadedPart(
        request=req,
        display_name="dummy",
        paths=[],
        geometry=box(0, 0, 1.0, 0.7),
        viewbox_min_x=0,
        viewbox_min_y=0,
        source_units_to_inch=1,
        base_min_x_in=0,
        base_min_y_in=0,
        base_width_in=1.0,
        base_height_in=0.7,
    )
    variant = bn.Variant(
        part=part,
        angle_deg=0,
        geometry=box(0, 0, 1.0, 0.7),
        rotated_min_x=0,
        rotated_min_y=0,
        width=1.0,
        height=0.7,
    )
    items = [
        bn.Item(uid=f"dummy_{i}", part=part, variants=[variant])
        for i in range(6)
    ]

    result = bn.pack_once(items, sheet, mode=0, seed=42)
    assert len(result.sheets) >= 1
    assert not result.unplaced

    for layout in result.sheets:
        for placement in layout.placements:
            assert bn.geometry_fits_sheet(placement.geometry, sheet)
        for i, a in enumerate(layout.placements):
            for b in layout.placements[i + 1:]:
                assert a.geometry.distance(b.geometry) >= sheet.spacing - 1e-7


# --- Helpers for the new-feature tests ---------------------------------------

def _box_part(name, w, h, geom=None):
    req = bn.PartRequest(Path(f"{name}.svg"), 1, grain="free")
    return bn.LoadedPart(
        request=req,
        display_name=name,
        paths=[],
        geometry=geom if geom is not None else box(0, 0, w, h),
        viewbox_min_x=0,
        viewbox_min_y=0,
        source_units_to_inch=1,
        base_min_x_in=0,
        base_min_y_in=0,
        base_width_in=w,
        base_height_in=h,
    )


def _box_item(name, w, h, qty=1):
    part = _box_part(name, w, h)
    variant = bn.Variant(
        part=part, angle_deg=0, geometry=box(0, 0, w, h),
        rotated_min_x=0, rotated_min_y=0, width=w, height=h,
    )
    return [bn.Item(uid=f"{name}_{i}", part=part, variants=[variant]) for i in range(qty)]


# --- Mirror -------------------------------------------------------------------

def test_mirror_path_x_reflects_line_and_arc():
    from svgpathtools import Arc, Line, Path as SvgPath

    p = SvgPath(Line(1 + 2j, 3 + 4j))
    m = bn.mirror_path_x(p)
    assert m[0].start == pytest.approx(-1 + 2j)
    assert m[0].end == pytest.approx(-3 + 4j)

    arc = SvgPath(Arc(0 + 0j, 2 + 1j, 30.0, True, True, 4 + 0j))
    ma = bn.mirror_path_x(arc)[0]
    assert ma.start == pytest.approx(0 + 0j)
    assert ma.end == pytest.approx(-4 + 0j)
    assert ma.rotation == pytest.approx(-30.0)
    assert ma.sweep is False  # reflection reverses orientation


def test_build_variants_mirror_adds_orientations_for_asymmetric_part():
    # The airfoil is cambered (asymmetric): its mirror image is a genuinely new
    # orientation, so allow_mirror should add mirrored variants.
    airfoil = ROOT / "examples" / "parts" / "airfoil.svg"
    req = bn.PartRequest(airfoil, 1, grain="free")
    part = bn.load_part(req, sample_step_in=0.02)

    with_mirror = bn.build_variants(part, bn.SheetSpec(36, 3, allow_mirror=True))
    without = bn.build_variants(part, bn.SheetSpec(36, 3, allow_mirror=False))
    assert any(v.mirrored for v in with_mirror)
    assert not any(v.mirrored for v in without)
    assert len(with_mirror) > len(without)


def test_build_variants_mirror_dedupes_symmetric_part():
    # The rib is symmetric, so mirrored copies duplicate rotations and must be
    # dropped (no wasted packing effort).
    rib = ROOT / "examples" / "parts" / "rib.svg"
    req = bn.PartRequest(rib, 1, grain="parallel")
    part = bn.load_part(req, sample_step_in=0.02)
    variants = bn.build_variants(part, bn.SheetSpec(36, 3, allow_mirror=True))
    assert not any(v.mirrored for v in variants)


# --- Capacity / oversize ------------------------------------------------------

def test_preflight_rejects_oversize_part():
    sheet = bn.SheetSpec(4.0, 3.0, grain_axis="x", margin=0.05)
    items = _box_item("huge", 6.0, 1.0, qty=1)
    with pytest.raises(bn.BalsaNestError):
        bn.preflight_capacity([items[0].part], items, sheet)


def test_preflight_warns_when_over_max_sheets():
    sheet = bn.SheetSpec(12.0, 3.0, grain_axis="x", margin=0.05, max_sheets=1)
    items = _box_item("tile", 5.0, 2.5, qty=6)
    warnings = bn.preflight_capacity([items[0].part], items, sheet)
    assert any("max_sheets" in w for w in warnings)


def test_segment_soup_outline_is_stitched_solid(tmp_path):
    # CAD exports often draw an outline as disconnected segments separated by
    # "m 0,0" movetos. That must still register as solid material, not hairlines.
    svg = tmp_path / "soup.svg"
    svg.write_text(
        '<?xml version="1.0"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="2in" height="2in" viewBox="0 0 192 192">\n'
        '  <path d="M 0,0 L 192,0 M 192,0 L 192,192 M 192,192 L 0,192 M 0,192 L 0,0'
        '           M 48,48 L 144,48 M 144,48 L 144,144 M 144,144 L 48,144 M 48,144 L 48,48"/>\n'
        "</svg>\n"
    )
    part = bn.load_part(bn.PartRequest(svg, 1, grain="free"), 0.02)
    # 2x2 square minus 1x1 hole = 3 in^2 of real material.
    assert part.geometry.area == pytest.approx(3.0, abs=0.05)
    holes = list(bn.iter_hole_polygons(part.geometry))
    assert len(holes) == 1
    assert holes[0].area == pytest.approx(1.0, abs=0.05)


def test_hole_placement_beats_open_sheet_space():
    from core.holes import detect_nestings
    from shapely.geometry import Polygon

    # Frame 3x3 with a 2x2 scrap window, on a sheet with plenty of open space
    # nearer the origin-corner objective than the window is.
    outer = box(0, 0, 3.0, 3.0)
    frame = Polygon(outer.exterior.coords, [box(0.5, 0.5, 2.5, 2.5).exterior.coords])
    frame_part = _box_part("frame", 3.0, 3.0, geom=frame)
    frame_variant = bn.Variant(
        part=frame_part, angle_deg=0, geometry=frame,
        rotated_min_x=0, rotated_min_y=0, width=3.0, height=3.0,
    )
    frame_item = bn.Item(uid="frame_1", part=frame_part, variants=[frame_variant])
    tiles = _box_item("tile", 0.6, 0.6, qty=2)

    sheet = bn.SheetSpec(10.0, 6.0, margin=0.05, spacing=0.05, passes=1,
                         allow_nesting_in_holes=True, allow_mirror=False)
    result = bn.pack_once([frame_item] + tiles, sheet, mode=0, seed=1)
    assert len(result.sheets) == 1 and not result.unplaced
    nested = detect_nestings(result.sheets[0])
    assert len(nested) == 2  # both tiles chose the scrap window over open sheet


def test_cavity_seeds_pack_into_concave_pocket():
    from core.holes import cavity_candidate_seeds, placement_cavity_regions
    from shapely.geometry import Polygon

    # U-channel: 4x3 outline with a 2x2.5 pocket opening upward.
    u = Polygon([(0, 0), (4, 0), (4, 3), (3, 3), (3, 0.5), (1, 0.5), (1, 3), (0, 3)])
    part = _box_part("uchan", 4.0, 3.0, geom=u)
    variant = bn.Variant(part=part, angle_deg=0, geometry=u,
                         rotated_min_x=0, rotated_min_y=0, width=4.0, height=3.0)
    item = bn.Item(uid="uchan_1", part=part, variants=[variant])
    placement = bn.Placement(item, variant, 0, 0, 0, u)

    sheet = bn.SheetSpec(12.0, 4.0, margin=0.05, spacing=0.05, passes=1)
    regions = placement_cavity_regions(placement, sheet)
    assert regions, "the U-pocket must be detected as a cavity"
    assert max(r.area for r in regions) > 3.0

    tile_variant = bn.Variant(part=_box_part("t", 1.0, 1.0), angle_deg=0,
                              geometry=box(0, 0, 1.0, 1.0),
                              rotated_min_x=0, rotated_min_y=0, width=1.0, height=1.0)
    seeds = cavity_candidate_seeds(tile_variant, [placement], sheet)
    assert seeds, "cavity seeds must be proposed for a part that fits the pocket"


def test_hole_nesting_can_be_disabled(tmp_path):
    from core.holes import detect_nestings
    reqs = [
        bn.PartRequest(ROOT / "examples" / "parts" / "bulkhead.svg", 1, grain="free"),
        bn.PartRequest(ROOT / "examples" / "parts" / "aileron_rib.svg", 4, grain="free"),
    ]
    parts = [bn.load_part(r, 0.03) for r in reqs]

    on = bn.SheetSpec(12.0, 4.5, margin=0.05, spacing=0.05, passes=3, allow_nesting_in_holes=True)
    off = bn.SheetSpec(12.0, 4.5, margin=0.05, spacing=0.05, passes=3, allow_nesting_in_holes=False)

    res_on = bn.optimize_layout(bn.make_items(parts, on), on, seed=1)
    res_off = bn.optimize_layout(bn.make_items(parts, off), off, seed=1)

    nested_on = sum(len(detect_nestings(s)) for s in res_on.sheets)
    nested_off = sum(len(detect_nestings(s)) for s in res_off.sheets)
    assert nested_on >= 1  # nesting happens when enabled
    assert nested_off == 0  # and is fully prevented when disabled


def test_parts_pack_tightly_inside_hole():
    # Three tiles into a 4x4 scrap window of a 6x6 frame: they must pack
    # shoulder-to-shoulder in a corner of the window, not float mid-hole.
    from shapely.geometry import Polygon
    frame = Polygon(box(0, 0, 6, 6).exterior.coords, [box(1, 1, 5, 5).exterior.coords])
    frame_part = _box_part("frame", 6.0, 6.0, geom=frame)
    fv = bn.Variant(part=frame_part, angle_deg=0, geometry=frame,
                    rotated_min_x=0, rotated_min_y=0, width=6.0, height=6.0)
    frame_item = bn.Item(uid="frame_1", part=frame_part, variants=[fv])
    tiles = _box_item("tile", 1.2, 1.2, qty=3)

    sheet = bn.SheetSpec(20.0, 10.0, margin=0.05, spacing=0.05, passes=1, allow_mirror=False)
    result = bn.pack_once([frame_item] + tiles, sheet, mode=0, seed=1)
    assert not result.unplaced
    placed_tiles = [p for p in result.sheets[0].placements if "tile" in p.item.uid]
    assert len(placed_tiles) == 3
    # All nested, and their combined bbox is tight (not scattered around the hole).
    from core.holes import detect_nestings
    assert len(detect_nestings(result.sheets[0])) == 3
    xs0 = min(bn.placement_bounds(p)[0] for p in placed_tiles)
    ys0 = min(bn.placement_bounds(p)[1] for p in placed_tiles)
    xs1 = max(bn.placement_bounds(p)[2] for p in placed_tiles)
    ys1 = max(bn.placement_bounds(p)[3] for p in placed_tiles)
    tiles_bbox = (xs1 - xs0) * (ys1 - ys0)
    # 3 tiles of 1.44 in^2: tight L/row packing stays well under 2x their area.
    assert tiles_bbox <= 2.0 * 3 * 1.44, f"tiles scattered: bbox {tiles_bbox:.2f} in^2"


def test_scrap_in_scrap_recursive_nesting():
    # Big frame holds a medium frame in its window; the medium frame's own
    # window holds a small tile. Two levels of scrap reuse.
    from shapely.geometry import Polygon
    from core.holes import detect_nestings

    big = Polygon(box(0, 0, 8, 8).exterior.coords, [box(1, 1, 7, 7).exterior.coords])
    med = Polygon(box(0, 0, 4, 4).exterior.coords, [box(0.8, 0.8, 3.2, 3.2).exterior.coords])
    big_part = _box_part("big", 8.0, 8.0, geom=big)
    med_part = _box_part("med", 4.0, 4.0, geom=med)
    bv = bn.Variant(part=big_part, angle_deg=0, geometry=big,
                    rotated_min_x=0, rotated_min_y=0, width=8.0, height=8.0)
    mv = bn.Variant(part=med_part, angle_deg=0, geometry=med,
                    rotated_min_x=0, rotated_min_y=0, width=4.0, height=4.0)
    items = [
        bn.Item(uid="big_1", part=big_part, variants=[bv]),
        bn.Item(uid="med_1", part=med_part, variants=[mv]),
    ] + _box_item("tiny", 1.0, 1.0, qty=1)

    sheet = bn.SheetSpec(24.0, 12.0, margin=0.05, spacing=0.05, passes=1, allow_mirror=False)
    result = bn.pack_once(items, sheet, mode=0, seed=1)
    assert not result.unplaced
    nestings = {c.item.uid: par.item.uid for c, par in detect_nestings(result.sheets[0])}
    assert nestings.get("med_1") == "big_1"  # medium inside big's window
    assert "tiny_0" in nestings              # tiny nested in scrap too (med's or big's window)


def test_pack_marks_unplaced_when_capped():
    sheet = bn.SheetSpec(
        width=6.0, height=2.0, grain_axis="x", margin=0.05,
        spacing=0.05, grid_step=0.1, passes=1, max_sheets=1,
    )
    items = _box_item("tile", 2.5, 1.6, qty=8)
    result = bn.pack_once(items, sheet, mode=0, seed=1)
    assert len(result.sheets) == 1
    assert result.unplaced  # cannot all fit on one capped sheet


# --- Labels -------------------------------------------------------------------

def test_label_fits_and_sits_inside_part():
    from shapely.geometry import Point
    opts = bn.OutputOptions()
    part = _box_part("wingrib", 2.0, 1.0)
    variant = bn.Variant(
        part=part, angle_deg=0, geometry=box(0.2, 0.3, 2.2, 1.3),
        rotated_min_x=0, rotated_min_y=0, width=2.0, height=1.0,
    )
    item = bn.Item(uid="wingrib_1", part=part, variants=[variant])
    placement = bn.Placement(item, variant, 0, 0.2, 0.3, box(0.2, 0.3, 2.2, 1.3))
    layout = bn.SheetLayout(placements=[placement])

    specs, skipped = bn.build_label_specs(layout, opts)
    assert skipped == 0
    assert len(specs) == 1
    spec = specs[0]
    assert spec.text == "wingrib"
    assert box(0.2, 0.3, 2.2, 1.3).contains(
        Point(spec.center_x_in, spec.center_y_in)
    )


def _label_block(spec):
    max_chars = max(len(line) for line in spec.lines)
    w = max_chars * 0.62 * spec.font_in  # matches LABEL_CHAR_WIDTH_RATIO
    h = len(spec.lines) * spec.line_spacing * spec.font_in
    return box(
        spec.center_x_in - w / 2.0,
        spec.center_y_in - h / 2.0,
        spec.center_x_in + w / 2.0,
        spec.center_y_in + h / 2.0,
    )


def _one_placement(name, geom, w, h):
    part = _box_part(name, w, h, geom=geom)
    variant = bn.Variant(
        part=part, angle_deg=0, geometry=geom, rotated_min_x=0, rotated_min_y=0, width=w, height=h
    )
    item = bn.Item(uid=f"{name}_1", part=part, variants=[variant])
    return bn.Placement(item, variant, 0, geom.bounds[0], geom.bounds[1], geom)


def test_label_block_stays_inside_material():
    # A tapered triangle: a naive centre would push a wide label past the slope.
    from shapely.geometry import Polygon
    poly = Polygon([(0, 0), (3, 0), (0, 2)])
    placement = _one_placement("wedge", poly, 3.0, 2.0)
    specs, warnings = bn.LabelPlanner(bn.OutputOptions()).plan(bn.SheetLayout([placement]))
    assert len(specs) == 1 and not warnings
    assert poly.contains(_label_block(specs[0]))


def test_label_wraps_multiword_on_narrow_part():
    part_box = box(0, 0, 0.8, 0.8)
    placement = _one_placement("aileron_rib", part_box, 0.8, 0.8)
    specs, _ = bn.LabelPlanner(bn.OutputOptions()).plan(bn.SheetLayout([placement]))
    assert len(specs) == 1
    assert len(specs[0].lines) >= 2  # forced onto multiple lines
    assert all("_" not in line for line in specs[0].lines)  # split on the word boundary
    assert part_box.contains(_label_block(specs[0]))


def test_single_word_stays_one_line_when_it_fits():
    part_box = box(0, 0, 3.0, 1.0)
    placement = _one_placement("airfoil", part_box, 3.0, 1.0)
    specs, _ = bn.LabelPlanner(bn.OutputOptions()).plan(bn.SheetLayout([placement]))
    assert specs[0].lines == ["airfoil"]


def test_tiny_part_skips_label():
    opts = bn.OutputOptions(label_min_font_in=0.06)
    part = _box_part("x", 0.05, 0.05)
    variant = bn.Variant(
        part=part, angle_deg=0, geometry=box(0, 0, 0.05, 0.05),
        rotated_min_x=0, rotated_min_y=0, width=0.05, height=0.05,
    )
    item = bn.Item(uid="x_1", part=part, variants=[variant])
    placement = bn.Placement(item, variant, 0, 0, 0, box(0, 0, 0.05, 0.05))
    specs, skipped = bn.build_label_specs(bn.SheetLayout(placements=[placement]), opts)
    assert specs == [] and skipped == 1


def test_multi_view_export_collapses_to_one_copy(tmp_path):
    # The same 2x1 piece drawn twice, 5 in apart -- like a SolidWorks two-view
    # export. The loader must keep one copy and note it, so the nester is free
    # to place pieces independently instead of pinning them 5 in apart.
    svg = tmp_path / "twoview.svg"
    svg.write_text(
        '<?xml version="1.0"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="8in" height="2in" viewBox="0 0 768 192">\n'
        '  <rect x="0" y="0" width="192" height="96"/>\n'
        '  <rect x="480" y="0" width="192" height="96"/>\n'
        "</svg>\n"
    )
    part = bn.load_part(bn.PartRequest(svg, 1, grain="free"), 0.02)
    assert part.base_width_in == pytest.approx(2.0, abs=0.02)  # one copy, not 7 in
    assert part.notes and "identical disconnected copies" in part.notes[0]


def test_distinct_islands_are_kept_with_note(tmp_path):
    svg = tmp_path / "distinct.svg"
    svg.write_text(
        '<?xml version="1.0"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="8in" height="2in" viewBox="0 0 768 192">\n'
        '  <rect x="0" y="0" width="192" height="96"/>\n'
        '  <circle cx="576" cy="48" r="48"/>\n'
        "</svg>\n"
    )
    part = bn.load_part(bn.PartRequest(svg, 1, grain="free"), 0.02)
    assert part.base_width_in == pytest.approx(6.5, abs=0.05)  # kept as drawn
    assert part.notes and "differ" in part.notes[0]


def test_concave_parts_interlock_like_tetris():
    # Two U-channels (4x3 outline, 2x2.5 pocket opening upward). Rotated 180°,
    # the second one's pocket faces down, so its centre bar can dip into the
    # first one's pocket -- the "handshake". Interlocked, the pair's combined
    # bbox is much smaller than side-by-side (8.05x3) or stacked (4x6.05).
    from shapely.affinity import rotate as shp_rotate, translate as shp_translate
    from shapely.geometry import Polygon

    u = Polygon([(0, 0), (4, 0), (4, 3), (3, 3), (3, 0.5), (1, 0.5), (1, 3), (0, 3)])
    part = _box_part("uchan", 4.0, 3.0, geom=u)
    variants = []
    for ang in (0.0, 180.0):
        g = shp_rotate(u, ang, origin=(0, 0))
        minx, miny, maxx, maxy = g.bounds
        g = shp_translate(g, -minx, -miny)
        variants.append(
            bn.Variant(part=part, angle_deg=ang, geometry=g,
                       rotated_min_x=minx, rotated_min_y=miny,
                       width=maxx - minx, height=maxy - miny)
        )
    items = [bn.Item(uid=f"uchan_{i}", part=part, variants=list(variants)) for i in (1, 2)]

    sheet = bn.SheetSpec(20.0, 12.0, margin=0.05, spacing=0.05, passes=1, allow_mirror=False)
    result = bn.pack_once(items, sheet, mode=0, seed=1)
    assert len(result.sheets) == 1 and not result.unplaced
    ps = result.sheets[0].placements
    assert ps[0].geometry.distance(ps[1].geometry) >= sheet.spacing - 1e-6

    min_x = min(bn.placement_bounds(p)[0] for p in ps)
    min_y = min(bn.placement_bounds(p)[1] for p in ps)
    max_x = max(bn.placement_bounds(p)[2] for p in ps)
    max_y = max(bn.placement_bounds(p)[3] for p in ps)
    union_area = (max_x - min_x) * (max_y - min_y)
    # Non-interlocked minimum is ~8.05*3 = 24.15; a real handshake beats it.
    assert union_area < 23.0, f"expected interlock, got union bbox {union_area:.2f} in^2"


def test_polish_pulls_stray_part_into_pocket():
    # A frame with a scrap window placed at origin, and a tile artificially
    # stranded far to the right (simulating a bad greedy decision). Polish must
    # pull the tile into the window and shrink the union footprint.
    from shapely.geometry import Polygon
    frame = Polygon(box(0.05, 0.05, 4.05, 4.05).exterior.coords,
                    [box(1.05, 1.05, 3.05, 3.05).exterior.coords])
    frame_part = _box_part("frame", 4.0, 4.0, geom=frame)
    fv = bn.Variant(part=frame_part, angle_deg=0,
                    geometry=Polygon(box(0, 0, 4, 4).exterior.coords,
                                     [box(1, 1, 3, 3).exterior.coords]),
                    rotated_min_x=0, rotated_min_y=0, width=4.0, height=4.0)
    frame_item = bn.Item(uid="frame_1", part=frame_part, variants=[fv])
    frame_pl = bn.Placement(frame_item, fv, 0, 0.05, 0.05, frame)

    tile_geom = box(10.0, 0.05, 11.0, 1.05)
    tile_part = _box_part("tile", 1.0, 1.0)
    tv = bn.Variant(part=tile_part, angle_deg=0, geometry=box(0, 0, 1, 1),
                    rotated_min_x=0, rotated_min_y=0, width=1.0, height=1.0)
    tile_item = bn.Item(uid="tile_1", part=tile_part, variants=[tv])
    tile_pl = bn.Placement(tile_item, tv, 0, 10.0, 0.05, tile_geom)

    sheet = bn.SheetSpec(20.0, 8.0, margin=0.05, spacing=0.05, passes=1, allow_mirror=False)
    layout = bn.SheetLayout([frame_pl, tile_pl])
    result = bn.LayoutResult(sheets=[layout], score=bn.score_layout([layout], sheet))
    area_before = result.score[2]

    polished = bn.polish_layout(result, sheet)
    assert polished.score[2] < area_before  # footprint shrank
    from core.holes import detect_nestings
    assert len(detect_nestings(polished.sheets[0])) == 1  # tile now in the window


def test_placement_minimizes_union_footprint():
    # A 6x2 bar already placed. A 2x2 tile placed to its right grows the union
    # bbox to 8.1x2 (~16.4 in^2); stacked above-left it would be 6x4.1 (~24.6).
    # The packer must pick the smaller combined footprint.
    sheet = bn.SheetSpec(20.0, 10.0, margin=0.05, spacing=0.05, passes=1, allow_mirror=False)
    bar = _box_item("bar", 6.0, 2.0)[0]
    tile = _box_item("tile", 2.0, 2.0)[0]

    result = bn.pack_once([bar, tile], sheet, mode=0, seed=1)
    assert len(result.sheets) == 1 and not result.unplaced
    ps = result.sheets[0].placements
    min_x = min(bn.placement_bounds(p)[0] for p in ps)
    min_y = min(bn.placement_bounds(p)[1] for p in ps)
    max_x = max(bn.placement_bounds(p)[2] for p in ps)
    max_y = max(bn.placement_bounds(p)[3] for p in ps)
    area = (max_x - min_x) * (max_y - min_y)
    assert area == pytest.approx(8.05 * 2.0, rel=0.05)


# --- DXF import ---------------------------------------------------------------

def _make_dxf(tmp_path, units_code):
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = units_code
    msp = doc.modelspace()
    # 2x1 rectangle with a circular lightening hole.
    msp.add_lwpolyline([(0, 0), (2, 0), (2, 1), (0, 1)], close=True)
    msp.add_circle((1.0, 0.5), 0.25)
    path = tmp_path / "part.dxf"
    doc.saveas(path)
    return path


def test_dxf_import_inches(tmp_path):
    path = _make_dxf(tmp_path, 1)  # inches
    part = bn.load_part(bn.PartRequest(path, 1, grain="free"), 0.02)
    assert part.base_width_in == pytest.approx(2.0, abs=0.02)
    assert part.base_height_in == pytest.approx(1.0, abs=0.02)
    holes = list(bn.iter_hole_polygons(part.geometry))
    assert len(holes) == 1
    assert holes[0].area == pytest.approx(3.14159 * 0.25 ** 2, rel=0.05)


def test_dxf_import_mm(tmp_path):
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.modelspace().add_lwpolyline([(0, 0), (25.4, 0), (25.4, 25.4), (0, 25.4)], close=True)
    path = tmp_path / "mm.dxf"
    doc.saveas(path)
    part = bn.load_part(bn.PartRequest(path, 1, grain="free"), 0.02)
    assert part.base_width_in == pytest.approx(1.0, abs=0.01)  # 25.4 mm == 1 in


def test_dxf_cad_features(tmp_path):
    # Realistic CAD-style export: closed spline outline, bulged-polyline slot,
    # circle, block INSERT, and a TEXT entity (which must be skipped).
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    pts = [(0, 0), (0.5, 0.35), (1.5, 0.5), (3, 0.3), (4, 0.05),
           (3, -0.15), (1.5, -0.25), (0.5, -0.2), (0, 0)]
    msp.add_spline(pts)
    msp.add_lwpolyline(
        [(1.0, 0.0, 0, 0, 1), (1.4, 0.0, 0, 0, 1)], format="xyseb", close=True
    )
    msp.add_circle((2.5, 0.05), 0.1)
    blk = doc.blocks.new("HOLE")
    blk.add_circle((0, 0), 0.05)
    msp.add_blockref("HOLE", (0.7, 0.05))
    msp.add_text("RIB-01", dxfattribs={"height": 0.1}).set_placement((1.5, 0.6))
    path = tmp_path / "cad.dxf"
    doc.saveas(path)

    part = bn.load_part(bn.PartRequest(path, 1, grain="free"), 0.01)
    assert part.base_width_in == pytest.approx(4.0, abs=0.02)
    assert len(part.paths) == 4  # spline + slot + circle + block circle (text skipped)
    holes = list(bn.iter_hole_polygons(part.geometry))
    assert len(holes) == 3  # slot, circle, and the circle inside the block


# --- PDF import ---------------------------------------------------------------

def _make_pdf(tmp_path, name="part.pdf", watermark_text=True, vector_watermark=False):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # US Letter, 72 pt/in
    shape = page.new_shape()
    # White background rect covering the page (as SolidWorks exports do) --
    # must be ignored as invisible ink.
    shape.draw_rect(pymupdf.Rect(0, 0, 612, 792))
    shape.finish(color=None, fill=(1, 1, 1))
    # 2x1 in rectangle with a 0.25 in radius lightening hole.
    shape.draw_rect(pymupdf.Rect(72, 72, 216, 144))
    shape.draw_circle(pymupdf.Point(144, 108), 18)
    shape.finish(color=(0, 0, 0), width=0.5)
    if vector_watermark:
        # Vectorized watermark: small strokes along the bottom edge.
        for i in range(6):
            x = 100 + i * 60
            shape.draw_line(pymupdf.Point(x, 780), pymupdf.Point(x + 40, 770))
        shape.finish(color=(0.5, 0.5, 0.5), width=0.5)
    shape.commit()
    if watermark_text:
        page.insert_text(
            pymupdf.Point(100, 782),
            "SOLIDWORKS Educational Product. For Instructional Use Only.",
            fontsize=10,
        )
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


def test_pdf_import_inches(tmp_path):
    # 72 pt = 1 in; text watermark and white background must be ignored.
    path = _make_pdf(tmp_path)
    part = bn.load_part(bn.PartRequest(path, 1, grain="free"), 0.02)
    assert part.base_width_in == pytest.approx(2.0, abs=0.02)
    assert part.base_height_in == pytest.approx(1.0, abs=0.02)
    holes = list(bn.iter_hole_polygons(part.geometry))
    assert len(holes) == 1
    assert holes[0].area == pytest.approx(3.14159 * 0.25 ** 2, rel=0.05)


def test_pdf_vector_watermark_dropped(tmp_path):
    path = _make_pdf(tmp_path, vector_watermark=True)
    part = bn.load_part(bn.PartRequest(path, 1, grain="free"), 0.02)
    assert part.base_width_in == pytest.approx(2.0, abs=0.02)
    assert part.base_height_in == pytest.approx(1.0, abs=0.02)
    assert any("watermark" in n for n in part.notes)


def test_dwg_rejected_with_guidance(tmp_path):
    p = tmp_path / "part.dwg"
    p.write_bytes(b"AC1027 fake dwg")
    with pytest.raises(bn.BalsaNestError, match="DXF or SVG"):
        bn.PartRequest(p, 1, grain="free").validate()


# --- Output conventions (hairline, grouping, label modes) ----------------------

def _write_one_part_svg(tmp_path, options):
    import xml.etree.ElementTree as ET
    from svgpathtools import parse_path

    part_box = box(0.05, 0.05, 2.05, 1.05)
    placement = _one_placement("wingrib", part_box, 2.0, 1.0)
    # Give the part a real vector outline so the writer emits a cut path
    # (source units = inches, viewBox origin 0).
    placement.item.part.paths = [parse_path("M 0,0 L 2,0 L 2,1 L 0,1 Z")]
    sheet = bn.SheetSpec(10.0, 5.0, margin=0.05, spacing=0.05)
    out = tmp_path / "out.svg"
    bn.write_sheet_svg(out, bn.SheetLayout([placement]), sheet, 1, 1, options)
    return ET.parse(out).getroot()


def test_output_uses_hairline_and_groups_label_with_part(tmp_path):
    ns = {"s": "http://www.w3.org/2000/svg"}
    root = _write_one_part_svg(tmp_path, bn.OutputOptions())
    # Cut path is red hairline.
    path = root.find(".//s:path", ns)
    style = path.get("style", "")
    assert "#ff0000" in style and "-inkscape-stroke:hairline" in style
    # Label text lives inside the part's group.
    group = root.find(".//s:g[@id='wingrib_1']", ns)
    assert group is not None
    assert group.find("s:text", ns) is not None
    # Document opens in inches.
    nv = root.find("{http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd}namedview")
    assert nv is not None


def test_output_outline_label_mode(tmp_path):
    ns = {"s": "http://www.w3.org/2000/svg"}
    root = _write_one_part_svg(
        tmp_path, bn.OutputOptions(label_mode="outline", label_outline_color="#0000ff")
    )
    text = root.find(".//s:text", ns)
    style = text.get("style", "")
    assert "#0000ff" in style and "hairline" in style and "fill:none" in style


def test_output_explicit_stroke_width(tmp_path):
    ns = {"s": "http://www.w3.org/2000/svg"}
    root = _write_one_part_svg(tmp_path, bn.OutputOptions(cut_stroke=1.0))
    path = root.find(".//s:path", ns)
    assert "stroke-width:1" in path.get("style", "")


# --- No-fit polygon -----------------------------------------------------------

def _variant_of(geom, name="v"):
    minx, miny, maxx, maxy = geom.bounds
    part = _box_part(name, maxx - minx, maxy - miny, geom=geom)
    return bn.Variant(
        part=part, angle_deg=0, geometry=geom,
        rotated_min_x=0, rotated_min_y=0, width=maxx - minx, height=maxy - miny,
    )


def test_nfp_of_two_unit_squares():
    # NFP of two unit squares (reference = bbox min corner) is the square
    # [-1, 1]^2: any offset inside overlaps, boundary is exact contact. The
    # engine works on a slightly inflated superset, so allow a small slack.
    from shapely.geometry import Point

    a = _variant_of(box(0, 0, 1, 1), "a")
    b = _variant_of(box(0, 0, 1, 1), "b")
    nfp = bn.nfp_for_pair(a, b, spacing=0.0)
    assert nfp is not None
    assert nfp.contains(Point(0.0, 0.0))      # coincident squares overlap
    assert nfp.contains(Point(0.9, 0.9))      # partial overlap
    assert not nfp.contains(Point(1.2, 0.0))  # well clear
    assert nfp.area == pytest.approx(4.0, rel=0.15)


def test_nfp_seeds_touch_but_never_collide():
    from shapely.affinity import translate as shp_translate
    from core.nfp import nfp_candidate_seeds

    sheet = bn.SheetSpec(10.0, 4.0, margin=0.1, spacing=0.05)
    sq = box(0, 0, 1, 1)
    v_placed = _variant_of(sq, "a")
    v_new = _variant_of(sq, "b")
    item = bn.Item(uid="a_1", part=v_placed.part, variants=[v_placed])
    pl = bn.Placement(item, v_placed, 0, 2.0, 1.0, shp_translate(sq, 2.0, 1.0))

    seeds = nfp_candidate_seeds(v_new, [pl], sheet)
    assert seeds
    touching = 0
    for x, y in seeds:
        g = shp_translate(sq, x, y)
        # Superset-based NFPs guarantee every seed is feasible for the exact
        # geometry -- no seed may violate the spacing.
        assert bn.is_collision_free(g, [pl], sheet.spacing)
        if g.distance(pl.geometry) < 0.15:
            touching += 1
    # A healthy share of seeds must actually hug the placed part at ~spacing.
    assert touching >= 4


def test_discs_nest_hexagonally_with_nfp():
    # Three 1-in discs on a sheet too narrow for a row of three. Square
    # stacking gives a 2.05-in-tall footprint; the tight answer nestles the
    # third disc into the valley between the first two (~1.91 in tall). That
    # position exists only where two spacing-inflated NFP circles intersect --
    # bbox contact lines and bottom-left sliding never find it.
    from shapely.geometry import Point

    disc = Point(0.5, 0.5).buffer(0.5, quad_segs=24)

    def disc_items(n):
        part = _box_part("disc", 1.0, 1.0, geom=disc)
        v = bn.Variant(part=part, angle_deg=0, geometry=disc,
                       rotated_min_x=0, rotated_min_y=0, width=1.0, height=1.0)
        return [bn.Item(uid=f"disc_{i}", part=part, variants=[v]) for i in range(n)]

    sheet = bn.SheetSpec(2.4, 4.0, margin=0.05, spacing=0.05, passes=1,
                         allow_mirror=False)
    result = bn.pack_once(disc_items(3), sheet, mode=0, seed=1)
    assert len(result.sheets) == 1 and not result.unplaced
    ps = result.sheets[0].placements
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            assert a.geometry.distance(b.geometry) >= sheet.spacing - 1e-6
    height = (
        max(bn.placement_bounds(p)[3] for p in ps)
        - min(bn.placement_bounds(p)[1] for p in ps)
    )
    assert height < 1.98, f"expected hexagonal nestle, got height {height:.3f} in"


# --- Machine defaults file ----------------------------------------------------

def test_defaults_file_merges_under_job_config(tmp_path):
    import json as _json

    defaults = {
        "cut_color": "#00ff00",
        "sheet": {"spacing": 0.11, "width": 99.0},
        "labels": {"mode": "outline"},
        "parts": [{"file": "must_be_ignored.svg"}],
        "output": "must_be_ignored.svg",
    }
    (tmp_path / "balsanest_defaults.json").write_text(_json.dumps(defaults))

    job_cfg = {
        "sheet": {"width": 10.0, "height": 4.0},
        "parts": [{"file": str(ROOT / "examples" / "parts" / "rib.svg")}],
        "output": "o.svg",
    }
    cfg_path = tmp_path / "job.json"
    cfg_path.write_text(_json.dumps(job_cfg))

    job = bn.load_config(cfg_path)
    assert job.options.cut_color == "#00ff00"      # default applied
    assert job.options.label_mode == "outline"     # nested default applied
    assert job.sheet.spacing == 0.11               # default fills the gap
    assert job.sheet.width == 10.0                 # job config wins over default
    assert len(job.requests) == 1                  # defaults' parts/output ignored
    assert job.output.name == "o.svg"


def test_no_defaults_file_keeps_builtin_defaults(tmp_path, monkeypatch):
    import json as _json

    # Point cwd somewhere empty so the repo's own defaults file cannot leak in.
    empty = tmp_path / "empty_cwd"
    empty.mkdir()
    monkeypatch.chdir(empty)

    job_cfg = {
        "sheet": {"width": 10.0, "height": 4.0},
        "parts": [{"file": str(ROOT / "examples" / "parts" / "rib.svg")}],
        "output": "o.svg",
    }
    cfg_path = tmp_path / "job.json"
    cfg_path.write_text(_json.dumps(job_cfg))
    job = bn.load_config(cfg_path)
    assert job.options.cut_color == "#ff0000"
    assert job.sheet.spacing == 0.04


# --- Compaction ---------------------------------------------------------------

def test_compaction_pulls_to_origin_corner():
    sheet = bn.SheetSpec(10.0, 4.0, grain_axis="x", margin=0.1, spacing=0.05)
    geom = box(5.0, 2.0, 6.0, 2.7)  # placed far from the origin, nothing nearby
    compacted = bn.compact_toward_origin(geom, [], sheet)
    min_x, min_y, _, _ = compacted.bounds
    # Should end tucked into the origin corner (small residual from step size).
    assert sheet.margin - 1e-6 <= min_x < sheet.margin + 0.1
    assert sheet.margin - 1e-6 <= min_y < sheet.margin + 0.1


# --- End-to-end integration ---------------------------------------------------

def test_end_to_end_layout_respects_spacing_and_labels_inside(tmp_path):
    from shapely.geometry import Point
    sheet = bn.SheetSpec(12.0, 3.0, grain_axis="x", margin=0.05, spacing=0.04, passes=4)
    reqs = [
        bn.PartRequest(ROOT / "examples" / "parts" / "rib.svg", 3, grain="parallel"),
        bn.PartRequest(ROOT / "examples" / "parts" / "gusset.svg", 4, grain="free"),
    ]
    parts = [bn.load_part(r, 0.02) for r in reqs]
    items = bn.make_items(parts, sheet)
    assert bn.preflight_capacity(parts, items, sheet) == []
    result = bn.optimize_layout(items, sheet, seed=7)
    assert not result.unplaced

    for layout in result.sheets:
        placements = layout.placements
        for i, a in enumerate(placements):
            for b in placements[i + 1:]:
                assert a.geometry.distance(b.geometry) >= sheet.spacing - 1e-6
        specs, _ = bn.build_label_specs(layout, bn.OutputOptions())
        for spec in specs:
            hit = any(
                p.geometry.buffer(1e-6).contains(Point(spec.center_x_in, spec.center_y_in))
                for p in placements
            )
            assert hit


# --- Common-line merging ------------------------------------------------------

def _square_placement(name, x):
    from svgpathtools import parse_path
    geom = box(x, 0.0, x + 1.0, 1.0)
    placement = _one_placement(name, geom, 1.0, 1.0)
    placement.item.part.paths = [parse_path("M 0,0 L 1,0 L 1,1 L 0,1 Z")]
    return placement


def _total_cut_segments(svg_path):
    import xml.etree.ElementTree as ET
    from svgpathtools import parse_path
    root = ET.parse(svg_path).getroot()
    total = 0
    for el in root.iter():
        style = el.get("style") or ""
        if el.tag.endswith("}path") and "stroke:" in style and "fill:none" in style:
            total += len(parse_path(el.get("d")))
    return total


def test_common_line_merge(tmp_path):
    # Two unit squares in exact contact share the x=1 edge: 8 segments total,
    # 7 once the shared edge is merged and cut once.
    layout = bn.SheetLayout(
        [_square_placement("sq_a", 0.0), _square_placement("sq_b", 1.0)]
    )
    sheet = bn.SheetSpec(10.0, 5.0, margin=0.05, spacing=0.0)

    off = tmp_path / "off.svg"
    bn.write_sheet_svg(off, layout, sheet, 1, 1, bn.OutputOptions(label_parts=False))
    assert _total_cut_segments(off) == 8

    on = tmp_path / "on.svg"
    bn.write_sheet_svg(
        on, layout, sheet, 1, 1,
        bn.OutputOptions(label_parts=False, merge_common_cuts=True),
    )
    assert _total_cut_segments(on) == 7


def test_common_line_merge_leaves_separated_parts_alone(tmp_path):
    # With a gap between the squares nothing coincides, so nothing merges.
    layout = bn.SheetLayout(
        [_square_placement("sq_a", 0.0), _square_placement("sq_b", 1.5)]
    )
    sheet = bn.SheetSpec(10.0, 5.0, margin=0.05, spacing=0.04)
    out = tmp_path / "out.svg"
    bn.write_sheet_svg(
        out, layout, sheet, 1, 1,
        bn.OutputOptions(label_parts=False, merge_common_cuts=True),
    )
    assert _total_cut_segments(out) == 8


# --- Genetic algorithm optimizer ----------------------------------------------

def test_ga_optimizer_places_all_and_not_worse():
    items = _box_item("plate", 2.0, 1.0, qty=3) + _box_item("tab", 1.0, 1.0, qty=2)
    sheet = bn.SheetSpec(8.0, 3.0, margin=0.05, spacing=0.04, passes=2)

    heuristic = bn.optimize_layout(items, sheet, seed=3)
    seen = []
    ga = bn.optimize_layout_ga(
        items, sheet, seed=3, population=4, generations=2,
        on_pass=lambda i, n: seen.append((i, n)),
    )
    assert not ga.unplaced
    assert ga.score <= heuristic.score
    assert seen == [(0, 2), (1, 2)]


def test_pack_in_order_respects_given_order():
    items = _box_item("big", 3.0, 1.0, qty=1) + _box_item("small", 1.0, 1.0, qty=1)
    sheet = bn.SheetSpec(8.0, 3.0, margin=0.05, spacing=0.04)
    result = bn.pack_in_order(list(reversed(items)), sheet)
    assert not result.unplaced
    assert len(result.sheets) == 1


# --- Polygonal (non-rectangular) sheets ---------------------------------------

def _triangle_sheet(size=8.0, **kw):
    from shapely.geometry import Polygon
    tri = Polygon([(0, 0), (size, 0), (0, size)])
    return bn.SheetSpec(size, size, margin=0.05, spacing=0.04, boundary=tri, **kw)


def test_polygon_sheet_containment():
    sheet = _triangle_sheet(8.0)
    sheet.validate()
    assert bn.geometry_fits_sheet(box(0.5, 0.5, 1.5, 1.5), sheet)
    # bbox-legal but outside the hypotenuse:
    assert not bn.geometry_fits_sheet(box(6.0, 6.0, 7.0, 7.0), sheet)


def test_polygon_sheet_packs_within_boundary():
    sheet = _triangle_sheet(8.0, passes=1)
    items = _box_item("tile", 1.5, 1.0, qty=4)
    result = bn.pack_once(items, sheet, mode=0, seed=1)
    assert not result.unplaced
    assert len(result.sheets) == 1
    region = sheet.boundary.buffer(-sheet.margin + 1e-6)
    for p in result.sheets[0].placements:
        assert region.contains(p.geometry.buffer(-1e-9))


def test_load_sheet_boundary_and_config(tmp_path):
    svg = tmp_path / "sheet_outline.svg"
    svg.write_text(
        '<?xml version="1.0"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="6in" height="4in" viewBox="0 0 576 384">\n'
        '  <path d="M 0,384 L 576,384 L 576,0 Z"/>\n'
        "</svg>\n"
    )
    boundary, w, h = bn.load_sheet_boundary(svg, 0.02)
    assert w == pytest.approx(6.0, abs=0.05)
    assert h == pytest.approx(4.0, abs=0.05)
    assert boundary.area == pytest.approx(12.0, rel=0.03)  # right triangle

    cfg = {
        "sheet": {"outline_file": str(svg), "margin": 0.05},
        "parts": [{"file": str(ROOT / "examples" / "parts" / "gusset.svg"), "quantity": 2}],
        "output": str(tmp_path / "o.svg"),
    }
    job = bn.config_to_specs(cfg, tmp_path)
    assert job.sheet.boundary is not None
    assert job.sheet.width == pytest.approx(6.0, abs=0.05)


def test_polygon_sheet_outline_layer_in_output(tmp_path):
    import xml.etree.ElementTree as ET

    def outline_layers(options):
        out = tmp_path / "tri.svg"
        bn.write_sheet_svg(
            out, bn.SheetLayout([_square_placement("sq", 0.5)]),
            _triangle_sheet(8.0), 1, 1, options,
        )
        root = ET.parse(out).getroot()
        labels = [g.get("{http://www.inkscape.org/namespaces/inkscape}label")
                  for g in root.iter() if g.tag.endswith("}g")]
        return [l for l in labels if l and "Sheet outline" in l]

    # Laser-ready export: no reference outline of the custom sheet shape.
    assert not outline_layers(bn.OutputOptions(label_parts=False))
    # Preview render: the dashed outline layer is present.
    assert outline_layers(bn.OutputOptions(label_parts=False, draw_boundary=True))


# --- Label raster-band optimization -------------------------------------------

def test_labels_band_near_parts_but_not_across_sheet():
    # A and B sit side by side at different heights; their labels can slide
    # vertically, so they must land on ONE shared band. C is identical but far
    # to the right -- banding it with A/B would sweep the raster head across
    # the whole sheet, so its label must stay on its own level.
    a = _one_placement("part a", box(0.0, 0.0, 2.0, 3.0), 2.0, 3.0)
    b = _one_placement("part b", box(2.2, 0.8, 4.2, 3.8), 2.0, 3.0)
    c = _one_placement("part c", box(20.0, 0.8, 22.0, 3.8), 2.0, 3.0)
    layout = bn.SheetLayout([a, b, c])
    specs, warnings = bn.LabelPlanner(bn.OutputOptions()).plan(layout)
    assert not warnings and len(specs) == 3
    by_name = {s.text: s for s in specs}
    # near neighbours share a raster band despite different natural centres:
    assert by_name["part a"].center_y_in == pytest.approx(by_name["part b"].center_y_in, abs=1e-6)
    # the far part keeps its own natural level (never dragged onto their band):
    assert by_name["part c"].center_y_in == pytest.approx(2.3, abs=0.1)
    # every label still sits fully inside its part:
    for placement, name in ((a, "part a"), (b, "part b"), (c, "part c")):
        s = by_name[name]
        from core.labels import _block, LABEL_LINE_SPACING
        from core.constants import LABEL_CHAR_WIDTH_RATIO
        w = max(len(l) for l in s.lines) * LABEL_CHAR_WIDTH_RATIO * s.font_in
        h = len(s.lines) * LABEL_LINE_SPACING * s.font_in
        assert placement.geometry.contains(_block(s.center_x_in, s.center_y_in, w, h))

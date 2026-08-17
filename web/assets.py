from __future__ import annotations

from base64 import b64encode
from pathlib import Path

import gradio as gr

FORCE_DARK_HEAD = (
    "<script>(function(){"
    "var u=new URL(window.location);"
    "if(u.searchParams.get('__theme')!=='dark'){"
    "u.searchParams.set('__theme','dark');"
    "window.location.replace(u.href);}"
    "})();</script>"
)


FORCE_DARK_JS = """
() => {
  const url = new URL(window.location);
  if (url.searchParams.get('__theme') !== 'dark') {
    url.searchParams.set('__theme', 'dark');
    window.history.replaceState({}, '', url.href);
  }
  document.documentElement.classList.add('dark');
  document.body.classList.add('dark');
}
"""

REPO_URL = "https://github.com/RU-Airborne/BalsaNest"
AUTHOR_URL = "https://github.com/scavenx"
DISCORD_HANDLE = "scaaavx"


def logo_header_html() -> str:
    """Header block: nest logo beside the BalsaNest title and its credit line."""
    logo = Path(__file__).with_name("balsanest_logo.png")
    try:
        b64 = b64encode(logo.read_bytes()).decode()
        img = (
            '<div class="logo-glow">'
            f'<img src="data:image/png;base64,{b64}" alt="BalsaNest logo" '
            'style="height:104px;width:auto;flex:none"></div>'
        )
    except OSError:  # logo not bundled: fall back to text-only header
        img = ""
    return (
        '<div style="display:flex;align-items:center;gap:18px">'
        f"{img}"
        '<div class="title-block">'
        '<h1 style="line-height:1.1;font-size:44px">BalsaNest</h1>'
        '<p>&nbsp;Developed by '
        f'<a class="author-link" href="{AUTHOR_URL}" target="_blank" '
        'rel="noopener">Scaven X</a> at RU Airborne</p></div>'
        f"{github_link_html()}"
        "</div>"
    )


def github_link_html() -> str:
    """The repository link at the right edge of the header."""
    return (
        f'<a class="gh-link" href="{REPO_URL}" '
        'target="_blank" rel="noopener" title="BalsaNest on GitHub" '
        'aria-label="BalsaNest on GitHub">'
        '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" '
        'aria-hidden="true">'
        '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17'
        ".55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94"
        "-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87"
        " 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59"
        ".82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27"
        "s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82"
        " 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01"
        ' 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>'
        "</svg><span>Open project on GitHub</span></a>"
    )


def footer_html() -> str:
    """Bug-report line closing the page."""
    return (
        '<div class="site-footer">Found a bug? DM Scaven X '
        f"(<code>{DISCORD_HANDLE}</code>) on Discord, or&#8202;"
        f'<a href="{REPO_URL}/issues" target="_blank" rel="noopener">'
        "open an issue on GitHub</a>.</div>"
    )


CSS = """
.gradio-container { max-width: 1440px !important; margin: 0 auto !important; }
/* Accent-red glow tracing the logo silhouette, breathing slowly. Layered
   drop-shadows follow the PNG's alpha edge, so the glow reads as backlight
   on the artwork rather than a shape parked behind it. The header containers
   must not clip the shadow bleed. */
#header-row, #header-row .block, #header-row .html-container,
#header-row .prose { overflow: visible !important; }
.logo-glow { flex: none; display: flex; }
/* Small GitHub project link at the right edge of the header: rounded rect
   with the octocat mark and a text label. */
.gh-link {
  margin-left: auto;
  display: flex; align-items: center; gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--border-color-primary);
  border-radius: 6px;
  background: var(--background-fill-secondary);
  color: var(--body-text-color);
  font-size: 12px; white-space: nowrap;
  opacity: 0.85;
  text-decoration: none !important;
}
.gh-link:hover {
  border-color: #be2e35;
  color: #be2e35;
  opacity: 1;
}
/* Bug-report line closing the page. Gradio's own footer is hidden further
   down, so this is a plain div rather than a <footer>. */
.site-footer {
  margin: 22px 0 6px;
  padding-top: 14px;
  border-top: 1px solid var(--border-color-primary);
  text-align: center;
  font-size: 13px;
  opacity: 0.75;
}
.site-footer a { color: var(--body-text-color); text-decoration: underline;
                 text-underline-offset: 2px; }
.site-footer a:hover { color: #be2e35; }
.site-footer code {
  font-size: 12px; padding: 1px 5px; border-radius: 4px;
  background: var(--background-fill-secondary);
}
/* Title and credit line share one left edge: Gradio's prose styles otherwise
   indent the heading and the paragraph by different amounts. */
.title-block h1, .title-block p {
  margin: 0 !important; padding: 0 !important;
  text-indent: 0 !important; text-align: left !important;
}
.title-block h1 { margin-bottom: 4px !important; }
/* An inline link inherits the theme's link box model, which pads it away from
   the words on either side. This one has to sit in the sentence. */
.author-link {
  display: inline !important;
  margin: 0 !important; padding: 0 !important; border: 0 !important;
  background: none !important;
  color: var(--body-text-color);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.author-link:hover { color: #be2e35; }
/* The logo's halo, kept as two custom properties so the radius and burn can be
   retuned in one place. The three layered shadows follow the PNG's alpha edge,
   so it reads as backlight on the artwork rather than a shape behind it. */
:root {
  --logo-glow-r: 18;      /* px, middle halo radius */
  --logo-glow-s: 0.7;     /* 0-1; the inner layer saturates at 1 */
}
.logo-glow img {
  filter: drop-shadow(0 0 calc(var(--logo-glow-r) * 0.34px)
                      rgba(190, 46, 53, calc(var(--logo-glow-s) * 1.5)))
          drop-shadow(0 0 calc(var(--logo-glow-r) * 1px)
                      rgba(190, 46, 53, var(--logo-glow-s)))
          drop-shadow(0 0 calc(var(--logo-glow-r) * 2.4px)
                      rgba(190, 46, 53, calc(var(--logo-glow-s) * 0.52)));
}
.part-card { border: 1px solid var(--border-color-primary); border-radius: 10px;
             padding: 8px 12px; margin-bottom: 8px; height: 100%; }
.part-thumb img { background: #fff; border-radius: 8px; }
#header-row { align-items: center; }
#header-row .prose h1 { margin-bottom: 2px; }
.section-title h2 { text-align: center; margin: 10px 0 2px; opacity: 0.9; }
#visualizer {
  width: 70%; margin: 4px auto 14px;
  padding: 16px 18px;
  background: var(--background-fill-secondary);
  border: 1px solid var(--border-color-primary);
  border-radius: 12px;
  box-shadow: 0 8px 28px rgba(0,0,0,.45), 0 2px 8px rgba(0,0,0,.35);
}
.empty-note { text-align: center; opacity: 0.8; }
/* Component titles (sliders, dropdowns, colour pickers) render in the muted
   block-title colour while checkbox and accordion labels use the strong body
   colour. Use the strong colour everywhere. */
span[data-testid="block-info"] { color: var(--body-text-color) !important; }
/* Markdown blocks restyled to match component info/description text. */
.info-text p {
  font-size: var(--block-info-text-size) !important;
  color: var(--block-info-text-color) !important;
}
.remove-part-btn {
  margin-top: 12px !important;
  background: rgba(190, 46, 53, 0.14) !important;
  border: 1px solid rgba(190, 46, 53, 0.45) !important;
  color: #ff9aa0 !important;
}
.remove-part-btn:hover {
  background: rgba(190, 46, 53, 0.28) !important;
}
/* The active custom-shape note (hidden from Python while no shape is set). */
.outline-note { margin: 6px 0 !important; padding: 0 12px; }
/* The drawing modal's render slot: Gradio can leave the container's
   "generating" indicator (a pulsing accent border) stuck after the modal
   closes, showing a red bar above Parts. While the modal is closed the slot
   must be completely invisible; while open, only the pulse is suppressed. */
#draw-modal-slot:not(:has(#draw-modal)),
#draw-modal-slot:not(:has(#draw-modal)) * {
  border: none !important;
  box-shadow: none !important;
  animation: none !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
}
#draw-modal-slot .generating {
  animation: none !important;
  border-color: transparent !important;
}
.legend-row {
  display: flex !important;
  justify-content: center !important;
  width: 100% !important;
}
#job-row { justify-content: center; }
.info-text.centered p { text-align: center; }
#draw-modal {
  position: fixed; top: 3vh; left: 0; right: 0; margin: 0 auto;
  width: min(1040px, 94vw); max-height: 93vh; overflow-y: auto;
  z-index: 1000; padding: 14px;
  background: var(--background-fill-primary);
  border: 1px solid var(--border-color-primary); border-radius: 12px;
  box-shadow: 0 14px 60px rgba(0,0,0,.65);
}
#draw-modal-head { align-items: flex-start; }
.evolve-banner {
  border: 1px solid #be2e35; background: rgba(190,46,53,.16); color: #ff9aa0;
  padding: 10px 14px; border-radius: 8px; font-weight: 600;
  letter-spacing: .3px; margin-bottom: 8px;
  animation: evolvepulse 1.4s ease-in-out infinite;
}
@keyframes evolvepulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(190,46,53,.35); }
  50% { box-shadow: 0 0 16px 3px rgba(190,46,53,.35); }
}
footer { display: none !important; }
"""


def accent_hue() -> gr.themes.Color:
    """Accent colour rgb(190, 46, 53) with generated light/dark shades."""
    return gr.themes.Color(
        c50="#fdeced", c100="#f8d3d5", c200="#f0a6aa", c300="#e57a80",
        c400="#d4535a", c500="#be2e35", c600="#a3272d", c700="#872025",
        c800="#6b191d", c900="#4f1216", c950="#380c0f",
    )


def app_theme() -> gr.themes.Base:
    """The dark theme, deliberately darker and neutral rather than the blue-grey
    Gradio ships. `zinc` drops most of slate's blue cast; the explicit fills go
    darker still and carry a touch of warmth (red channel a shade above blue),
    so the page sits under the accent red and the nest browns instead of
    fighting them."""
    return gr.themes.Default(
        primary_hue=accent_hue(), neutral_hue="zinc"
    ).set(
        body_background_fill_dark="#111010",
        background_fill_primary_dark="#191817",
        background_fill_secondary_dark="#201f1d",
        block_background_fill_dark="#191817",
        panel_background_fill_dark="#1b1a19",
        input_background_fill_dark="#201f1d",
        block_label_background_fill_dark="#201f1d",
        border_color_primary_dark="#2f2d2b",
        block_border_color_dark="#2f2d2b",
    )


# --- option legends ----------------------------------------------------------

def _wood_svg(
    w: int, h: int, vertical: bool = False, inner: str = "", grain: bool = True
) -> str:
    """A small wood-sheet thumbnail (optionally with grain lines) for legends."""
    lines = []
    if grain:
        if vertical:
            for x in range(10, w - 4, 9):
                lines.append(f'<line x1="{x}" y1="5" x2="{x}" y2="{h - 5}"/>')
        else:
            for y in range(10, h - 4, 9):
                lines.append(f'<line x1="5" y1="{y}" x2="{w - 5}" y2="{y}"/>')
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'style="display:block;margin:0 auto">'
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="5" '
        f'fill="#c9a06c" stroke="#8a6a3f"/>'
        f'<g stroke="#8a6a3f" stroke-width="1" opacity="0.55">{"".join(lines)}</g>'
        f"{inner}</svg>"
    )


def _legend_entry(thumb: str, caption: str) -> str:
    return (
        f'<div style="text-align:center;max-width:150px">{thumb}'
        f'<div style="font-size:12px;opacity:.85;line-height:1.25;margin-top:2px">'
        f"{caption}</div></div>"
    )


SHEET_GRAIN_HTML = (
    '<div class="legend-row" style="display:flex;gap:22px;margin:4px 0 2px;flex-wrap:wrap;justify-content:center">'
    + _legend_entry(_wood_svg(120, 58), "x &mdash; grain runs left to right")
    + _legend_entry(_wood_svg(120, 58, vertical=True), "y &mdash; grain runs bottom to top")
    + "</div>"
)

# Part thumbnails drawn on a sheet whose grain runs left-to-right.
_PART_H = '<rect x="27" y="21" width="66" height="16" rx="6" fill="#c0392b" opacity="0.9"/>'
_PART_V = '<rect x="52" y="7" width="16" height="44" rx="6" fill="#c0392b" opacity="0.9"/>'
# Free = the same part shown in both its 0 deg and 90 deg orientations.
_PART_R = (
    '<rect x="18" y="24" width="42" height="12" rx="5" fill="#c0392b" opacity="0.9"/>'
    '<rect x="80" y="13" width="12" height="34" rx="5" fill="#c0392b" opacity="0.9"/>'
)

GRAIN_RULE_HTML = (
    '<div class="legend-row" style="display:flex;gap:18px;margin:2px 0 6px;flex-wrap:wrap;justify-content:center">'
    + _legend_entry(
        _wood_svg(112, 58, inner=_PART_H),
        "<b>Parallel</b> &nbsp; the part's grain runs with the sheet grain (strongest)",
    )
    + _legend_entry(
        _wood_svg(112, 58, inner=_PART_V),
        "<b>Perpendicular</b> &nbsp; the part sits across the sheet grain",
    )
    + _legend_entry(
        _wood_svg(112, 58, inner=_PART_R),
        "<b>Free</b> &nbsp; grain does not matter, the part may only be turned "
        "in right-angle (90&#176;) steps",
    )
    + "</div>"
)

LABEL_STYLE_HTML = (
    '<div class="legend-row" style="display:flex;gap:22px;margin:2px 0 4px;flex-wrap:wrap;justify-content:center">'
    + _legend_entry(
        _wood_svg(
            150, 44, grain=False,
            inner='<text x="75" y="27" text-anchor="middle" font-size="15" '
            'font-weight="bold" font-family="sans-serif" fill="#1a1a1a">RU_Airborne</text>',
        ),
        "<b>Raster</b> &nbsp; solid filled letters",
    )
    + _legend_entry(
        _wood_svg(
            150, 44, grain=False,
            inner='<text x="75" y="27" text-anchor="middle" font-size="15" '
            'font-weight="bold" font-family="sans-serif" fill="none" '
            'stroke="#1d4ed8" stroke-width="0.8">RU_Airborne</text>',
        ),
        "<b>Outline</b> &nbsp; traced letter outlines",
    )
    + "</div>"
)

def font_preview_html(font: str) -> str:
    """Sample of the chosen label font on a wood chip, with a visible caution
    when the font is not the safe generic. The browser renders locally
    installed fonts by family name, so any installed font previews."""
    family = "".join(c for c in (font or "sans-serif") if c not in "\"'<>&;")
    warning = ""
    if family != "sans-serif":
        warning = (
            '<div style="text-align:center;color:#ffb454;font-size:13px;'
            'margin:0 0 6px">'
            f"{family} must also be installed on the computer that opens the "
            "file, or its software substitutes another font and the labels "
            "can change size.</div>"
        )
    return (
        '<div style="display:flex;justify-content:center;margin:2px 0 6px">'
        '<div style="background:#c9a06c;border:1px solid #8a6a3f;'
        "border-radius:6px;padding:7px 20px;color:#1a1a1a;font-size:20px;"
        f"font-weight:bold;font-family:&quot;{family}&quot;\">"
        "RU_Airborne</div></div>" + warning
    )


# Kerf legend: a dashed drawn outline on wood, with the dark burn band either
# centred on the line (off) or pushed outside it (on).
_KERF_OFF = (
    '<rect x="38" y="16" width="74" height="36" rx="3" fill="none" '
    'stroke="#3a3a42" stroke-width="7" opacity="0.9"/>'
    '<rect x="38" y="16" width="74" height="36" rx="3" fill="none" '
    'stroke="#f2f2f6" stroke-width="1.3" stroke-dasharray="4 3"/>'
)
_KERF_ON = (
    '<rect x="34.5" y="12.5" width="81" height="43" rx="4" fill="none" '
    'stroke="#3a3a42" stroke-width="7" opacity="0.9"/>'
    '<rect x="38" y="16" width="74" height="36" rx="3" fill="none" '
    'stroke="#f2f2f6" stroke-width="1.3" stroke-dasharray="4 3"/>'
)

KERF_RULE_HTML = (
    '<div class="legend-row" style="display:flex;gap:22px;margin:2px 0 4px;flex-wrap:wrap;justify-content:center">'
    + _legend_entry(
        _wood_svg(150, 68, grain=False, inner=_KERF_OFF),
        "<b>0 (off)</b> &nbsp; The burn is centred on the drawn line, so half the burn falls inside the "
        "shape and every part come out slightly smaller than intended",
    )
    + _legend_entry(
        _wood_svg(150, 68, grain=False, inner=_KERF_ON),
        "<b>On</b> &nbsp; the beam runs half a kerf outside the line, so the "
        "part matches the drawing exactly",
    )
    + "</div>"
)

_FACE_A = (
    "M 16,44 C 22,30 38,24 62,26 C 100,29 130,34 152,38 "
    "C 128,43.6 70,50 40,49 C 26,48.5 15,47 16,44 Z"
)
_FACE_B = (
    "M 17.8,43.85 C 23.8,31.4 38.4,25.6 62,27.4 C 99.6,30.6 128.6,34.9 152,38 "
    "C 129,44.4 70,46 40.4,47.7 C 27.4,47.1 17.1,46.3 17.8,43.85 Z"
)
# The trailing-edge window its two panels are cropped to (x y w h).
_TE_VIEW = "121 30.3 38 15.7"

# The other shape doubling takes, on a corner: each straight edge is drawn
# twice and a short line runs across between the two corner points. Material
# lies below and left of the edges, so face A is the outer copy of both.
_CORNER_A = "M -10,9 L 44,9 L 56,45 L -10,45 Z"
_CORNER_B = "M -10,15 L 37,15 L 49,51 L -10,51 Z"
_CORNER_JOIN = "M 37,15 L 44,9"
_CORNER_VIEW = "0 3 70 28.9"

_FACE_A_COLOR = "#c0392b"
_FACE_B_COLOR = "#1f6feb"
_JOIN_COLOR = "#2f3540"

# Every panel is drawn at the width _legend_entry allows, so the row centres
# and keeps its gap instead of overflowing.
_PANEL_W, _PANEL_H = 150, 62


def _stroke(d: str, color: str, width: float) -> str:
    return (
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
        f'stroke-linejoin="round"/>'
    )


def _outer_envelope_d(*d_strings: str) -> str:
    """The contour a weld keeps: whichever of the given contours is outermost at
    each point, i.e. the exterior of their union.

    Derived from the same paths the first panel draws, rather than drawn by
    hand, so the legend cannot promise a result the importer would not produce."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from svgpathtools import parse_path

    polys = []
    for d in d_strings:
        # Per closed subpath: sampling a whole multi-subpath d would bridge the
        # jump between them and fuse two separate contours into one blob.
        for sub in parse_path(d).continuous_subpaths():
            pts = [
                (z.real, z.imag)
                for z in (sub.point(i / 300.0) for i in range(301))
            ]
            poly = Polygon(pts).buffer(0)
            if not poly.is_empty:
                polys.append(poly)
    merged = unary_union(polys)
    geoms = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    return " ".join(
        "M "
        + " L ".join(f"{x:.2f},{y:.2f}" for x, y in g.simplify(0.08).exterior.coords)
        + " Z"
        for g in geoms
    )


def _detail_svg(w: int, h: int, view: str, inner: str) -> str:
    """A wood chip cropped to `view` (an SVG viewBox), for magnified details.

    The corner radius and border are given in view units scaled to the panel's
    pixel size, so the chip is drawn with the same 5px radius and 1px edge as
    the sheet thumbnails in the other legends however far the view is zoomed."""
    vx, vy, vw, vh = (float(v) for v in view.split())
    per_px = vw / float(w)
    _detail_svg.count = getattr(_detail_svg, "count", 0) + 1
    clip = f"weld-chip-{_detail_svg.count}"
    chip = (
        f'x="{vx + per_px:.4g}" y="{vy + per_px:.4g}" '
        f'width="{vw - 2 * per_px:.4g}" height="{vh - 2 * per_px:.4g}" '
        f'rx="{5 * per_px:.4g}"'
    )
    return (
        f'<svg width="{w}" height="{h}" viewBox="{view}" '
        f'style="display:block;margin:0 auto">'
        f'<defs><clipPath id="{clip}"><rect {chip}/></clipPath></defs>'
        f'<rect {chip} fill="#c9a06c" stroke="#8a6a3f" '
        f'stroke-width="{per_px:.4g}"/>'
        # Contours run off the edge of the crop, so they are clipped to the
        # chip instead of poking past its rounded corners.
        f'<g clip-path="url(#{clip})">{inner}</g>'
        "</svg>"
    )


_TE_TWO_LINES = (
    _stroke(_FACE_A, _FACE_A_COLOR, 0.34) + _stroke(_FACE_B, _FACE_B_COLOR, 0.34)
)
_TE_ONE_LINE = _stroke(_outer_envelope_d(_FACE_A, _FACE_B), _FACE_A_COLOR, 0.34)
_CORNER_TWO_LINES = (
    _stroke(_CORNER_A, _FACE_A_COLOR, 0.62)
    + _stroke(_CORNER_B, _FACE_B_COLOR, 0.62)
    + _stroke(_CORNER_JOIN, _JOIN_COLOR, 0.62)
)
_CORNER_ONE_LINE = _stroke(
    _outer_envelope_d(_CORNER_A, _CORNER_B), _FACE_A_COLOR, 0.62
)


def _weld_row(left: str, left_cap: str, right: str, right_cap: str) -> str:
    return (
        '<div class="legend-row" style="display:flex;gap:22px;margin:2px 0 4px;'
        'flex-wrap:wrap;justify-content:center">'
        + _legend_entry(left, left_cap)
        + _legend_entry(right, right_cap)
        + "</div>"
    )


WELD_RULE_HTML = _weld_row(
    _detail_svg(_PANEL_W, _PANEL_H, _TE_VIEW, _TE_TWO_LINES),
    "<b>Before repair (as exported)</b> &nbsp; typically caused by a tapered part, the part's "
    "two faces are represented by two closely spaced contours. These contours "
    "converge where the taper ends and may cross along the way, so neither "
    "contour can be consistently identified as the inner one.",
    _detail_svg(_PANEL_W, _PANEL_H, _TE_VIEW, _TE_ONE_LINE),
    "<b>Welded</b> &nbsp; contours within this tolerance are merged into a "
    "single cut line, following the outermost edge of the two. The laser then "
    "cuts the edge only once.",
) + _weld_row(
    _detail_svg(_PANEL_W, _PANEL_H, _CORNER_VIEW, _CORNER_TWO_LINES),
    "<b>Before repair (as exported)</b> &nbsp; the same doubling can occur at a corner. Both "
    "straight edges are drawn twice, with a short line connecting the two "
    "corner points.",
    _detail_svg(_PANEL_W, _PANEL_H, _CORNER_VIEW, _CORNER_ONE_LINE),
    "<b>Welded</b> &nbsp; the duplicate edges are merged into a single corner "
    "following the outermost edges, while the short connecting line is "
    "absorbed during the repair.",
)

OR_DIVIDER_HTML = (
    '<div style="display:flex;align-items:center;gap:10px;'
    'margin:4px 0;opacity:.65">'
    '<div style="flex:1;border-top:1px solid var(--border-color-primary)"></div>'
    '<span style="font-size:12px">or</span>'
    '<div style="flex:1;border-top:1px solid var(--border-color-primary)"></div></div>'
)

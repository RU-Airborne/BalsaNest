from __future__ import annotations

from base64 import b64encode
from pathlib import Path

import gradio as gr

FORCE_DARK_JS = """
() => {
  const url = new URL(window.location);
  if (url.searchParams.get('__theme') !== 'dark') {
    url.searchParams.set('__theme', 'dark');
    window.location.href = url.href;
  }
}
"""

def logo_header_html() -> str:
    """Header block: nest logo beside the BalsaNest title."""
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
    github = (
        '<a class="gh-link" href="https://github.com/RU-Airborne/BalsaNest" '
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
        "</svg><span>Open GitHub project</span></a>"
    )
    return (
        '<div style="display:flex;align-items:center;gap:18px">'
        f"{img}"
        '<div><h1 style="margin:0 0 4px;line-height:1.1;font-size:44px">BalsaNest</h1>'
        '<p style="margin:0">All dimensions are in <b>inches</b>.</p></div>'
        f"{github}"
        "</div>"
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
.logo-glow img {
  filter: drop-shadow(0 0 6px rgba(190, 46, 53, 0.85))
          drop-shadow(0 0 18px rgba(190, 46, 53, 0.5))
          drop-shadow(0 0 42px rgba(190, 46, 53, 0.28));
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
        "<b>parallel</b> &mdash; the part's grain runs with the sheet grain (strongest)",
    )
    + _legend_entry(
        _wood_svg(112, 58, inner=_PART_V),
        "<b>perpendicular</b> &mdash; the part sits across the sheet grain",
    )
    + _legend_entry(
        _wood_svg(112, 58, inner=_PART_R),
        "<b>free</b> &mdash; grain does not matter, the part may only be turned "
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
        "<b>raster</b> &mdash; solid filled letters",
    )
    + _legend_entry(
        _wood_svg(
            150, 44, grain=False,
            inner='<text x="75" y="27" text-anchor="middle" font-size="15" '
            'font-weight="bold" font-family="sans-serif" fill="none" '
            'stroke="#1d4ed8" stroke-width="0.8">RU_Airborne</text>',
        ),
        "<b>outline</b> &mdash; traced letter outlines",
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
        "<b>on</b> &nbsp; the beam runs half a kerf outside the line, so the "
        "part matches the drawing exactly",
    )
    + "</div>"
)

OR_DIVIDER_HTML = (
    '<div style="display:flex;align-items:center;gap:10px;'
    'margin:4px 0;opacity:.65">'
    '<div style="flex:1;border-top:1px solid var(--border-color-primary)"></div>'
    '<span style="font-size:12px">or</span>'
    '<div style="flex:1;border-top:1px solid var(--border-color-primary)"></div></div>'
)

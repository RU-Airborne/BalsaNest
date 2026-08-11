from __future__ import annotations

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

CSS = """
.gradio-container { max-width: 1440px !important; margin: 0 auto !important; }
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
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
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
    '<div style="display:flex;gap:22px;margin:4px 0 2px;flex-wrap:wrap;justify-content:center">'
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
    '<div style="display:flex;gap:18px;margin:2px 0 6px;flex-wrap:wrap;justify-content:center">'
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
        "<b>free</b> &mdash; grain does not matter; the part may only be turned "
        "in right-angle (90&#176;) steps",
    )
    + "</div>"
)

LABEL_STYLE_HTML = (
    '<div style="display:flex;gap:22px;margin:2px 0 4px;flex-wrap:wrap;justify-content:center">'
    + _legend_entry(
        _wood_svg(
            150, 44, grain=False,
            inner='<text x="75" y="27" text-anchor="middle" font-size="15" '
            'font-weight="bold" font-family="sans-serif" fill="#1a1a1a">RU_Airborne</text>',
        ),
        "<b>raster</b> &mdash; solid filled letters (bold but slow to engrave)",
    )
    + _legend_entry(
        _wood_svg(
            150, 44, grain=False,
            inner='<text x="75" y="27" text-anchor="middle" font-size="15" '
            'font-weight="bold" font-family="sans-serif" fill="none" '
            'stroke="#1d4ed8" stroke-width="0.8">RU_Airborne</text>',
        ),
        "<b>outline</b> &mdash; traced letter outlines (much faster)",
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

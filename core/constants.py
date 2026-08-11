"""Physical constants, SVG namespaces and unit tables."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

PX_PER_INCH = 96.0
EPS = 1e-8

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd"

ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)
ET.register_namespace("sodipodi", SODIPODI_NS)

# Average glyph advance as a fraction of the font size.
LABEL_CHAR_WIDTH_RATIO = 0.62

LENGTH_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z%]*)\s*$"
)

UNIT_TO_INCH = {
    "in": 1.0,
    "mm": 1.0 / 25.4,
    "cm": 1.0 / 2.54,
    "pt": 1.0 / 72.0,
    "pc": 1.0 / 6.0,
    "px": 1.0 / PX_PER_INCH,
    "": 1.0 / PX_PER_INCH,  # unitless SVG lengths are CSS px
}

from __future__ import annotations

import os
import platform
from functools import lru_cache
from pathlib import Path
from typing import Optional

_FONT_EXTS = {".ttf", ".otf", ".ttc"}

# Preferred face per family: a regular weight, not bold or italic.
_REGULAR_STYLES = {"regular", "normal", "book", "roman", "plain"}


def _font_dirs() -> list[Path]:
    system = platform.system()
    if system == "Darwin":
        return [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
        ]
    if system == "Windows":
        return [
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts",
        ]
    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path.home() / ".local" / "share" / "fonts",
    ]


@lru_cache(maxsize=1)
def system_font_families() -> dict[str, str]:
    """Installed font families mapped to a font file, preferring the regular
    face. The nesting app runs on the user's own machine, so these are the
    same fonts their laser software and Inkscape can render."""
    from PIL import ImageFont

    families: dict[str, str] = {}
    regular: set[str] = set()
    for d in _font_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in _FONT_EXTS or f.name.startswith("."):
                continue
            try:
                fam, style = ImageFont.truetype(str(f), 24).getname()
            except Exception:
                continue
            if not fam or fam.startswith("."):
                continue
            is_regular = style.lower() in _REGULAR_STYLES
            if fam not in families or (is_regular and fam not in regular):
                families[fam] = str(f)
                if is_regular:
                    regular.add(fam)
    return families


def font_choices() -> list[str]:
    """Dropdown choices: the always-safe generic first, then every installed
    family."""
    return ["sans-serif"] + sorted(system_font_families())


_RATIO_SAMPLE = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "abcdefghijklmnopqrstuvwxyz" "0123456789_- "
)


@lru_cache(maxsize=256)
def label_width_ratio(family: str) -> Optional[float]:
    """Measured average character advance of the family's regular face, as a
    multiple of the font size, with a small safety margin so wide names still
    fit. None when the family is unknown (generic families, missing fonts)."""
    path = system_font_families().get(family)
    if not path:
        return None
    from PIL import ImageFont

    size = 128
    try:
        font = ImageFont.truetype(path, size)
        width = font.getlength(_RATIO_SAMPLE)
    except Exception:
        return None
    if width <= 0:
        return None
    return (width / len(_RATIO_SAMPLE)) / size * 1.10

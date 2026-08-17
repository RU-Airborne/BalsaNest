"""BalsaNest web UI launcher.

    python webui.py            # http://127.0.0.1:7860
    python webui.py --port 8080 --host 0.0.0.0

All UI code lives in the :mod:`web` package; this file only parses
arguments and starts the server.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import gradio as gr

from web import (  # noqa: F401  (re-exported for scripts/tests)
    build_ui,
    run_nest,
    set_outline_from_drawing,
    set_outline_from_file,
    sync_parts,
)
from web.assets import CSS, FORCE_DARK_HEAD, FORCE_DARK_JS, app_theme


def main() -> None:
    parser = argparse.ArgumentParser(description="BalsaNest web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true", help="Create a public Gradio share link")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser")
    args = parser.parse_args()

    demo = build_ui()
    # Previews (part thumbnails, nested-sheet images, the drawing grid) are
    # written to the temp dir and served by URL instead of being embedded in
    # streamed updates, so those paths must be servable.
    tmp = tempfile.gettempdir()
    favicon = Path(__file__).with_name("balsanest.ico")
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=not args.no_browser,
        favicon_path=str(favicon) if favicon.is_file() else None,
        allowed_paths=[tmp, os.path.realpath(tmp)],
        theme=app_theme(),
        css=CSS,
        head=FORCE_DARK_HEAD,
        js=FORCE_DARK_JS,
    )


if __name__ == "__main__":
    main()

"""Static export of the rendering gallery: one self-contained HTML file.

Mirrors `lofbench.suites`'s own `_main`/argparse-in-the-owning-module
convention (no new script directory). Run via:

    uv run python -m lofsite.export_gallery --out dist/gallery.html

Does **not** go through `layout.page`/`Titled` or the live FastHTML
app/request cycle at all -- it calls `gallery_content()` directly (the bare
node list shared with the live `/gallery` route) and wraps it in a
hand-built minimal document shell. Two reasons, checked directly rather than
assumed:

1. `Titled` emits a top-level `<title>` sibling rather than nesting it
   inside `<head>` when serialised outside FastHTML's own response
   machinery (`to_xml(Titled("t", Div("x")))` puts `<title>` before
   `<main>`) -- building the shell by hand sidesteps that.
2. Site nav (`layout.nav`) is meaningless in an offline single file: the
   other routes don't exist to link to.

The footer's external link (`A("distinction-bench", href="https://...")`)
is kept in `layout.page`-wrapped live pages but is not part of this bare
export shell at all -- there is no footer here, only the gallery content
itself, so there is nothing to check for an accidental external
`<script src>`/`<link href>` beyond the images (already inline data URIs)
and the inline CSS text.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fasthtml.common import Div, to_xml

from lofsite import layout
from lofsite.gallery_data import build_gallery_data
from lofsite.pages.gallery import gallery_content

DEFAULT_OUT = Path("dist/gallery.html")


def build_static_document() -> str:
    """Self-contained HTML: `gallery_content()` serialised, styles inlined,
    no external script/link tags, no relative asset paths -- every image is
    already a base64 data URI from `pipeline.emit`, so no extra work is
    needed there. Pure: same input registries in, same string out, no
    request context or file I/O.
    """
    data = build_gallery_data()
    body = to_xml(Div(*gallery_content(data)))
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>distinction-bench rendering gallery</title>"
        f"<style>{layout.style_css()}</style>"
        "</head><body>"
        "<h1>distinction-bench rendering gallery</h1>"
        "<p>Static export -- see the live /gallery route for the "
        "up-to-date version of this page.</p>"
        f"{body}"
        "</body></html>"
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lofsite.export_gallery",
        description="Export the rendering gallery as one self-contained HTML file.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    document = build_static_document()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(document)
    print(f"Wrote {args.out} ({len(document)} bytes).")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

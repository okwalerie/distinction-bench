"""FastHTML app wiring for lofsite.

Run with `uv run python -m lofsite.app` (see the package README for the full
one-command boot instructions).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fasthtml.core import FastHTML

from lofsite.pages.axioms import axioms_page
from lofsite.pages.charts import charts_page, matrix_page, walkthroughs_page
from lofsite.pages.gallery import gallery_page
from lofsite.pages.sandbox import sandbox_page, sandbox_results

STATIC_DIR = Path(__file__).parent / "static"


def create_app(secret_key: str | None = None) -> FastHTML:
    """Build the app without relying on FastHTML's cwd-backed key file."""
    session_secret = secret_key or os.environ.get("LOFSITE_SECRET_KEY") or secrets.token_urlsafe(32)
    app = FastHTML(secret_key=session_secret)
    app.static_route_exts(static_path=str(STATIC_DIR))

    @app.get("/")
    def index():
        return sandbox_page()

    @app.post("/render")
    def render(form_input: str = ""):
        return sandbox_results(form_input)

    @app.get("/axioms")
    def axioms():
        return axioms_page()

    @app.get("/charts")
    def charts():
        return charts_page()

    @app.get("/matrix")
    def matrix():
        return matrix_page()

    @app.get("/walkthroughs")
    def walkthroughs():
        return walkthroughs_page()

    @app.get("/gallery")
    def gallery():
        return gallery_page()

    return app


app = create_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "5001"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

"""Shared page chrome: nav, footer, and base styling.

Kept deliberately plain -- audience is eval researchers, rigour before
polish (per the binding design-decisions note).
"""

from __future__ import annotations

from fasthtml.common import A, Div, Footer, Nav, Strong, Style, Titled

_NAV_ITEMS = (
    ("/", "Sandbox"),
    ("/axioms", "Axioms"),
    ("/charts", "Charts"),
    ("/matrix", "Matrix"),
    ("/walkthroughs", "Walkthroughs"),
    ("/gallery", "Gallery"),
)

# Raw CSS text, held as a plain string precisely so a caller that needs the
# text itself (not a wrapped FastHTML `Style` node) has a real accessor --
# see `style_css()` below. Grepped: no other module reached into `_STYLE` by
# name before this change, so exposing it is a small, in-scope refactor
# (DB-12 gallery plan's "layout._STYLE privacy" risk note). The static
# gallery export (`lofsite.export_gallery`) does not go through
# `layout.page`/the live app at all, so it needs this text to inline into
# its own hand-built `<style>` tag.
_STYLE_CSS = """
    body {
        font-family: system-ui, sans-serif; max-width: 960px; margin: 0 auto;
        padding: 1rem 1.5rem 3rem; color: #1a1a1a;
    }
    nav {
        display: flex; gap: 1.25rem; padding-bottom: 1rem; margin-bottom: 1.5rem;
        border-bottom: 1px solid #ddd;
    }
    nav a { text-decoration: none; color: #444; font-weight: 600; }
    nav a:hover { color: #000; text-decoration: underline; }
    .panel-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1rem; margin-top: 1.5rem;
    }
    .panel {
        border: 1px solid #ddd; border-radius: 6px; padding: 0.75rem 1rem;
        background: #fafafa;
    }
    .panel h3 {
        margin: 0 0 0.5rem; font-size: 0.85rem; text-transform: uppercase;
        letter-spacing: 0.03em; color: #555;
    }
    .panel .caption { font-size: 0.75rem; color: #888; margin-top: 0.25rem; }
    .panel pre { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 0.95rem; }
    .panel img { max-width: 100%; height: auto; display: block; }
    .error {
        color: #a4171d; background: #fff0f0; border: 1px solid #f2b8b8;
        border-radius: 6px; padding: 0.75rem 1rem; margin-top: 1rem;
    }
    .placeholder {
        border: 1px dashed #bbb; border-radius: 6px; padding: 2rem 1.5rem;
        text-align: center; color: #666; margin-top: 1.5rem;
    }
    form.sandbox-form textarea {
        width: 100%; font-family: ui-monospace, monospace; font-size: 1rem;
        padding: 0.5rem; box-sizing: border-box;
    }
    form.sandbox-form button {
        margin-top: 0.5rem; padding: 0.5rem 1.25rem; font-size: 1rem; cursor: pointer;
    }
    .pilot-banner {
        background: #fff8e1; border: 1px solid #eda100; border-radius: 6px;
        padding: 0.75rem 1rem; margin-bottom: 1.5rem; font-size: 0.9rem;
    }
    .pilot-banner strong { color: #8a5c00; }
    .status-badge {
        display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px;
        font-size: 0.8rem; font-weight: 600; color: #fff;
    }
    .status-badge.correct { background: #0ca30c; }
    .status-badge.incorrect { background: #d03b3b; }
    table.data-table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    table.data-table th, table.data-table td {
        border: 1px solid #e1e0d9; padding: 0.4rem 0.6rem; text-align: left;
        font-size: 0.9rem;
    }
    table.data-table th { background: #f9f9f7; }
    .transcript-block {
        white-space: pre-wrap; word-break: break-word; background: #fafafa;
        border: 1px solid #ddd; border-radius: 6px; padding: 0.75rem 1rem;
        font-family: ui-monospace, monospace; font-size: 0.85rem; max-height: 16rem;
        overflow-y: auto;
    }
    /* Chart color roles -- see the dataviz skill's reference palette.
       Light defaults here; dark steps override via prefers-color-scheme,
       validated against the same palette (blue/red diverging pair,
       single-hue blue sequential ramp). */
    .viz-root {
        --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
        --muted: #898781; --gridline: #e1e0d9; --baseline: #c3c2b7;
        --diverging-pos: #e34948; --diverging-neg: #2a78d6;
        --seq-light: #cde2fb; --seq-dark: #0d366b;
    }
    @media (prefers-color-scheme: dark) {
        .viz-root {
            --surface-1: #1a1a19; --text-primary: #ffffff; --text-secondary: #c3c2b7;
            --muted: #898781; --gridline: #2c2c2a; --baseline: #383835;
            --diverging-pos: #e66767; --diverging-neg: #3987e5;
            --seq-light: #184f95; --seq-dark: #cde2fb;
        }
    }
    .viz-root text { fill: var(--text-primary); }
    .viz-root .muted-text { fill: var(--text-secondary); }
"""

_STYLE = Style(_STYLE_CSS)


def style_css() -> str:
    """The site's inline CSS text, verbatim -- the public accessor
    `export_gallery.py` uses to inline the same styling into its
    hand-built document shell, without reaching into `_STYLE` (a
    FastHTML node, not a string) by a private name.
    """
    return _STYLE_CSS


def nav(active: str) -> Nav:
    links = [
        A(label, href=href, style="text-decoration: underline;" if href == active else None)
        for href, label in _NAV_ITEMS
    ]
    return Nav(*links)


def page(title: str, active: str, *content):
    """Wrap `content` in the shared page chrome and return a full document.

    `Titled` supplies the `<title>` and an `<h1>` for `title` itself, so
    callers should not add their own top-level heading.
    """
    return Titled(
        title,
        _STYLE,
        nav(active),
        *content,
        Footer(
            A("distinction-bench", href="https://github.com/weavermarquez/distinction-bench"),
            " -- representation sensitivity in Laws of Form arithmetic.",
            style="margin-top: 3rem; font-size: 0.85rem; color: #888;",
        ),
    )


def placeholder(message: str):
    """Placeholder shown in place of a DB-5-dependent chart/page."""
    return Div(message, cls="placeholder")


def pilot_banner(suite_version: str):
    """Prominent, honest caveat for every DB-5-backed page.

    All 47 real logs the pipeline can read today are pilot data (pre-DB-4,
    pre-frozen-suite) -- see the pipeline module's own docstring. Per the
    design-decisions note, "suite v1 is a clean break from all previous
    findings. Prior runs become pilot data (v0), cited in the writeup as
    motivation, not compared against." Charts baked from this data must say
    so on the page, not just in a code comment, so nobody mistakes a pilot
    number for a suite v1 score.
    """
    return Div(
        "PILOT DATA (",
        Strong(suite_version),
        "). This is ",
        Strong("not"),
        " the frozen suite v1 -- these numbers are from pre-DB-4 pilot runs, "
        "shown for illustration only. Suite v1 scores are never compared "
        "against pilot data; see the design-decisions note.",
        cls="pilot-banner",
    )

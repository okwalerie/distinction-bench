"""Shared page chrome: nav, footer, and base styling.

Kept deliberately plain -- audience is eval researchers, rigour before
polish (per the binding design-decisions note).
"""

from __future__ import annotations

from fasthtml.common import A, Div, Footer, Nav, Style, Titled

_NAV_ITEMS = (
    ("/", "Sandbox"),
    ("/axioms", "Axioms"),
    ("/charts", "Charts"),
    ("/matrix", "Matrix"),
    ("/walkthroughs", "Walkthroughs"),
)

_STYLE = Style("""
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
""")


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

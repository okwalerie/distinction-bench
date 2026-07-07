"""Rendering showcase gallery (DB-12): every registered dialect, its
variations, a sample of what the generator produces per difficulty tier,
and the headline numbers from the frozen suite v1 -- built only from real
code output (`lofsite.gallery_data`), never a hand-written dialect list.

`gallery_content()` is the bare content node list, shared by the live route
(`gallery_page()`, wrapped in `layout.page`) and the static exporter
(`lofsite.export_gallery`, which does not go through `layout.page` at all --
see that module's docstring for why).
"""

from __future__ import annotations

from fasthtml.common import H2, H3, Div, Img, P, Pre, Small, Table, Td, Th, Tr

from lofsite import layout
from lofsite.gallery_data import GalleryData, build_gallery_data


def _panel(dialect_id: str, kind: str, content: str, caption: str, *, accent: bool = False):
    style = "border-left: 3px solid var(--seq-dark);" if accent else None
    body = Img(src=content, alt=f"{dialect_id} rendering") if kind == "image" else Pre(content)
    children = [H3(dialect_id), body]
    if caption:
        children.append(Small(caption, cls="caption"))
    return Div(*children, cls="panel", style=style)


def _archetype_group(group) -> list:
    """One archetype's row: the canonical panel (accent colour, per the
    binding design constraint "one accent colour per section applied to the
    focal panel") plus its variation or fallback panel(s)."""
    canonical = group.canonical
    panels = [
        _panel(
            canonical.dialect_id, canonical.kind, canonical.content, canonical.caption, accent=True
        )
    ]
    for v in group.variations:
        panels.append(_panel(v.dialect_id, v.kind, v.content, v.caption))
    out: list = [
        Small(f"archetype: {group.archetype}", cls="caption"),
        Div(*panels, cls="panel-grid"),
    ]
    if group.note:
        out.append(P(group.note, cls="caption"))
    return out


def _family_sections(families) -> list:
    # A family heading per distinct family, with an "archetype: <key>" direct
    # label on every archetype row underneath it -- shown even for a
    # single-archetype family (Tufte: direct labels throughout, no implicit
    # reliance on "the only one here"). This is what keeps a two-archetype
    # family (parens: parens@1 / word_brackets@1; trees: trees@1 /
    # tree_indent@1; biopolymer: rna_arc@1 / rna_dotbracket@1) from reading
    # as one conflated block -- see the gallery plan's readability risk note.
    sections: list = []
    current_family = None
    for group in families:
        if group.family != current_family:
            current_family = group.family
            sections.append(H3(current_family))
        sections.extend(_archetype_group(group))
    return sections


def _legacy_section(legacy, circle_note: str) -> list:
    out: list = [
        H2("Legacy renderers"),
        P("Bare renderers with no registered DialectSpec / injector arm."),
    ]
    for entry in legacy:
        panel = entry.panel
        row = [_panel(panel.dialect_id, panel.kind, panel.content, panel.caption)]
        if entry.seed_pair:
            seed_a, seed_b = entry.seed_pair
            row.append(
                _panel(
                    f"{entry.dialect_id} (seed pair)", seed_a.kind, seed_a.content, seed_a.caption
                )
            )
            row.append(
                _panel(
                    f"{entry.dialect_id} (seed pair)", seed_b.kind, seed_b.content, seed_b.caption
                )
            )
        out.append(Div(*row, cls="panel-grid"))
    if circle_note:
        out.append(P(circle_note, cls="caption"))
    return out


def _generator_section(samples) -> list:
    rows = [
        Tr(
            Td(s.tier),
            Td(Pre(s.form_string)),
            Td(str(s.depth)),
            Td(str(s.steps)),
        )
        for s in samples
    ]
    return [
        H2("The generator, sampled per tier"),
        P(
            "One form generated at each difficulty tier's own depth, width and "
            "mark limits, with a fixed seed distinct from the frozen suite's "
            "own generation seed -- this is a separate, smaller sample, not a "
            "claim about suite v1's actual forms."
        ),
        Table(
            Tr(Th("Tier"), Th("Form"), Th("Depth"), Th("Steps")),
            *rows,
            cls="data-table",
        ),
    ]


def _suite_section(summary) -> list:
    rows = []
    for c in summary.sample_cells:
        badge_cls = "correct" if c.structure_verified else "incorrect"
        badge_text = "verified" if c.structure_verified else "not verified"
        rows.append(
            Tr(
                Td(c.form_id),
                Td(c.dialect_id),
                Td(c.family),
                Td(c.modality),
                Td(c.format),
                Td(Small(badge_text, cls=f"status-badge {badge_cls}")),
                Td(Pre(c.payload_hash_prefix + "...")),
            )
        )
    return [
        H2("Suite v1: headline and a sampled slice"),
        P(
            f"Suite {summary.suite_version}: {summary.n_forms} forms, "
            f"{summary.n_dialects} dialects, {summary.n_cells} cells -- read "
            "live from the checked-in suite file, never hardcoded."
        ),
        P("A deterministic sample of 12 cells, evenly spaced by sorted (form_id, dialect_id):"),
        Table(
            Tr(
                Th("form_id"),
                Th("dialect_id"),
                Th("family"),
                Th("modality"),
                Th("format"),
                Th("structure_verified"),
                Th("payload_hash (truncated)"),
            ),
            *rows,
            cls="data-table",
        ),
    ]


def gallery_content(data: GalleryData | None = None) -> list:
    """The bare content node list: every family/archetype panel, the legacy
    section, the generator sample, and the suite v1 summary. Shared,
    unmodified, by the live `/gallery` route and the static exporter.
    """
    if data is None:
        data = build_gallery_data()
    content: list = [
        H2("Per-family renderings"),
        P(
            "Every registered dialect, grouped by family and archetype. The "
            "canonical (injector-free) panel is shown with an accent border; "
            "its variation arms sit beside it on the same exemplar form, "
            "labelled with their own injector settings. An archetype with no "
            "registered injector variation shows a deeper exemplar instead of "
            "a second copy of the same image, captioned honestly."
        ),
        Div(*_family_sections(data.families), cls="viz-root"),
        Div(*_legacy_section(data.legacy, data.circle_note), cls="viz-root"),
        Div(*_generator_section(data.generator_samples), cls="viz-root"),
        Div(*_suite_section(data.suite_summary), cls="viz-root"),
    ]
    return content


def gallery_page():
    return layout.page(
        "Rendering gallery",
        "/gallery",
        P(
            "Every renderer the benchmark ships, live: every registered "
            "dialect, its variation arms, a sample of the generator's output "
            "per difficulty tier, and the frozen suite v1's headline numbers. "
            "Rendered fresh on every request from the same registries the "
            "benchmark itself uses -- nothing here is a hand-written example."
        ),
        *gallery_content(),
    )

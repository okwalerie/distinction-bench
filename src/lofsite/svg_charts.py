"""Server-rendered inline SVG for the DB-5-backed charts.

Plain hand-built SVG rather than a client-side charting library or a
matplotlib-rasterised PNG: it stays crisp at any zoom, themes via the CSS
custom properties defined in ``lofsite.layout`` (light/dark, no separate
bake step), and keeps every mark a real DOM node so a ``<title>`` gives a
free, cheap hover tooltip. Colour choices follow the dataviz skill's
reference palette (diverging blue/red pair for signed magnitude, single-hue
blue sequential ramp for 0-1 accuracy) -- see ``lofsite/layout.py``'s
``.viz-root`` custom properties for the validated hex values in each mode.

These functions build markup strings embedded via ``fasthtml.common.NotStr``
inside a ``Div(cls="viz-root")`` -- the class is what resolves the
``--diverging-pos`` etc. custom properties at render time.
"""

from __future__ import annotations

from lofsite.charts_data import MatrixData, SensitivityRow

# --- shared geometry --------------------------------------------------------

_ROW_HEIGHT = 28
_BAR_HEIGHT = 14
_LABEL_WIDTH = 300
_PVAL_WIDTH = 110
_PLOT_WIDTH = 340
_MARGIN = 12


def _fmt_p(p: float) -> str:
    if p < 0.001:
        return "p<0.001"
    return f"p={p:.3f}"


def _fmt_drop(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.1f}pp"


def _whisker_cap(cx: float, cy: float, half: float = 4.0) -> str:
    return (
        f'<line x1="{cx:.1f}" y1="{cy - half:.1f}" x2="{cx:.1f}" y2="{cy + half:.1f}" '
        f'stroke="var(--muted)" stroke-width="1.5"/>'
    )


def sensitivity_svg(rows: list[SensitivityRow]) -> str:
    """Headline chart: paired accuracy drop per (model, reasoning_setting,
    dialect_id) contrast against the canonical dialect, with a bootstrap CI
    whisker and the exact McNemar p-value.

    Positive ``paired_drop`` means the canonical dialect scored higher (the
    treatment dialect hurt accuracy); negative means the treatment dialect
    scored higher. That sign, not an arbitrary "good/bad", is what the
    diverging red/blue colour encodes -- direction, not judgement.
    """
    if not rows:
        return ""

    max_abs = max(
        (max(abs(r.ci_low), abs(r.ci_high), abs(r.paired_drop)) for r in rows), default=0.05
    )
    domain = max(max_abs * 1.15, 0.05)

    width = _MARGIN * 2 + _LABEL_WIDTH + _PLOT_WIDTH + _PVAL_WIDTH
    height = _MARGIN * 2 + _ROW_HEIGHT * len(rows) + 30  # +30 for the axis labels row

    plot_x0 = _MARGIN + _LABEL_WIDTH
    plot_cx = plot_x0 + _PLOT_WIDTH / 2

    def x(value: float) -> float:
        return plot_x0 + (value + domain) / (2 * domain) * _PLOT_WIDTH

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" aria-label="Per-model sensitivity: paired accuracy drop vs canonical dialect">'
    ]
    # Zero gridline.
    top = _MARGIN
    bottom = _MARGIN + _ROW_HEIGHT * len(rows)
    parts.append(
        f'<line x1="{plot_cx:.1f}" y1="{top}" x2="{plot_cx:.1f}" y2="{bottom}" '
        f'stroke="var(--baseline)" stroke-width="1.5"/>'
    )

    for i, row in enumerate(rows):
        y = _MARGIN + i * _ROW_HEIGHT
        cy = y + _ROW_HEIGHT / 2
        bar_x0 = x(min(0.0, row.paired_drop))
        bar_x1 = x(max(0.0, row.paired_drop))
        color = "var(--diverging-pos)" if row.paired_drop >= 0 else "var(--diverging-neg)"
        title = (
            f"{row.label}: paired drop {_fmt_drop(row.paired_drop)}, "
            f"95% CI [{_fmt_drop(row.ci_low)}, {_fmt_drop(row.ci_high)}], "
            f"{_fmt_p(row.mcnemar_p)}, n={row.n_distinct_forms} distinct forms"
        )
        whisker_x_low, whisker_x_high = x(row.ci_low), x(row.ci_high)
        parts.append(
            f"<g><title>{_escape(title)}</title>"
            f'<text x="{_MARGIN}" y="{cy + 4:.1f}" font-size="11" class="muted-text">'
            f"{_escape(row.label)}</text>"
            f'<line x1="{whisker_x_low:.1f}" y1="{cy:.1f}" x2="{whisker_x_high:.1f}" '
            f'y2="{cy:.1f}" stroke="var(--muted)" stroke-width="1.5"/>'
            f"{_whisker_cap(whisker_x_low, cy)}"
            f"{_whisker_cap(whisker_x_high, cy)}"
            f'<rect x="{bar_x0:.1f}" y="{y + (_ROW_HEIGHT - _BAR_HEIGHT) / 2:.1f}" '
            f'width="{max(1.5, bar_x1 - bar_x0):.1f}" height="{_BAR_HEIGHT}" '
            f'rx="2" fill="{color}"/>'
            f'<text x="{plot_x0 + _PLOT_WIDTH + 10}" y="{cy + 4:.1f}" font-size="11" '
            f'class="muted-text">{_escape(_fmt_p(row.mcnemar_p))} (n={row.n_distinct_forms})</text>'
            f"</g>"
        )

    axis_y = bottom + 16
    parts.append(
        f'<text x="{_MARGIN}" y="{axis_y}" font-size="10" class="muted-text">'
        f"&#8592; treatment easier (canonical scores lower)</text>"
    )
    parts.append(
        f'<text x="{width - _MARGIN}" y="{axis_y}" font-size="10" text-anchor="end" '
        f'class="muted-text">canonical easier (treatment hurts) &#8594;</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


# --- matrix heatmap ----------------------------------------------------------

_CELL_W = 100
_CELL_H = 48
_ROW_LABEL_W = 260


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _interp_hex(c0: str, c1: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    r0, g0, b0 = _hex_to_rgb(c0)
    r1, g1, b1 = _hex_to_rgb(c1)
    r = round(r0 + (r1 - r0) * t)
    g = round(g0 + (g1 - g0) * t)
    b = round(b0 + (b1 - b0) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# Sequential single-hue blue ramp endpoints (see the dataviz skill's palette
# reference: step 100 -> step 700). Interpolated per-channel for a smooth
# heatmap fill rather than snapping to the eight named steps -- an
# approximation acceptable for a magnitude cue with the value re-stated as a
# direct text label in every cell (never colour-alone).
_SEQ_LIGHT = "#cde2fb"
_SEQ_DARK = "#0d366b"


def accuracy_matrix_svg(matrix: MatrixData) -> str:
    """Model x dialect_id accuracy heatmap, direct-labelled per cell."""
    if not matrix.row_labels or not matrix.col_labels:
        return ""

    width = _ROW_LABEL_W + _CELL_W * len(matrix.col_labels) + _MARGIN * 2
    header_h = 32
    legend_h = 34
    height = header_h + _CELL_H * len(matrix.row_labels) + legend_h + _MARGIN * 2

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="Accuracy by model and dialect">'
    ]

    for j, col in enumerate(matrix.col_labels):
        cx = _MARGIN + _ROW_LABEL_W + j * _CELL_W + _CELL_W / 2
        parts.append(
            f'<text x="{cx:.1f}" y="{_MARGIN + 20}" font-size="12" font-weight="600" '
            f'text-anchor="middle">{_escape(col)}</text>'
        )

    for i, row_label in enumerate(matrix.row_labels):
        y0 = _MARGIN + header_h + i * _CELL_H
        parts.append(
            f'<text x="{_MARGIN}" y="{y0 + _CELL_H / 2 + 4:.1f}" font-size="12">'
            f"{_escape(row_label)}</text>"
        )
        for j, col in enumerate(matrix.col_labels):
            x0 = _MARGIN + _ROW_LABEL_W + j * _CELL_W
            cell = matrix.cells.get((row_label, col))
            if cell is None:
                parts.append(
                    f'<rect x="{x0}" y="{y0}" width="{_CELL_W - 2}" height="{_CELL_H - 2}" '
                    f'fill="none" stroke="var(--gridline)" stroke-dasharray="3,3"/>'
                    f'<text x="{x0 + _CELL_W / 2:.1f}" y="{y0 + _CELL_H / 2 + 4:.1f}" '
                    f'font-size="11" text-anchor="middle" class="muted-text">not run</text>'
                )
                continue
            fill = _interp_hex(_SEQ_LIGHT, _SEQ_DARK, cell.accuracy)
            text_color = "#ffffff" if cell.accuracy > 0.55 else "#0b0b0b"
            title = f"{row_label} x {col}: {cell.accuracy * 100:.1f}% (n={cell.n})"
            parts.append(
                f"<g><title>{_escape(title)}</title>"
                f'<rect x="{x0}" y="{y0}" width="{_CELL_W - 2}" height="{_CELL_H - 2}" '
                f'fill="{fill}"/>'
                f'<text x="{x0 + _CELL_W / 2:.1f}" y="{y0 + _CELL_H / 2:.1f}" '
                f'font-size="13" font-weight="600" text-anchor="middle" fill="{text_color}">'
                f"{cell.accuracy * 100:.0f}%</text>"
                f'<text x="{x0 + _CELL_W / 2:.1f}" y="{y0 + _CELL_H / 2 + 15:.1f}" '
                f'font-size="9" text-anchor="middle" fill="{text_color}">n={cell.n}</text>'
                f"</g>"
            )

    # Legend: a small gradient bar with 0/50/100% ticks.
    legend_y = _MARGIN + header_h + _CELL_H * len(matrix.row_labels) + 20
    legend_x0 = _MARGIN + _ROW_LABEL_W
    legend_w = 200
    grad_id = "lofsite-matrix-seq"
    parts.append(
        f'<defs><linearGradient id="{grad_id}" x1="0" x2="1" y1="0" y2="0">'
        f'<stop offset="0%" stop-color="{_SEQ_LIGHT}"/>'
        f'<stop offset="100%" stop-color="{_SEQ_DARK}"/>'
        f"</linearGradient></defs>"
    )
    parts.append(
        f'<rect x="{legend_x0}" y="{legend_y}" width="{legend_w}" height="10" '
        f'fill="url(#{grad_id})"/>'
    )
    for frac, label in ((0.0, "0%"), (0.5, "50%"), (1.0, "100%")):
        lx = legend_x0 + frac * legend_w
        parts.append(
            f'<text x="{lx:.1f}" y="{legend_y + 24}" font-size="10" '
            f'text-anchor="{"start" if frac == 0 else "end" if frac == 1 else "middle"}" '
            f'class="muted-text">{label}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

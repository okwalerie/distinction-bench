"""Shared exact-``Fraction`` weighted subdivision, used by blocks, map,
map-centred and rooms. One partition routine, four families -- avoids four
near-duplicate implementations of the same laminar-partition idea.

A weighted partition of an interval (or, applied per-axis, a rectangle) is
a *laminar family*: any two resulting sub-intervals are either disjoint or
one nests inside the other, never partially overlapping. That property is
exactly what makes ``interval_subset``/``rect_subset`` over these
sub-regions agree with tree containment for every pair, not just direct
parent-child pairs -- see each archetype module's docstring for the
per-family argument.
"""

from __future__ import annotations

from fractions import Fraction

from ._geometry import Interval, Rect


def partition_interval(lo: Fraction, hi: Fraction, weights: list[int]) -> list[Interval]:
    """Exact ``Fraction`` partition of ``[lo, hi)`` into ``len(weights)``
    touching (no-gap) sub-intervals, sized proportional to ``weights``."""
    total = sum(weights)
    bounds = [lo]
    acc = Fraction(0)
    span = hi - lo
    for w in weights:
        acc += Fraction(w, total)
        bounds.append(lo + span * acc)
    return list(zip(bounds, bounds[1:]))


def shrink_interval(interval: Interval, gap: Fraction) -> Interval:
    """Shrink ``interval`` inward by ``gap`` (a fraction of its own width)
    on both ends -- a purely cosmetic separation that stays an exact
    ``Fraction`` subset of the original."""
    lo, hi = interval
    pad = (hi - lo) * gap
    return lo + pad, hi - pad


def shrink_rect(rect: Rect, gap: Fraction) -> Rect:
    """:func:`shrink_interval` applied independently on both axes."""
    x0, y0, x1, y1 = rect
    nx0, nx1 = shrink_interval((x0, x1), gap)
    ny0, ny1 = shrink_interval((y0, y1), gap)
    return nx0, ny0, nx1, ny1


def subdivide_rect(rect: Rect, weights: list[int], *, split_axis: str) -> list[Rect]:
    """Partition ``rect`` into ``len(weights)`` weighted sub-rects along
    ``split_axis`` (``"x"`` or ``"y"``); the other axis keeps the full
    parent span. Slice-and-dice, not squarified -- alternating
    ``split_axis`` by depth parity at the call site is what makes
    successive levels read as a floor plan rather than a flat strip."""
    x0, y0, x1, y1 = rect
    if split_axis == "x":
        return [(lo, y0, hi, y1) for lo, hi in partition_interval(x0, x1, weights)]
    if split_axis == "y":
        return [(x0, lo, x1, hi) for lo, hi in partition_interval(y0, y1, weights)]
    raise ValueError(f"split_axis must be 'x' or 'y', got {split_axis!r}")

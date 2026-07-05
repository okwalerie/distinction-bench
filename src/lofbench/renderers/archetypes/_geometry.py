"""Platform-stable geometry helpers shared by DB-3's spatial archetypes.

Two guarantees, deliberately kept separate from float/libm, per the plan's
"Shared infrastructure this task adds" and the architecture doc's rejection
of rounding as a determinism guarantee:

- ``stable_sin``/``stable_cos``/``stable_sqrt``: a from-scratch
  :class:`decimal.Decimal` Taylor series and ``Decimal.sqrt()``, never
  ``math.sin``/``math.cos``/``math.sqrt``. Decimal arithmetic is defined by
  the General Decimal Arithmetic spec and correctly rounded, so it is
  bit-identical across platforms -- it does not depend on libm the way
  ``math``'s transcendentals do. Convert to ``float`` only at the final
  coordinate, after every comparison that matters for a containment
  predicate has already been made in ``Decimal``/``Fraction``.
- ``rect_subset``/``interval_subset``/``interval_subset_3d``: exact
  ``Fraction`` subset tests. No epsilon is needed pre-emission -- epsilon,
  if it appears at all, is a rendering margin, never a verification fudge.

This module lives under ``renderers/archetypes/``, not the shared pipeline
core, to avoid a merge conflict with DB-4's parallel M3-M5 work. DB-4 may
later absorb it into the pipeline package (e.g. for the ``enclosure@1``
migration, which needs the same ``stable_sin``/``stable_cos``).

See ``.lattice/notes/rendering-architecture-2026-07-04.md`` and
``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

# 100-digit Decimal pi. Precision far exceeds the module's 28-digit default
# Decimal context, so this constant is never the rounding bottleneck.
PI = Decimal(
    "3.14159265358979323846264338327950288419716939937510582097494459230781640628620899862803482534211706798"
)
TWO_PI = PI * 2

# Fixed term count -- deterministic cost, no convergence loop.
_TAYLOR_TERMS = 15


def _reduce_angle(theta: Decimal) -> Decimal:
    """Range-reduce ``theta`` to ``(-pi, pi]`` so the Taylor series stays
    accurate without needing more terms."""
    reduced = theta % TWO_PI
    if reduced > PI:
        reduced -= TWO_PI
    return reduced


def stable_sin(theta: Decimal) -> Decimal:
    """``sin(theta)`` via a fixed 15-term Taylor series, range-reduced.
    Decimal throughout -- bit-identical across platforms, unlike
    ``math.sin``."""
    x = _reduce_angle(theta)
    x2 = x * x
    term = x
    total = x
    for k in range(1, _TAYLOR_TERMS):
        denom = Decimal((2 * k) * (2 * k + 1))
        term = -term * x2 / denom
        total += term
    return total


def stable_cos(theta: Decimal) -> Decimal:
    """``cos(theta)``, same discipline as :func:`stable_sin`."""
    x = _reduce_angle(theta)
    x2 = x * x
    term = Decimal(1)
    total = Decimal(1)
    for k in range(1, _TAYLOR_TERMS):
        denom = Decimal((2 * k - 1) * (2 * k))
        term = -term * x2 / denom
        total += term
    return total


def stable_sqrt(value: Decimal) -> Decimal:
    """``Decimal.sqrt()`` -- correctly rounded per the spec, part of the
    determinism guarantee, not an approximation layered on top of it."""
    return value.sqrt()


def turn_to_radians(turn: Fraction) -> Decimal:
    """Exact ``Fraction`` of a full turn -> ``Decimal`` radians. The one
    place an angle crosses from exact rational to Decimal trig."""
    return TWO_PI * Decimal(turn.numerator) / Decimal(turn.denominator)


def polar_to_cartesian(radius: Decimal, turn: Fraction) -> tuple[Decimal, Decimal]:
    """``(radius, turn)`` -> ``(x, y)`` through :func:`stable_cos`/
    :func:`stable_sin`. Shared by every family that places primitives by
    angle (graph, map-centred), per the plan's explicit call to reuse one
    helper rather than reimplementing polar placement per family."""
    theta = turn_to_radians(turn)
    return radius * stable_cos(theta), radius * stable_sin(theta)


Rect = tuple[Fraction, Fraction, Fraction, Fraction]  # (x0, y0, x1, y1), x0<=x1, y0<=y1
Interval = tuple[Fraction, Fraction]  # (lo, hi), lo <= hi


def rect_subset(child: Rect, parent: Rect, margin: Fraction = Fraction(0)) -> bool:
    """Exact rect-in-rect test: ``child`` inside ``parent`` shrunk by
    ``margin`` on every side."""
    px0, py0, px1, py1 = parent
    cx0, cy0, cx1, cy1 = child
    return (
        cx0 >= px0 + margin and cy0 >= py0 + margin and cx1 <= px1 - margin and cy1 <= py1 - margin
    )


def interval_subset(child: Interval, parent: Interval, margin: Fraction = Fraction(0)) -> bool:
    """Exact 1-D interval subset test, ``child`` inside ``parent`` shrunk by
    ``margin`` at both ends."""
    clo, chi = child
    plo, phi = parent
    return clo >= plo + margin and chi <= phi - margin


def interval_subset_3d(
    child: tuple[Interval, Interval, Interval],
    parent: tuple[Interval, Interval, Interval],
    margin: Fraction = Fraction(0),
) -> bool:
    """Three independent 1-D interval-subset tests, one per axis -- the
    architecture doc's explicit call-out for the blocks family."""
    return all(interval_subset(child[axis], parent[axis], margin) for axis in range(3))

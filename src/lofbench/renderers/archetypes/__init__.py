"""Concrete archetype implementations for the composed rendering pipeline.

Importing this package registers every archetype here into
``pipeline.registry.ARCHETYPE_REGISTRY`` and its worked-example named
dialects into ``pipeline.spec.DIALECT_SPECS`` -- a side-effect import,
mirroring ``lofbench.renderers.injectors``. DB-2 and DB-3 add sibling
modules here (one archetype file, one registry line) without touching
anything else in the pipeline.

M5 migration status: ``parens`` (canonical + noisy_parens/bracket_swap) and
``pattern`` (sexpr) land here. ``enclosure`` (svg_circle) does not -- see the
DB-4 M3-M5 lattice comment for what remains.
"""

from __future__ import annotations

from . import parens, pattern  # noqa: F401  (import for registration side effect)

__all__: list[str] = []

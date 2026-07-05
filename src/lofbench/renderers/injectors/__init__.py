"""Concrete injector implementations for the composed rendering pipeline.

Importing this package registers every injector here into
``pipeline.registry.INJECTOR_REGISTRY`` -- a side-effect import, mirroring
``lofbench.renderers.archetypes``.

M5 migration status: ``whitespace_jitter`` (canonical spacing),
``bracket_swap`` (noisy_parens), and ``preset`` (sexpr presets) land here.
Spatial injectors (``boundary_jitter``, ``distractor_marks``) do not -- they
are DB-3 scope once the ``enclosure`` archetype lands.
"""

from __future__ import annotations

from . import bracket_swap, preset, whitespace_jitter  # noqa: F401

__all__: list[str] = []

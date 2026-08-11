"""Concrete archetype implementations for the composed rendering pipeline.

Importing this package registers every archetype here into
``pipeline.registry.ARCHETYPE_REGISTRY`` and its worked-example named
dialects into ``pipeline.spec.DIALECT_SPECS`` -- a side-effect import,
mirroring ``lofbench.renderers.injectors``. ``renderers/__init__.py`` needs
exactly one import line to pull this in. DB-2 adds sibling modules here
(one archetype file, one registry line) without touching anything else in
the pipeline.

Two registration paths landed here, merged from DB-4 and DB-3:

- ``parens``, ``pattern`` and ``enclosure`` (DB-4 M5's migration of
  canonical/noisy_parens/bracket_swap, sexpr, and svg_circle) self-register
  at import time via ``pipeline.spec.register_named_dialect``, which does
  the full job: ``ARCHETYPE_REGISTRY``, ``DIALECT_SPECS``, *and* this
  module's ``_RENDERER_REGISTRY`` (so ``-T renderer=<dialect_id>`` and
  ``list_renderers()`` resolve them, and ``lofsite``'s sandbox fan-out
  renders them).
- ``trees``, ``blocks``, ``graph``, ``map_rect``, ``map_centred``, ``rooms``,
  ``rna_arc``, ``paths_lite`` (DB-3's visual archetype families) were
  registered, until this pass, by the loop below via
  ``_register_named_dialect_when_ready``, which populated
  ``ARCHETYPE_REGISTRY``/``DIALECT_SPECS`` but *deliberately skipped* the
  renderer-registry step, because ``pipeline.emit``'s spatial branch
  raised ``NotImplementedError`` (no pinned rasteriser yet) and every
  zero-arg-constructible renderer is reachable through ``lofsite``'s
  sandbox fan-out.

  DB-4 M5-rest lands the pinned rasteriser (``cairosvg``) and a real
  spatial ``emit()`` path (see ``pipeline.emit``), so that gap is closed:
  each DB-3 module's own ``to_svg(base_render) -> str`` (built for
  standalone viewing/sanity-checking) is wired onto its ``ARCHETYPE``
  instance below as a duck-typed ``to_svg`` attribute -- the same
  convention ``Archetype.text_reader`` already uses -- and
  ``_register_named_dialect_when_ready`` now delegates straight to
  ``register_named_dialect``, so every DB-3 family is reachable via
  ``get_renderer``/``-T renderer=<dialect_id>`` and renders a real PNG data
  URI. This satisfies DB-3's own reachability acceptance criterion,
  deferred at review time pending this exact M5 landing (see the DB-3
  review comment and the DB-4 M5-rest landing comment for the sanctioned
  cross-task line this crosses).

Enclosure (``enclosure@1``) is DB-4's own M5 migration of
``svg_circle_renderer.py`` -- registered below alongside its ``circle``
legacy-key alias (see ``enclosure.py``'s module docstring).
"""

from __future__ import annotations

from ..pipeline.registry import ARCHETYPE_REGISTRY
from ..pipeline.spec import DialectSpec, register_named_dialect
from . import (  # noqa: F401  (import for registration side effect)
    blocks,
    enclosure,
    graph,
    map_centred,
    map_rect,
    parens,
    paths_lite,
    pattern,
    rna_arc,
    rooms,
    trees,
)

__all__: list[str] = []

# (defining module, archetype instance, checked-in dialect id). "map-centred"
# and "rna-arc" keep the architecture doc's/plan's own hyphenated slugs;
# everything else follows the "{family}.canonical-v1" pattern for a plain,
# injector-free dialect. The module is carried alongside the archetype
# instance so its own ``to_svg`` can be wired on below without editing any
# of these DB-3-owned files directly.
_FAMILIES = (
    (trees, trees.ARCHETYPE, "trees.plain-v1"),
    (blocks, blocks.ARCHETYPE, "blocks.plain-v1"),
    (graph, graph.ARCHETYPE, "graph.plain-v1"),
    (map_rect, map_rect.ARCHETYPE, "map.plain-v1"),
    (map_centred, map_centred.ARCHETYPE, "map-centred.plain-v1"),
    (rooms, rooms.ARCHETYPE, "rooms.plain-v1"),
    (rna_arc, rna_arc.ARCHETYPE, "biopolymer.rna-arc-plain-v1"),
    (paths_lite, paths_lite.ARCHETYPE, "paths.arc-nest-plain-v1"),
)


def _register_named_dialect_when_ready(spec: DialectSpec) -> None:
    """DB-4 M5-rest flip: the renderer-registry step is no longer deferred
    -- ``pipeline.emit`` has a real spatial path now, so this delegates
    straight to ``register_named_dialect``. Kept as a thin wrapper (rather
    than inlining the call at each of the eight call sites below) so the
    historical deferral this name documents stays discoverable."""
    register_named_dialect(spec)


for _module, _archetype, _dialect_id in _FAMILIES:
    _registry_key = f"{_archetype.name}@{_archetype.version}"
    ARCHETYPE_REGISTRY[_registry_key] = _archetype
    if hasattr(_module, "to_svg") and not hasattr(_archetype, "to_svg"):
        _archetype.to_svg = _module.to_svg  # type: ignore[attr-defined]
    _register_named_dialect_when_ready(
        DialectSpec(
            dialect_id=_dialect_id,
            family=_archetype.family,
            archetype=_registry_key,
            injectors=[],
        )
    )

# DB-4's own M5 migration: enclosure@1, plus the "circle" legacy-key alias
# that kills the circle/svg_circle registry-key-vs-name asymmetry (the
# static "circle": SVGCircleRenderer entry is removed from
# renderers/__init__.py's _RENDERER_REGISTRY so this registration can claim
# the key). Both dialect ids share the same archetype and no injectors;
# "circle" exists only so the pre-existing registry key keeps resolving,
# now to a renderer whose own .name equals it.
_ENCLOSURE_CANONICAL = DialectSpec(
    dialect_id="enclosure.plain-v1", family="enclosure", archetype="enclosure@1"
)
register_named_dialect(_ENCLOSURE_CANONICAL)
register_named_dialect(
    DialectSpec(dialect_id="circle", family="enclosure", archetype="enclosure@1")
)

"""Concrete archetype implementations for the composed rendering pipeline.

Importing this package registers every archetype here into
``pipeline.registry.ARCHETYPE_REGISTRY`` and its worked-example named
dialects into ``pipeline.spec.DIALECT_SPECS`` -- a side-effect import,
mirroring ``lofbench.renderers.injectors``. ``renderers/__init__.py`` needs
exactly one import line to pull this in. DB-2 adds sibling modules here
(one archetype file, one registry line) without touching anything else in
the pipeline.

Two registration paths land here, merged from DB-4 and DB-3:

- ``parens`` and ``pattern`` (DB-4 M5's migration of canonical/noisy_parens/
  bracket_swap and sexpr) self-register at import time via
  ``pipeline.spec.register_named_dialect``, which does the full job:
  ``ARCHETYPE_REGISTRY``, ``DIALECT_SPECS``, *and* this module's
  ``_RENDERER_REGISTRY`` (so ``-T renderer=<dialect_id>`` and
  ``list_renderers()`` resolve them, and ``lofsite``'s sandbox fan-out
  renders them).
- ``trees``, ``blocks``, ``graph``, ``map_rect``, ``map_centred``, ``rooms``,
  ``rna_arc``, ``paths_lite`` (DB-3's visual archetype families) are
  registered by the loop below via ``_register_named_dialect_when_ready``,
  which populates ``ARCHETYPE_REGISTRY``/``DIALECT_SPECS`` but *deliberately
  skips* the renderer-registry step.

  PNG rasterisation through ``pipeline.emit`` is DB-4 M5 scope: every
  spatial archetype here builds a verified symbolic scene and, separately,
  exposes its own ``to_svg(base_render) -> str`` in its module for viewing
  and sanity-checking now -- DB-4 may fold the same primitives into
  ``emit``'s spatial path once the rasteriser is pinned.

  Deliberately deferred: the renderer-registry wiring (``register_renderer``,
  which is what makes ``-T renderer=<dialect_id>`` and ``list_renderers()``
  resolve a dialect). ``pipeline.spec.register_named_dialect`` would do both
  steps in one call, but calling it here surfaces every family through
  ``lofsite.rendering.render_all_dialects`` -- DB-6's sandbox fan-out, which
  calls ``.render()`` on every zero-arg-constructible registered renderer.
  Construction succeeds (``ComposedRenderer.__init__`` only needs the spec),
  but ``.render()`` reaches ``pipeline.emit.emit()``, whose spatial branch
  raises ``NotImplementedError`` by design until M5 lands (see that module's
  own docstring: reaching it with a spatial ``BaseRender`` before M5 "is a
  programming error, not a silent no-op"). Registering the renderer-registry
  entry now would make that "programming error" reachable through entirely
  normal use of an unrelated task's (DB-6's) existing, already-tested
  sandbox -- five of its tests fail this way if the renderer-registry step
  below is uncommented today. Fixing that needs either DB-4's M5 spatial
  ``emit()`` or a DB-6 guard in ``render_all_dialects``, and both files are
  outside this task's boundary. Flagged as a new lattice task (see the DB-3
  review comment) rather than silently worked around by editing either file.

  ``ARCHETYPE_REGISTRY`` and ``DIALECT_SPECS`` are unaffected by this
  deferral -- they are plain dicts ``lofsite`` never iterates, so populating
  them is always safe, and it is what lets every test in this package's
  ``tests/archetypes/`` verify predicates and containment today. Re-enabling
  the renderer-registry step is a one-line change once the gap above is
  resolved (see ``_register_named_dialect_when_ready`` below).

Enclosure (``enclosure@1``) is not registered here -- it is DB-4's M5
migration of ``svg_circle_renderer.py``, per the plan's disposition.
"""

from __future__ import annotations

from ..pipeline.registry import ARCHETYPE_REGISTRY
from ..pipeline.spec import DIALECT_SPECS, DialectSpec
from . import (  # noqa: F401  (import for registration side effect)
    blocks,
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

# (archetype instance, checked-in dialect id). "map-centred" and "rna-arc"
# keep the architecture doc's/plan's own hyphenated slugs; everything else
# follows the "{family}.canonical-v1" pattern for a plain, injector-free
# dialect.
_FAMILIES = (
    (trees.ARCHETYPE, "trees.canonical-v1"),
    (blocks.ARCHETYPE, "blocks.canonical-v1"),
    (graph.ARCHETYPE, "graph.canonical-v1"),
    (map_rect.ARCHETYPE, "map.canonical-v1"),
    (map_centred.ARCHETYPE, "map-centred.canonical-v1"),
    (rooms.ARCHETYPE, "rooms.canonical-v1"),
    (rna_arc.ARCHETYPE, "rna-arc-v1"),  # architecture doc worked example 4's own slug
    (paths_lite.ARCHETYPE, "paths.arc-nest-v1"),  # plan section 8's own slug
)


def _register_named_dialect_when_ready(spec: DialectSpec) -> None:
    """``pipeline.spec.register_named_dialect`` without its
    ``register_renderer`` step -- see the module docstring for why. Once
    M5 or a ``lofsite`` guard closes the gap, replace calls to this with
    ``register_named_dialect(spec)`` directly (single-line change, no
    other code here needs to move)."""
    DIALECT_SPECS[spec.dialect_id] = spec


for _archetype, _dialect_id in _FAMILIES:
    _registry_key = f"{_archetype.name}@{_archetype.version}"
    ARCHETYPE_REGISTRY[_registry_key] = _archetype
    _register_named_dialect_when_ready(
        DialectSpec(
            dialect_id=_dialect_id,
            family=_archetype.family,
            archetype=_registry_key,
            injectors=[],
        )
    )

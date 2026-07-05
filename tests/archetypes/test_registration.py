"""Every DB-3 archetype is registered: an ``ARCHETYPE_REGISTRY`` entry and
a ``DIALECT_SPECS`` entry, matching the plan's acceptance criterion.

The renderer-registry step (what makes ``-T renderer=<dialect_id>`` and
``list_renderers()`` resolve a dialect, matching the ``circle`` precedent)
is deliberately deferred -- see ``lofbench.renderers.archetypes``'s module
docstring: registering it today makes every family reachable through
``lofsite``'s eager render-all-dialects sandbox, which calls ``.render()``
on every zero-arg-constructible renderer and has no guard for DB-4 M5's
not-yet-landed spatial ``emit()``, so five ``tests/test_lofsite.py`` tests
fail. Both files that would close that gap (``pipeline/emit.py``,
``lofsite/rendering.py``) are outside this task's boundary, so the gap is
flagged as a new lattice task rather than silently edited around.

Enclosure is out of scope (DB-4's M5 migration), so it is not asserted
here.
"""

from __future__ import annotations

import pytest

from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY
from lofbench.renderers.pipeline.spec import DIALECT_SPECS

EXPECTED_ARCHETYPE_KEYS = {
    "trees@1",
    "blocks@1",
    "graph@1",
    "map@1",
    "map_centred@1",
    "rooms@1",
    "rna_arc@1",
    "paths_lite@1",
}

EXPECTED_DIALECT_IDS = {
    "trees.canonical-v1",
    "blocks.canonical-v1",
    "graph.canonical-v1",
    "map.canonical-v1",
    "map-centred.canonical-v1",
    "rooms.canonical-v1",
    "rna-arc-v1",
    "paths.arc-nest-v1",
}


class TestArchetypeRegistry:
    @pytest.mark.parametrize("key", sorted(EXPECTED_ARCHETYPE_KEYS))
    def test_archetype_registered(self, key):
        assert key in ARCHETYPE_REGISTRY


class TestDialectSpecs:
    @pytest.mark.parametrize("dialect_id", sorted(EXPECTED_DIALECT_IDS))
    def test_dialect_spec_registered(self, dialect_id):
        assert dialect_id in DIALECT_SPECS
        spec = DIALECT_SPECS[dialect_id]
        assert spec.archetype in ARCHETYPE_REGISTRY
        assert spec.dialect_id == dialect_id

    @pytest.mark.parametrize("dialect_id", sorted(EXPECTED_DIALECT_IDS))
    def test_dialect_id_constructs_a_working_composed_renderer(self, dialect_id):
        # The renderer-registry step is deferred (module docstring), so
        # this constructs directly from the checked-in spec rather than
        # through get_renderer/-T -- exercising the same ComposedRenderer
        # path that a future registration would use.
        from lofbench.renderers import ComposedRenderer

        spec = DIALECT_SPECS[dialect_id]
        renderer = ComposedRenderer(spec)
        assert renderer.name == dialect_id


class TestSpatialEmitNotYetWired:
    """M5 (the pinned rasteriser and emit's spatial path) has not landed;
    calling ``render()`` on a spatial composed dialect must fail loudly,
    not silently. This documents the current, expected boundary, and is
    exactly why the renderer-registry step is deferred above."""

    def test_render_raises_not_implemented_pending_m5(self):
        from lofbench.renderers import ComposedRenderer

        renderer = ComposedRenderer(DIALECT_SPECS["trees.canonical-v1"])
        with pytest.raises(NotImplementedError):
            renderer.render("(())")

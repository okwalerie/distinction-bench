"""Every DB-3 archetype is registered: an ``ARCHETYPE_REGISTRY`` entry and
a ``DIALECT_SPECS`` entry, matching the plan's acceptance criterion.

The renderer-registry step (what makes ``-T renderer=<dialect_id>`` and
``list_renderers()`` resolve a dialect, matching the ``circle`` precedent)
was deliberately deferred pending DB-4 M5-rest's pinned rasteriser and
spatial ``emit()`` path -- see ``lofbench.renderers.archetypes``'s module
docstring for the history. That gap is now closed: M5-rest lands
``cairosvg`` and wires each DB-3 family's own ``to_svg`` into
``pipeline.emit``'s spatial branch, so ``_register_named_dialect_when_ready``
delegates straight to ``register_named_dialect`` and every family below
resolves via ``get_renderer``/``-T renderer=<dialect_id>`` and renders a
real PNG data URI through ``lofsite``'s sandbox fan-out (verified: the
``tests/test_lofsite.py`` fan-out tests pass with all eight families
registered).

Enclosure is out of scope here (DB-4's own M5 migration) -- see
``tests/archetypes/test_enclosure.py``.
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
    "trees.plain-v1",
    "blocks.plain-v1",
    "graph.plain-v1",
    "map.plain-v1",
    "map-centred.plain-v1",
    "rooms.plain-v1",
    "biopolymer.rna-arc-plain-v1",
    "paths.arc-nest-plain-v1",
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
        from lofbench.renderers import ComposedRenderer, get_renderer

        spec = DIALECT_SPECS[dialect_id]
        renderer = ComposedRenderer(spec)
        assert renderer.name == dialect_id
        # The renderer-registry step is active now (M5-rest flip): the same
        # dialect_id also resolves through the normal get_renderer/-T path.
        assert get_renderer(dialect_id).name == dialect_id


class TestSpatialEmitWired:
    """M5-rest lands the pinned rasteriser and emit's spatial path: a
    spatial composed dialect now renders a real PNG data URI instead of
    raising ``NotImplementedError``."""

    @pytest.mark.requires_visual_runtime
    @pytest.mark.parametrize("dialect_id", sorted(EXPECTED_DIALECT_IDS))
    def test_render_produces_png_data_uri(self, dialect_id):
        from lofbench.renderers import ComposedRenderer

        renderer = ComposedRenderer(DIALECT_SPECS[dialect_id])
        result = renderer.render("(()(()))")
        assert result.metadata["format"] == "image"
        assert result.rendered.startswith("data:image/png;base64,")
        assert result.metadata["structure_verified"] is True

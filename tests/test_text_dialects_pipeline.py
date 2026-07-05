"""Phase B tests for DB-2's text dialects: archetype/injector wrapping,
named DialectSpec registration, and pipeline-level verification.

See `.lattice/plans/task_01KWQKYTN19RZN2BFKAAGQCFKA.md` for the phased
plan. Companion to `tests/test_text_dialects_standalone.py` (Phase A's
pure render/parse round-trip and determinism tests, unchanged and still
passing here). This file exercises the Phase B layer added on top: each
dialect wrapped as an `Archetype`, resolvable through `get_renderer` via
its named `DialectSpec`, with a real `Injector` for its family.

A finding this file used to document, now resolved by the DB-4/DB-2B merge
fixes: M1's `verify()` (`lofbench.renderers.pipeline.verify`) folds a
render's payload through `induced_relation`'s `text_reader` when the
archetype provides one, and falls back to the *canonical parens* reader
only when it does not (see `archetype.py`'s `induced_relation` docstring).
`rna_dotbracket`'s structure line happens to be literal parens, so its
`structure_verified` from the real pipeline was already genuinely `True`
via the canonical reader. The other four dialects (`tree_indent`,
`word_brackets`, `prose`, `clause_embedding`) have no literal parens in
their payload at all -- each now wires its own `parse_X` as its
`Archetype.text_reader` (M4's per-stage verification, threaded through by
this merge), so the real pipeline's `structure_verified` is genuinely
`True` for all ten dialects, not just `rna_dotbracket`. This file still
also exercises each dialect's own `parse_X` directly, for round-trip
coverage independent of the pipeline's own `text_reader` plumbing.
"""

from __future__ import annotations

import random

import pytest

from lofbench.core import DIFFICULTY_CONFIGS, generate_form_string, string_to_form
from lofbench.dialects_text.clause_embedding import (
    ClauseEmbeddingArchetype,
    parse_clause_embedding,
)
from lofbench.dialects_text.prose import parse_prose
from lofbench.dialects_text.rna_dotbracket import parse_rna_dotbracket
from lofbench.dialects_text.tree_indent import parse_tree_indent
from lofbench.dialects_text.word_brackets import (
    parse_word_brackets,
    parse_word_brackets_mismatched,
)
from lofbench.renderers import ComposedRenderer, get_renderer
from lofbench.renderers.pipeline.archetype import BaseRender
from lofbench.renderers.pipeline.nodes import form_to_nodes, iter_node_ids
from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY, INJECTOR_REGISTRY
from lofbench.renderers.pipeline.spec import DIALECT_SPECS

WORKED_EXAMPLE = "(()())"

# dialect_id -> (parser, whether the pipeline's own `structure_verified`
# is currently reliable for it -- see module docstring).
DIALECTS = {
    "biopolymer.rna-dotbracket-v1": (parse_rna_dotbracket, True),
    "biopolymer.rna-dotbracket-filler-v1": (parse_rna_dotbracket, True),
    "trees.indent-v1": (parse_tree_indent, True),
    "trees.indent-jitter-v1": (parse_tree_indent, True),
    "parens.word-brackets-v1": (parse_word_brackets, True),
    "parens.word-brackets-mismatched-v1": (parse_word_brackets_mismatched, True),
    "prose.containment-v1": (parse_prose, True),
    "prose.containment-jitter-v1": (parse_prose, True),
    "embedding.center-clause-v1": (parse_clause_embedding, True),
    "embedding.center-clause-jitter-v1": (parse_clause_embedding, True),
}

EDGE_CASE_FORMS = [
    "",
    "()",
    "()()",
    "(())",
    "(()())",
    "((()))",
    "()()()()",
    "(()(())((())))",
    "()" * 9,
    "(" * 9 + ")" * 9,
]


def _generated_forms(seed: int, per_tier: int = 15) -> list[str]:
    forms = []
    rng = random.Random(seed)
    for _, min_d, max_d, max_w, max_m in DIFFICULTY_CONFIGS:
        for _ in range(per_tier):
            forms.append(
                generate_form_string(
                    min_depth=min_d, max_depth=max_d, max_width=max_w, max_marks=max_m, rng=rng
                )
            )
    return forms


GENERATED_FORMS = _generated_forms(20260705)


class TestRegistryResolution:
    """Every named dialect this task adds must resolve via get_renderer."""

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    def test_resolves_via_get_renderer(self, dialect_id):
        renderer = get_renderer(dialect_id)
        assert isinstance(renderer, ComposedRenderer)
        assert renderer.name == dialect_id

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    def test_renders_worked_example_and_round_trips(self, dialect_id):
        parser, _ = DIALECTS[dialect_id]
        renderer = get_renderer(dialect_id)
        result = renderer.render(WORKED_EXAMPLE, random.Random(0))
        assert parser(result.rendered) == string_to_form(WORKED_EXAMPLE)


class TestCanonicalDialectsMatchPhaseA:
    """The four no-jitter archetypes must be byte-identical to the Phase A
    pure render function they wrap (`word_brackets` needs no rng at all;
    its canonical dialect is covered by the round-trip test above)."""

    def test_rna_dotbracket_worked_example(self):
        renderer = get_renderer("biopolymer.rna-dotbracket-v1")
        result = renderer.render(WORKED_EXAMPLE, random.Random(0))
        lines = result.rendered.split("\n")
        assert lines[0].startswith("sequence: ")
        assert lines[1] == "structure: ( ( ) ( ) )"

    def test_tree_indent_worked_example(self):
        renderer = get_renderer("trees.indent-v1")
        result = renderer.render(WORKED_EXAMPLE)
        assert result.rendered == "mark\n    mark\n    mark"

    def test_word_brackets_worked_example(self):
        renderer = get_renderer("parens.word-brackets-v1")
        result = renderer.render(WORKED_EXAMPLE)
        assert result.rendered == "BEGIN BEGIN END BEGIN END END"

    def test_prose_worked_example(self):
        renderer = get_renderer("prose.containment-v1")
        result = renderer.render(WORKED_EXAMPLE)
        assert result.rendered == "a box holding two empty boxes."

    def test_clause_embedding_worked_example(self):
        renderer = get_renderer("embedding.center-clause-v1")
        result = renderer.render(WORKED_EXAMPLE)
        assert result.rendered == (
            "the creature that two creatures namely the creature sleeps "
            "and the creature sleeps watches."
        )


class TestRoundTripEdgeAndGeneratedForms:
    @pytest.mark.parametrize("dialect_id", DIALECTS)
    @pytest.mark.parametrize("form_string", EDGE_CASE_FORMS)
    def test_edge_cases_round_trip(self, dialect_id, form_string):
        parser, _ = DIALECTS[dialect_id]
        renderer = get_renderer(dialect_id)
        result = renderer.render(form_string, random.Random(42))
        assert parser(result.rendered) == string_to_form(form_string)

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    @pytest.mark.parametrize("form_string", GENERATED_FORMS)
    def test_generated_forms_round_trip(self, dialect_id, form_string):
        parser, _ = DIALECTS[dialect_id]
        renderer = get_renderer(dialect_id)
        result = renderer.render(form_string, random.Random(20260705))
        assert parser(result.rendered) == string_to_form(form_string)


class TestDeterminism:
    """Same seed, fresh renderer/rng objects each time, identical output."""

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    def test_same_seed_same_output_fresh_objects(self, dialect_id):
        for form_string in EDGE_CASE_FORMS:
            first = get_renderer(dialect_id).render(form_string, random.Random(1234))
            second = get_renderer(dialect_id).render(form_string, random.Random(1234))
            assert first.rendered == second.rendered
            assert first.metadata == second.metadata

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    def test_different_seed_can_differ_for_jittered_dialects(self, dialect_id):
        # Not a strict requirement for the two no-jitter families that have
        # no randomness at all (tree_indent/word_brackets/prose/clause_embedding
        # canonical specs) -- only asserts *no crash* and continued round-trip
        # validity across a spread of seeds, which the round-trip tests above
        # already check per-seed. This test exists to make sure varying the
        # seed is at least accepted without error for every dialect.
        parser, _ = DIALECTS[dialect_id]
        for seed in range(5):
            result = get_renderer(dialect_id).render(WORKED_EXAMPLE, random.Random(seed))
            assert parser(result.rendered) == string_to_form(WORKED_EXAMPLE)


class TestProvenanceMetadata:
    """Provenance carries the spec identity the M1/M2 schema provides."""

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    def test_metadata_carries_spec_identity(self, dialect_id):
        spec = DIALECT_SPECS[dialect_id]
        result = get_renderer(dialect_id).render(WORKED_EXAMPLE, random.Random(0))
        assert result.metadata["dialect_id"] == spec.dialect_id
        assert result.metadata["family"] == spec.family
        assert result.metadata["modality"] == "text"
        assert result.metadata["format"] == "text"
        assert "input_relation_hash" in result.metadata
        assert "render_relation_hash" in result.metadata
        assert "structure_verified" in result.metadata

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    def test_structure_verified_matches_known_pipeline_state(self, dialect_id):
        # See module docstring: every dialect here now carries its own
        # `text_reader` (rna_dotbracket via the canonical-parens fallback,
        # since its structure line happens to already be literal parens;
        # the other four via their own `parse_X`, wired as
        # `Archetype.text_reader` by the M4 merge fixes), so
        # `structure_verified` is genuinely `True` for all ten dialects
        # through the real pipeline.
        _, pipeline_verify_reliable = DIALECTS[dialect_id]
        result = get_renderer(dialect_id).render(WORKED_EXAMPLE, random.Random(0))
        assert result.metadata["structure_verified"] is pipeline_verify_reliable


class TestNodeMapCoverage:
    """Every real node id from the source form appears exactly once in the
    archetype's (post-injector) node_map -- the architecture note's
    admission rule, generalised from `test_pipeline.py`'s
    `TestNodeMapExactlyOnce` to every dialect this task adds."""

    @pytest.mark.parametrize("dialect_id", DIALECTS)
    @pytest.mark.parametrize("form_string", ["()", "()()", "(())", "(()())", "(()(())((())))"])
    def test_node_map_covers_every_id_exactly_once(self, dialect_id, form_string):
        spec = DIALECT_SPECS[dialect_id]
        archetype = ARCHETYPE_REGISTRY[spec.archetype]
        root = form_to_nodes(form_string)
        base = archetype.build(root, random.Random(7))
        for injector_name, params in spec.injectors:
            base = INJECTOR_REGISTRY[injector_name].apply(base, root, random.Random(7), **params)
        all_ids = set(iter_node_ids(root))
        assert set(base.node_map.keys()) == all_ids


class _BrokenRnaDotbracketArchetype:
    """A deliberately broken archetype fixture: same interface as
    `RnaDotbracketArchetype`, but always emits a structure line for a
    single mark, `()`, regardless of the real input -- corrupting
    structure while leaving the payload superficially well-formed. Used
    only to prove `verify()` actually catches a real structural break
    through the live pipeline, not just at the `parse_X` level.
    """

    name = "broken-rna@test"
    version = "1"
    family = "biopolymer"
    modality = "text"

    def build(self, root, rng):
        return BaseRender(
            modality="text",
            payload="sequence: A\nstructure: ()",
            node_map={},
        )

    def predicate(self, parent, child):
        return False


def _swap_word_bracket_tokens_2_and_3(rendered: str) -> str:
    tokens = rendered.split()
    tokens[2], tokens[3] = tokens[3], tokens[2]
    return " ".join(tokens)


class TestParseBackCatchesCorruption:
    """Parse-back verification must catch a deliberately corrupted render,
    not just pass everything through. Two levels: the real pipeline's
    `verify()` (reliable for `rna_dotbracket`, see module docstring) and
    each dialect's own `parse_X`, which is what actually stands in for
    verification for the other four dialects today.
    """

    def test_broken_archetype_fails_pipeline_verify(self):
        """Under the M4 contract (`composed.py::ComposedRenderer.render`),
        `verify()` failing before any injector runs is not a metadata flag
        the caller inspects on a returned render -- it is a `RuntimeError`
        raised immediately, and no `RenderedForm` is ever returned (see
        `composed.py`'s own docstring: "structure_verified in the stamped
        provenance is always true for a returned render"). This is the
        merge-time reconciliation of DB-2B's original expectation (a
        returned result with `structure_verified is False`) with DB-4
        M3-M5's actual, already-merged contract: the raise IS the catch.

        The relation-hash mismatch this fixture is designed to trigger is
        still checked, just at the lower `verify()` level directly (the
        same call `ComposedRenderer.render` makes internally) rather than
        through a returned result's metadata.
        """
        from lofbench.renderers.pipeline.nodes import containment_relation, relation_hash
        from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY as reg
        from lofbench.renderers.pipeline.spec import DialectSpec
        from lofbench.renderers.pipeline.verify import verify

        broken = _BrokenRnaDotbracketArchetype()
        reg.setdefault(broken.name, broken)
        spec = DialectSpec(
            dialect_id="test.broken-rna-v1", family="biopolymer", archetype=broken.name
        )
        renderer = ComposedRenderer(spec)

        # (()()) has one top-level mark containing two children; the broken
        # archetype always emits a single unrelated mark. Confirm the
        # relation hashes disagree via the same `verify()` call the
        # pipeline makes internally, before asserting the pipeline-level
        # behavior (raise, not a False-flagged return).
        root = form_to_nodes(WORKED_EXAMPLE)
        base = broken.build(root, random.Random(0))
        structure_verified, render_relation_hash = verify(base, root, broken.predicate)
        input_relation_hash = relation_hash(containment_relation(root))
        assert structure_verified is False
        assert input_relation_hash != render_relation_hash

        # M4 contract: `ComposedRenderer.render` raises `RuntimeError`
        # immediately on this failure rather than returning a result with
        # `structure_verified is False`.
        with pytest.raises(RuntimeError, match="failed containment verification"):
            renderer.render(WORKED_EXAMPLE, random.Random(0))

    @pytest.mark.parametrize(
        "dialect_id,parser,corrupt",
        [
            (
                "trees.indent-v1",
                parse_tree_indent,
                # Drop the last line: one of the two leaves under the top
                # mark disappears, changing the containment relation.
                lambda rendered: "\n".join(rendered.split("\n")[:-1]),
            ),
            (
                "parens.word-brackets-v1",
                parse_word_brackets,
                # Swap tokens 2 and 3 ("END BEGIN" -> "BEGIN END"): turns
                # the two-sibling-leaves shape into a depth-3 chain, a
                # different but still token-balanced (so not just
                # incidentally re-collapsing to the same shape) tree.
                _swap_word_bracket_tokens_2_and_3,
            ),
            (
                "prose.containment-v1",
                parse_prose,
                # Change the count word: "two" -> "three" no longer matches
                # the two rendered leaf descriptions that follow it.
                lambda rendered: rendered.replace("two empty boxes", "three empty boxes"),
            ),
            (
                "embedding.center-clause-v1",
                parse_clause_embedding,
                # Change the numeral the same way as the prose case.
                lambda rendered: rendered.replace("two creatures", "three creatures"),
            ),
        ],
    )
    def test_dialect_parser_rejects_corrupted_render(self, dialect_id, parser, corrupt):
        renderer = get_renderer(dialect_id)
        result = renderer.render(WORKED_EXAMPLE, random.Random(0))
        corrupted = corrupt(result.rendered)
        assert corrupted != result.rendered
        expected = string_to_form(WORKED_EXAMPLE)
        # A correct parser rejects the corruption one of two ways: it
        # raises (malformed input), or it parses to a tree that disagrees
        # with the true structure (well-formed-looking but wrong). Either
        # demonstrates the corruption was not silently accepted as
        # correct; only "parses AND matches the true structure" is a
        # genuine failure of parse-back verification.
        try:
            parsed = parser(corrupted)
        except (ValueError, IndexError, StopIteration, KeyError):
            return
        assert parsed != expected


class TestReachableThroughArchetypeDirectly:
    """Sanity: the archetype classes are directly usable outside the
    named-dialect wiring too (the `Archetype` protocol itself), matching
    the `Archetype` runtime-checkable protocol shape used elsewhere in the
    pipeline test suite (see `test_pipeline.py::TestInjectorApplicabilityShape`
    for the analogous `Injector` check).
    """

    def test_clause_embedding_archetype_satisfies_protocol_shape(self):
        from lofbench.renderers.pipeline.archetype import Archetype

        archetype = ClauseEmbeddingArchetype()
        assert isinstance(archetype, Archetype)
        assert archetype.modality == "text"
        assert archetype.family == "embedding"

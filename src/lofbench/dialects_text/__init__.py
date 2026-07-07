"""Text dialects for DB-2 (task_01KWQKYTN19RZN2BFKAAGQCFKA).

Phase A: each module started as a standalone `render_X` / `parse_X` pair,
depending only on `lofbench.core`. Phase B (this package's `__init__.py`)
wraps each pair as a DB-4 `Archetype`, adds its family-specific
`Injector`(s), and registers ten named `DialectSpec` entries (one
canonical + one jittered per family) once DB-4's M1/M2 core abstractions
(`Archetype`, `Injector`, the registries, `DialectSpec`,
`register_named_dialect`) landed. See
`.lattice/plans/task_01KWQKYTN19RZN2BFKAAGQCFKA.md` for the phased plan
and the per-dialect grammar pins that bind each module's render/parse
implementation.

`register_text_dialects()` is idempotent and is the single entry point
`lofbench.renderers` calls (one append-only hook at the tail of
`renderers/__init__.py`) to wire all five dialects into the live
registries. Import-order note: this module imports from
`lofbench.renderers.pipeline.*` submodules, which is safe only once
`lofbench.renderers.pipeline` itself has finished importing -- true by
the time `renderers/__init__.py` reaches its own tail-end hook, since
`ComposedRenderer`/`DialectSpec` are already imported near the top of
that file.
"""

from __future__ import annotations

from lofbench.dialects_text.clause_embedding import (
    ClauseEmbeddingArchetype,
    VocabularyJitterInjector,
)
from lofbench.dialects_text.prose import ProseArchetype, SynonymJitterInjector
from lofbench.dialects_text.rna_dotbracket import RnaDotbracketArchetype, UnpairedFillerInjector
from lofbench.dialects_text.tree_indent import IndentStyleJitterInjector, TreeIndentArchetype
from lofbench.dialects_text.word_brackets import WordBracketsArchetype, WordDelimiterSwapInjector
from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY, INJECTOR_REGISTRY
from lofbench.renderers.pipeline.spec import DIALECT_SPECS, DialectSpec, register_named_dialect

_ARCHETYPES = {
    "rna_dotbracket@1": RnaDotbracketArchetype(),
    "tree_indent@1": TreeIndentArchetype(),
    "word_brackets@1": WordBracketsArchetype(),
    "prose@1": ProseArchetype(),
    "clause_embedding@1": ClauseEmbeddingArchetype(),
}

_INJECTORS = {
    "unpaired_filler": UnpairedFillerInjector(),
    "indent_style_jitter": IndentStyleJitterInjector(),
    "word_delimiter_swap": WordDelimiterSwapInjector(),
    "synonym_jitter": SynonymJitterInjector(),
    "vocabulary_jitter": VocabularyJitterInjector(),
}

# One canonical (no injectors) + one jittered DialectSpec per family, per
# the plan's Phase B file list. `word_brackets@1` is this module's own
# provisional archetype, not the plan's preferred `parens@1` reuse -- see
# the deviation note in `dialects_text/word_brackets.py`.
_DIALECT_SPECS = (
    DialectSpec(
        dialect_id="biopolymer.rna-dotbracket-v1",
        family="biopolymer",
        archetype="rna_dotbracket@1",
    ),
    DialectSpec(
        dialect_id="biopolymer.rna-dotbracket-filler-v1",
        family="biopolymer",
        archetype="rna_dotbracket@1",
        injectors=[("unpaired_filler", {})],
    ),
    DialectSpec(
        dialect_id="trees.indent-v1",
        family="trees",
        archetype="tree_indent@1",
    ),
    DialectSpec(
        dialect_id="trees.indent-jitter-v1",
        family="trees",
        archetype="tree_indent@1",
        injectors=[("indent_style_jitter", {})],
    ),
    DialectSpec(
        dialect_id="parens.word-brackets-v1",
        family="parens",
        archetype="word_brackets@1",
    ),
    DialectSpec(
        dialect_id="parens.word-brackets-mismatched-v1",
        family="parens",
        archetype="word_brackets@1",
        injectors=[("word_delimiter_swap", {"mismatched": True})],
    ),
    DialectSpec(
        dialect_id="prose.containment-v1",
        family="prose",
        archetype="prose@1",
    ),
    DialectSpec(
        dialect_id="prose.containment-jitter-v1",
        family="prose",
        archetype="prose@1",
        injectors=[("synonym_jitter", {})],
    ),
    DialectSpec(
        dialect_id="embedding.center-clause-v1",
        family="embedding",
        archetype="clause_embedding@1",
    ),
    DialectSpec(
        dialect_id="embedding.center-clause-jitter-v1",
        family="embedding",
        archetype="clause_embedding@1",
        injectors=[("vocabulary_jitter", {})],
    ),
)

_registered = False


def register_text_dialects() -> None:
    """Wire all five DB-2 text dialects into the live pipeline registries.

    Idempotent: safe to call more than once in a process (a second call is
    a no-op), since `register_named_dialect` -> `register_renderer` raises
    on a duplicate registry key.
    """
    global _registered
    if _registered:
        return

    for name, archetype in _ARCHETYPES.items():
        ARCHETYPE_REGISTRY.setdefault(name, archetype)
    for name, injector in _INJECTORS.items():
        INJECTOR_REGISTRY.setdefault(name, injector)
    for spec in _DIALECT_SPECS:
        if spec.dialect_id not in DIALECT_SPECS:
            register_named_dialect(spec)

    _registered = True

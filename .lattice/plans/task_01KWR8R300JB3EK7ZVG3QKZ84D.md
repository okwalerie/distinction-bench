# DB-11: Consolidate tests with property-based and parametric testing

## 1. Census (current state, 2026-07-04)

`uv run pytest --collect-only -q` reports **7,444 tests**. `uv run pytest -q --durations=15` (full run) takes **150.9s**, dominated by two single slow tests, not by the grid itself:

| file | tests | notes |
|---|---:|---|
| `tests/test_text_dialects_standalone.py` | 5,070 | Phase A: 5 pure-function dialects (rna_dotbracket, tree_indent, word_brackets, prose, clause_embedding) |
| `tests/test_text_dialects_pipeline.py` | 971 | Phase B: 10 named `DialectSpec` ids through the real archetype/injector pipeline |
| `tests/archetypes/test_enclosure.py` | 181 | `all_test_forms()` grid (70) + 50-seeded legacy-divergence grid (×2) |
| `tests/archetypes/test_nesting.py` | 150 | `all_test_forms()` (70) × 2 archetypes (rna_arc, paths_lite) |
| `tests/archetypes/test_map_centred.py` | 147 | `all_test_forms()` × 2 (verification + wraparound-safety) |
| `tests/archetypes/test_trees.py` | 146 | `all_test_forms()` × 2 (verification + geometric sanity) |
| `tests/archetypes/test_graph.py` | 144 | `all_test_forms()` × 2 (verification + geometric sanity) |
| `tests/archetypes/test_blocks.py` | 77 | `all_test_forms()` × 1 |
| `tests/archetypes/test_map_rect.py` | 76 | `all_test_forms()` × 1 |
| `tests/archetypes/test_rooms.py` | 75 | `all_test_forms()` × 1 |
| `tests/test_pipeline.py` | 49 | hand-example based, small parametrizations (legacy renderer names, fixture shapes) |
| `tests/test_lofsite.py` | 49 | unrelated to this task (site rendering) |
| `tests/archetypes/test_geometry.py` | 37 | fixed enumeration of 11 real angle values — legitimate parametrize, not a grid |
| `tests/archetypes/test_registration.py` | 32 | fixed enumeration of registry keys — legitimate parametrize |
| `tests/test_archetypes.py` | 24 | small, presets/bool params |
| everything else (17 files) | ≤20 each | hand-example based, out of scope |
| **total** | **7,444** | |

Runtime tail (`--durations=15`):

| test | time | why |
|---|---:|---|
| `test_pipeline_real_logs.py::test_full_real_log_run_reconciles` (module-scoped fixture setup) | 58.9s | runs the real pipeline over `logs/` (47 logs). Already `skipif`-gated on `logs/` existing, so it no-ops in CI/fresh clones — but it exists locally in this sandbox and anywhere else `logs/` is present, and nothing currently stops it running by default there. |
| `test_suites.py::TestVerifySuiteRerunGate::test_committed_v1_suite_reruns_clean` | 33.7s | reruns and re-hashes all ~3,480 cells of the committed frozen suite, including every cairosvg-backed spatial family. This is the test the task text names explicitly to mark slow. |
| everything else (7,442 tests) | ~58s combined | i.e. ~8ms/test average — the grids are not individually expensive, there are just a lot of them. |

**Reading**: the parametrization explosion (7,444 tests) and the runtime problem (150s) are two mostly-separate problems with a common villain-adjacent pair. Cutting the grids fixes the count. Getting under a minute also requires moving both slow single tests behind `@pytest.mark.slow` — not just the one the task text names. This is flagged as a plan decision below (Â§6), not silently assumed.

## 2. Shared building block: one `forms()` Hypothesis strategy

Add `tests/_forms_strategy.py` (unprefixed, like `tests/archetypes/_helpers.py`, so pytest does not collect it):

```python
"""Shared Hypothesis strategy for generating Laws of Form forms.

Generates directly in the internal representation (nested lists — see
lofbench.core.form_to_string's own docstring) rather than driving
generate_form_string(rng=...) through a seed, so Hypothesis's shrinker
walks the real structure (fewer marks, shallower nesting) instead of an
opaque integer seed.
"""
from __future__ import annotations

from hypothesis import strategies as st

from lofbench.core import form_to_string

def raw_forms(max_leaves: int = 40):
    """Hypothesis strategy over the internal nested-list form
    representation. `max_leaves` bounds total marks generated (mirrors
    DIFFICULTY_CONFIGS's max_marks range of 15-35)."""
    return st.recursive(
        st.just([]),
        lambda children: st.lists(children, max_size=6),
        max_leaves=max_leaves,
    )

def forms(max_leaves: int = 40):
    """Same shape, rendered to canonical form-string. This is what test
    modules import and pass to @given."""
    return raw_forms(max_leaves).map(form_to_string)
```

`st.recursive(st.just([]), lambda children: st.lists(children, max_size=6), max_leaves=...)` builds exactly the same shape as the codebase's own `form` type: a form is a list of child-forms, each child-form itself a list of its own children — `[[]]` is one mark with no children (`"()"`), `[[], []]` is two adjacent marks (`"()()"`. Shrinking walks toward `[]` (void), which is the correct "simplest" direction for this domain.

Hand-picked forms are not dropped — they move to `@example(...)` decorators stacked on the same property (Hypothesis always runs `@example` cases in addition to generated ones, so they stay pinned and asserted every single run, not just probabilistically sampled in).

### Determinism discipline (new, `tests/conftest.py`)

```python
import os
from hypothesis import settings

settings.register_profile("default", derandomize=True, deadline=None, max_examples=100)
settings.register_profile("thorough", derandomize=True, deadline=None, max_examples=500)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
```

- `derandomize=True` is load-bearing: it pins Hypothesis's internal PRNG to a hash of the test's source, so every machine/CI run explores the *same* sequence of examples — no `.hypothesis/` example database dependency, no flakiness from a different random seed each run. This directly answers the brief's "a flaky property test in a benchmark repo is worse than a big grid" concern.
- `deadline=None` because several properties (pipeline-dialect round trips, spatial archetype builds) run real archetype/injector code and occasionally cairosvg; Hypothesis's default per-example deadline is tuned for pure functions and will spuriously fail on legitimate variance here, not on real regressions.
- `max_examples=100` default, bumped to `settings(max_examples=300)` locally on the two Phase-A/B round-trip properties specifically (they replace grids of 1000 and 750 respectively — 100 default examples is a real coverage cut versus that, 300 keeps the argued-coverage bar honest without ballooning runtime; see Â§3 disposition table for the exact number per property).
- Add `.hypothesis/` to `.gitignore` (created locally even with `derandomize=True`; harmless but shouldn't be committed).

## 3. Per-grid disposition

### `tests/test_text_dialects_standalone.py` (5,070 → ~85)

| grid | count | disposition | replacement property |
|---|---:|---|---|
| `test_round_trip_generated[dialect][form]` (5 dialects × 1,000 generated forms) | 5,000 | **delete** | `@given(form_string=forms())` + `@settings(max_examples=300)`, parametrized only over `dialect` (5 tests): `parse(render(form_string, Random(seed))) == string_to_form(form_string)`. Stack `@example(f)` for each of the 10 `EDGE_CASE_FORMS` so they're still asserted unconditionally. |
| `test_round_trip_edge_cases[dialect][form]` (5×10) | 50 | **keep** | already hand-picked, cheap, high-signal; folded as `@example`s on the property above is optional — plan keeps both for now since they're already passing and cost ~nothing; implementer may fold if it reads cleaner. |
| `test_deterministic_across_generated_forms[dialect]` (5, internally loops 50 forms) | 5 | **replace internals** | same test count (5, parametrized by dialect only), but the internal `GENERATED_FORMS[:50]` loop becomes `@given(form_string=forms())`: same seed ⇒ identical output. |
| `test_deterministic_under_fixed_seed[dialect]` (5, loops 10 edge cases) | 5 | keep as-is | already cheap, hand-picked. |
| `TestWorkedExample`, `TestVoidForm`, `TestProseDisambiguation`, `TestClauseEmbeddingDisambiguation` | ~20 | keep as-is | hand-picked regression pins, not a grid. |

Net: 5,070 → 5 (new property) + 50 + 5 + 5 + ~20 ≈ **85**.

### `tests/test_text_dialects_pipeline.py` (971 → ~230)

| grid | count | disposition | replacement property |
|---|---:|---|---|
| `TestRoundTripEdgeAndGeneratedForms::test_generated_forms_round_trip[dialect_id][form]` (10 × 75) | 750 | **delete** | `@given(form_string=forms())` + `@settings(max_examples=200)`, parametrized over the 10 `dialect_id`s (10 tests): `parser(get_renderer(dialect_id).render(form_string, Random(seed)).rendered) == string_to_form(form_string)`. |
| `TestRoundTripEdgeAndGeneratedForms::test_edge_cases_round_trip[dialect_id][form]` (10×10) | 100 | keep | hand-picked, cheap. |
| `TestNodeMapCoverage::test_node_map_covers_every_id_exactly_once[dialect_id][form]` (10×5) | 50 | **delete**, replaced | `@given(form_string=forms(max_leaves=15))` parametrized over `dialect_id` (10 tests): every real node id from the source form appears exactly once in the post-injector `node_map`. The fixed 5-form list becomes `@example`s. |
| `TestRegistryResolution`, `TestDeterminism`, `TestProvenanceMetadata`, `TestCanonicalDialectsMatchPhaseA`, `TestParseBackCatchesCorruption` (~70 total) | ~70 | keep as-is | these already parametrize over the *fixed, finite* set of 10 registered dialect ids — not a generated-data grid. Enumerating every registered dialect once is the correct tool (parametrize), and is exactly what "keep a small set of hand-picked example tests per dialect for readability" asks for. |

Net: 971 → 10 + 100 + 10 + ~70 ≈ **190**.

### `tests/archetypes/*.py` (~1,077 across 8 files → ~30)

All eight files share `tests/archetypes/_helpers.py`'s `HAND_PICKED_FORMS` (10) + `generated_forms()` (60 via `generate_form_string`, fixed seed) = `all_test_forms()` (70). Replace the shared helper's generated half with the shared `forms()` strategy, keep `HAND_PICKED_FORMS` as `@example`s:

```python
# tests/archetypes/_helpers.py (revised)
from tests._forms_strategy import forms as _forms  # or relative import, see note below

HAND_PICKED_FORMS = [ ... ]  # unchanged, still exported for @example stacking

def property_forms():
    return _forms(max_leaves=25)  # matches generated_forms()'s depth 1-5 / width ≤4 intent
```

(Note for implementer: `tests/archetypes/` is a separate rootdir-relative package from `tests/` for import purposes per its existing `from _helpers import ...` style — confirm whether `tests/_forms_strategy.py` needs a `conftest.py`-added `sys.path` entry or whether a plain `from _forms_strategy import forms` after adding `tests/` to `rootdir` resolves; `tests/archetypes/_helpers.py` already does `from lofbench.core import generate_form_string` so absolute imports work fine — the only open question is whether `tests/_forms_strategy.py` needs to sit next to `_helpers.py` instead, i.e. duplicated/imported from `tests/archetypes/_forms_strategy.py`. Simplest fix if import resolution is awkward: put the shared strategy at `tests/archetypes/_helpers.py` level only, and have `tests/test_text_dialects_standalone.py` / `tests/test_text_dialects_pipeline.py` each import via `sys.path`-relative trick or just duplicate the ~10-line strategy file at `tests/_forms_strategy.py` and `tests/archetypes/_forms_strategy.py` if pytest's rootdir-per-package import mode makes a single shared module inconvenient. Either is fine; duplication of a 10-line strategy is not worth fighting pytest import modes over.)

| file | grid | count | disposition |
|---|---|---:|---|
| `test_enclosure.py` | `TestVerification` (`all_test_forms()`) | 70 | **delete**, replace with `@given(form_string=property_forms())` + `@example` per hand-picked form (1 test: `assert_verifies(ARCHETYPE, form_string)`) |
| `test_enclosure.py` | `TestStructuralEquivalenceVsLegacy` (2× 50 seeded) | 100 | **delete**, replace with 1 property (`@given` over `property_forms()`, `assert_verifies`) + keep 1 hand test asserting the legacy renderer still runs unmodified on a single fixed form (the "not deleted" claim doesn't need 50 repetitions) |
| `test_nesting.py` | `TestVerification` (`all_test_forms()` × 2 archetypes) | 140 | **delete**, replace with `@given` property parametrized over the 2 archetype names (2 tests) |
| `test_map_centred.py` | `TestVerification` + `TestWraparoundSafety` (`all_test_forms()` × 2) | 140 | **delete**, replace with 2 `@given` properties (verification; angular-span-in-unit-circle) |
| `test_trees.py` | `TestVerification` + `TestGeometricSanity` | 140 | **delete**, replace with 2 `@given` properties |
| `test_graph.py` | `TestVerification` + `TestGeometricSanity` | 140 | **delete**, replace with 2 `@given` properties |
| `test_blocks.py` | `TestVerification` | 70 | **delete**, replace with 1 `@given` property |
| `test_map_rect.py` | `TestVerification` | 70 | **delete**, replace with 1 `@given` property |
| `test_rooms.py` | `TestVerification` | 70 | **delete**, replace with 1 `@given` property |

Net grid instances deleted: 70+100+140+140+140+140+70+70+70 = **940**. Replaced by 1+1+2+2+2+2+1+1+1 = **13** property tests. Remaining fixed/hand tests per file (determinism checks, SVG well-formedness, epsilon-predicate unit tests, geometric-sanity unit tests, door-gap tests) are untouched — roughly 11+10+7+6+4+7+6+5 ≈ **56** across the 8 files.

Net: ~1,077 → 13 + 56 ≈ **69**. (`test_geometry.py` (37) and `test_registration.py` (32) untouched — fixed-value enumeration, not a grid — so the archetypes/ directory as a whole goes from ~1,146 to ~138.)

### Everything else — unchanged, except one marker addition

- `tests/test_suites.py::TestVerifySuiteRerunGate::test_committed_v1_suite_reruns_clean` → add `@pytest.mark.slow` (named explicitly by the task).
- `tests/test_pipeline_real_logs.py` (all 3 tests in the module) → add `pytestmark = [pytest.mark.skipif(...), pytest.mark.slow]` (append to the existing `pytestmark`). **This is beyond the task text's literal instruction** (which names only the frozen-suite rerun test) but is necessary to hit "default suite runtime well under a minute" on any machine where `logs/` happens to exist locally — it already no-ops in CI/fresh clones via the existing skipif, so marking it slow only changes behaviour for local runs where `logs/` is present, which is exactly where it currently costs 58.9s for free. Flagged here for reviewer sign-off rather than assumed silently.
- No other file changes. `test_admission.py`, `test_injectors.py`, `test_archetypes.py`, `test_verification.py`, `test_determinism.py`, `test_core.py`, `test_spec.py`, `test_pipeline.py` are already hand-example-based at a healthy size (11-49 tests each) and are explicitly what "keep a small set of hand-picked example tests... for readability" describes — not a target for this task. (Optional stretch, not required for acceptance: `test_admission.py` could gain one property — "for a random archetype/injector pair drawn from the real registries, `validate_applicability` raises iff the injector's `applicability` set doesn't intersect the archetype's identifiers" — but the existing 13 hand tests already cover this behaviour with named, readable scenarios and the registries are small enough (16 archetypes × 8 injectors = 128 pairs) that a property adds shrinking value but not coverage value. Left out of scope; note it here so a future agent doesn't rediscover the option from scratch.)

## 4. Dependency change

Run `uv add --dev hypothesis` (not a hand-edit) so `uv.lock` stays consistent. This repo has two dev-dependency mechanisms already: `[project.optional-dependencies].dev` (pytest, ruff) and `[dependency-groups].dev` (playwright, pytest — PEP 735, the group `uv sync` installs by default). Confirm with `uv add --dev` which one it lands in (expected: `[dependency-groups].dev`, since that's the modern uv-native mechanism) and sanity check with `uv run python -c "import hypothesis"` — currently `ModuleNotFoundError`, confirmed not installed. `hypothesis` never ships in `src/lofbench` — dev-only per the task's justified stdlib exception.

## 5. pytest configuration changes (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: exercises the full frozen suite rerun or the real-log corpus; excluded by default, run explicitly with `pytest -m slow`",
]
addopts = "-m 'not slow'"
```

No `markers` list exists today (confirmed — `grep markers pyproject.toml` empty), so this also silences the "unknown marker" warning `@pytest.mark.slow` would otherwise raise. Document the two invocations in a comment near this block or in a short addition to `CLAUDE.md`'s existing "Development Commands" section (optional, not gating — the constraint is test-code-only, but a one-line doc note costs nothing and prevents the next agent from wondering where the slow tests went):

```
uv run pytest              # fast path, ~hundreds of tests, well under a minute
uv run pytest -m slow      # the frozen-suite rerun + real-log integration, minutes
uv run pytest -o addopts=""  # truly everything, no marker filter
```

## 6. Projected outcome

| | before | after (projected) |
|---|---:|---:|
| collected tests | 7,444 | ~85 + ~190 + ~138 + (~7,444 − 5,070 − 971 − 1,146) ≈ **~590** |
| default-run wall time | 150.9s | the two slow tests (92.6s combined) move behind `-m slow`; remaining ~58s was already spread over 7,442 fast tests at ~8ms each — cutting ~6,850 of them should bring the fast path to **well under 60s** (rough proportional estimate: ~58s × (590/7442) ≈ 4.6s of *old* per-test overhead, plus new Hypothesis property overhead: ~35 properties × ~100-300 examples × sub-ms-to-few-ms per example ≈ single-digit seconds each for the cheap pure-function properties, more for the pipeline/spatial ones that go through cairosvg — implementer must actually time it post-change rather than trust this estimate, see acceptance criteria) |

The "~590" figure is deliberately not "the hundreds" rounded down — it's slightly above the task's "down to the hundreds" framing because ~590 is still technically hundreds (5.9 hundred), consistent with the target, and further cuts would mean deleting the fixed/hand-picked example tests the task explicitly says to keep. If the implementer's actual count lands meaningfully higher (say >1,000) after doing the work, that's a signal a grid was missed — re-check against Â§3 before calling it done.

## 7. Acceptance criteria

1. `uv run pytest --collect-only -q` reports a count in the hundreds (target ≈590, hard ceiling 1,000 — investigate if exceeded).
2. `uv run pytest` (default markers, i.e. `not slow`) completes in well under 60s wall time on this machine — measure with `time uv run pytest`, don't estimate.
3. `uv run pytest -m slow` still passes (frozen-suite rerun + real-log tests, when `logs/` exists) — this is the safety net proving nothing was silently broken by marking things slow, not just moved.
4. Every new `@given` property is demonstrably able to fail: for each property, the implementer temporarily introduces the one-line mutation named below, confirms the property test fails (not just "would fail in theory"), then reverts before committing. Record which mutation was checked for each in the commit message or a PR comment — this is the "coverage argued, not asserted" bar applied to the *new* tests, not just the deleted grids.

   | property | mutation to prove it can fail |
   |---|---|
   | Phase A round-trip (5, one per dialect) | in one dialect's `parse_X`, skip the first token/line (e.g. `tree_indent`: drop the first line before splitting) |
   | Phase A determinism-across-generated (5) | seed the second render call with a *different* `random.Random` instance/seed inside the test temporarily |
   | Phase B round-trip (10, one per dialect_id) | same as Phase A, applied to one of the 10 registered dialects' parser |
   | Phase B node-map coverage (10) | in one archetype's `build`, drop one child node from `node_map` |
   | Archetype containment verification (9: enclosure, nesting×1 covering both sub-archetypes via parametrize, map_centred, trees, graph, blocks, map_rect, rooms) | in one archetype's `predicate`, flip `return True`/`False` (or negate the containment test) so a genuinely-nested pair reads as not-contained |
   | map_centred wraparound-safety property | in the layout code, temporarily allow a span to cross the `[0,1)` seam (e.g. don't mod the angle) |
   | trees/graph geometric-sanity properties | temporarily disable the overlap check inside `geometric_sanity` |

5. `hypothesis` appears only in a dev dependency group (`uv add --dev` output), not in `[project.dependencies]`.
6. `derandomize=True` (or equivalent pinned-seed mechanism) is in force for the default profile — two consecutive `uv run pytest` invocations produce identical Hypothesis example sequences (no reliance on a committed `.hypothesis/` database; add `.hypothesis/` to `.gitignore`).
7. No source file under `src/` changes (test-code-only refactor) — except the one-line mutations in acceptance criterion 4, which must be reverted before the final commit.
8. `pytest.ini_options.markers` registers `slow` (no more "unknown marker" warnings), and `addopts` excludes it by default.

## 8. Risks

- **Import path for the shared `forms()` strategy across `tests/` and `tests/archetypes/`**: these two directories appear to be separate pytest "rootdir" packages today (archetype tests do `from _helpers import ...`, a same-directory relative-style import, not `from tests.archetypes._helpers import ...`). A single shared strategy module may not import cleanly across that boundary without a `conftest.py`/`sys.path` change. Fallback: duplicate the ~10-line strategy file rather than fight pytest's import mode — flagged in Â§3, not a blocker.
- **`derandomize=True` + cairosvg-backed spatial archetypes**: Hypothesis's chosen examples for the spatial-archetype properties (enclosure, trees, graph, map_centred, map_rect, rooms, blocks) are pinned by source hash, but if `max_leaves` is too generous the shrinker or the initial example set could occasionally generate deep/wide forms that make a single property test slow (cairosvg cost is real, per the frozen-suite-rerun evidence: ~3,480 cells taking 33.7s ≈ 10ms/cell). Budget: keep `max_leaves` for spatial-archetype properties at ~20-25 (matching `_helpers.generated_forms()`'s existing depth 1-5/width≤4 intent) rather than the pure-function properties' 40, and treat criterion 2 (well under 60s, measured) as the actual gate — if it's not met, tighten `max_examples`/`max_leaves` on the spatial properties specifically before touching anything else.
- **Coverage regression risk on the deleted grids' *volume***: Phase A's deleted grid ran 1,000 distinct forms/dialect; the replacement property at `max_examples=300` runs fewer raw executions per test invocation. This is deliberately accepted per the task's own framing ("Hypothesis generates and shrinks counterexamples, which finds edge cases a fixed sample cannot" — the claim is behavioural coverage via targeted search + shrinking, not raw volume). If review disagrees this trade is worth it for a specific property, `max_examples` is a one-line dial, not a redesign.
- **`test_pipeline_real_logs.py` slow-marking is an unrequested scope addition**: named explicitly in Â§3 as a plan decision, not slipped in silently. If the reviewer wants it left alone (task text only named the frozen-suite test), reverting is a one-line removal of the `pytest.mark.slow` addition — everything else in this plan is independent of that decision.
- **`@example` + `@given` on the same form list across dialects**: for the Phase A/B round-trip properties, `@example` is stacked per-form but the property itself is parametrized over `dialect`/`dialect_id`, so each `@example` fires once per dialect (fine, matches current per-dialect behaviour) — just noting so the implementer doesn't try to hoist `@example` outside the parametrize and accidentally only pin it for one dialect.

marker: plan-wave-4-20260704

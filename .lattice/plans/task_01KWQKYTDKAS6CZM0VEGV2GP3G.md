# DB-1: Refresh CLAUDE.md and README to match actual lofbench architecture

CLAUDE.md still describes a dead architecture (form.py, simplify.py, generate.py, dataset.py, cli.py, 'bench generate' CLI) that no longer exists. Actual code: src/lofbench/ with core.py (form repr + simplify + generate + DIFFICULTY_CONFIGS), renderers/ (registry: canonical, noisy_parens, circle, nested_list, sexpr), datasets/factory.py (inspect-ai Sample factories, handles text + image dialects), tasks/ (single.py, composite.py, prompts.py), scorers/lof_scorer.py, analysis.py (inspect log loading + pandas). Rewrite the project-overview/architecture/commands sections of CLAUDE.md to match reality; keep the Lattice section intact. README.org is mostly accurate but verify examples against current __init__.py exports. Why: agents reading stale docs hallucinate the old layout.

## What is actually true (verified this session)

- No `bench` CLI exists anywhere. No `cli.py`, no `[project.scripts]` entry in `pyproject.toml`. Confirmed absent.
- `form.py`, `simplify.py`, `generate.py`, `dataset.py` do not exist. The real package is `src/lofbench/`:
  - `core.py` — form repr (`form_to_string`, `string_to_form`), simplification (`simplify_string`, `canonical_string`, `evaluate`), generation (`generate_form_string`, `generate_test_cases`, `generate_composite_test_cases`), `DIFFICULTY_CONFIGS` (5 tiers: easy/medium/hard/lunatic/extra).
  - `renderers/` — a plain-dict registry (`_RENDERER_REGISTRY` in `renderers/__init__.py`) mapping name -> `FormRenderer` subclass: `canonical`, `noisy_parens`, `circle`, `nested_list`, `sexpr`. Access via `get_renderer(name, **kwargs)` / `list_renderers()`. `FormRenderer` (base.py) is an ABC with `.name` and `.render(form_string, rng)`; renderer instances carry their own `Config` dataclasses (`CanonicalConfig`, `NoisyParensConfig`, `SVGCircleConfig`, `NestedListConfig`, `SExprConfig`).
  - `datasets/factory.py` (via `datasets/__init__.py`) — `create_single_dataset`, `create_composite_dataset`: build inspect-ai `Sample` objects, calling the renderer per item. Handles both text and image dialects.
  - `tasks/` — `single.py` (`single_lof_task`, params `n`, `seed`, `renderer`, `render_seed`, `renderer_config`), `composite.py` (`composite_lof_task`, params `n_groups`, `group_size` default **4** not 8, `seed`, `renderer`, `render_seed`, `renderer_config`; uses `Epochs(3, "at_least_2")`), `prompts.py` (system/user templates; system prompt keeps parens-flavoured axiom examples per the design-decisions note).
  - `scorers/lof_scorer.py` — `lof_single_scorer`, `lof_composite_scorer`.
  - `analysis.py` — loads inspect eval logs (`load_curated_logs`, log parsing helpers) into pandas.
- `src/lofbench/__init__.py` exports (version `0.2.0`): `DIFFICULTY_CONFIGS`, `form_to_string`, `string_to_form`, `form_depth`, `string_depth`, `simplify_string`, `canonical_string`, `evaluate`, `generate_form_string`, `generate_test_cases`, `generate_composite_test_cases`, `single_lof_task`, `composite_lof_task`, `FormRenderer`, `RenderedForm`, `get_renderer`, `list_renderers`.
- Verified commands (this session, in repo root):
  - `uv run pytest --collect-only -q` → 56 tests collected, no errors. Test files: `tests/test_core.py`, `tests/test_renderers.py`, `tests/test_scorers.py` (not `test_form.py` as CLAUDE.md's example currently claims).
  - `uv run ruff check .` → runs cleanly as a command (exit non-zero, reports 15 pre-existing lint findings in `analysis.py`, unrelated to this task — do not fix here, out of scope).
  - `uv run inspect eval src/lofbench/tasks/single.py --model anthropic/claude-sonnet-4-20250514 -T n=1 --limit 1` → parses correctly, gets past dataset/task construction, fails only on missing `ANTHROPIC_API_KEY`. This confirms the command syntax is valid and reaches model-init, which is as far as it can be checked without a live key or model spend.
- `agents.md` is byte-for-byte identical to CLAUDE.md's current "## Lattice" section onward (verified with `diff`). It needs **no changes** — it's already in sync, and the Lattice section is staying byte-for-byte anyway.
- `README.org` usage example (`simplify_string`, `canonical_string`, `generate_form_string`) matches current `__init__.py` exports exactly — accurate, no change needed there.
- `README.org` Task Parameters table has one stale value: `group_size` default listed as **8**, but `composite_lof_task`'s actual default is **4** (`src/lofbench/tasks/composite.py` line ~19). This must be fixed as part of "every command listed must actually run / every fact listed must be true" — it's a factual staleness in scope for this task even though it's a table cell, not a command.
- Renderer list in README's table only mentions `canonical, noisy_parens` — incomplete but not false (it's not claiming those are the only two; still, worth broadening to name all five registered renderers so agents know `circle`, `nested_list`, `sexpr` exist).

## Scope boundary (explicit, per orchestrator instruction)

`.lattice/notes/rendering-architecture-2026-07-04.md` describes a `ComposedRenderer` / archetype-injector rewrite of the renderer layer, and says DB-1 must stamp `suite_version`, `dialect_id`, `family`, `form_id` into `Task.metadata` / sample metadata (`single.py`), and rewrite `analysis.py`'s `get_log_metadata`. **That is a code change, not a docs change.** It belongs to whatever implementation ticket carries out the rendering-architecture migration (referred to there as "DB-1" in a different, code-authoring sense, and DB-4's migration per the orchestrator's framing) — not to *this* DB-1, which is scoped by its actual Lattice description to refreshing CLAUDE.md/README/agents.md prose. This plan does not touch `single.py`, `composite.py`, `factory.py`, `analysis.py`, or the renderer registry. It only links the design note from CLAUDE.md so implementers find it.

## Plan: CLAUDE.md changes

Keep the file's overall shape (Overview → Development Commands → Architecture → Constraints → Lattice). Replace only the stale sections; the "## Lattice" section (current lines 70–265) stays byte-for-byte untouched — do not even reflow whitespace in it.

1. **`## Project Overview`** — keep the Laws of Form / I1 / I2 explanation (still correct: `simplify_string`, `evaluate` still implement exactly this). Add one sentence noting the benchmark now runs as inspect-ai tasks over multiple rendering "dialects" (renderers), not just canonical parens, and link forward to the architecture section.

2. **`## Development Commands`** — replace the whole block with what actually runs:
   ```bash
   # Environment setup
   uv sync                    # Install dependencies
   uv run pytest              # Run tests
   uv run pytest -x -v        # Run tests, stop on first failure, verbose
   uv run pytest tests/test_core.py::test_name  # Run single test

   # Linting
   uv run ruff check .        # Lint
   uv run ruff format .       # Format

   # Evaluation (inspect-ai)
   uv run inspect eval src/lofbench/tasks/single.py --model <provider/model> -T n=10
   uv run inspect eval src/lofbench/tasks/composite.py --model <provider/model> -T n_groups=20
   uv run inspect eval src/lofbench/tasks/single.py --model <provider/model> -T renderer=noisy_parens
   uv run inspect view         # Browse eval logs

   # Pre-commit
   pre-commit install         # Install hooks
   pre-commit run --all-files # Run manually
   ```
   Drop the old `bench generate` / `bench validate` lines entirely — no such CLI exists. Note that a live run needs a provider API key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / etc.) set in the environment; without one the command fails at model init, not at task construction — this is expected and not a bug in the docs.

3. **`## Architecture`** — replace `### Core Library (src/)` and its bullet list with a `src/lofbench/` map:
   - `core.py` — form representation, parsing, simplification, generation, `DIFFICULTY_CONFIGS`.
   - `renderers/` — dialect renderer registry (`canonical`, `noisy_parens`, `circle`, `nested_list`, `sexpr`); `get_renderer`/`list_renderers`/`register_renderer` in `renderers/__init__.py`; `FormRenderer` ABC in `renderers/base.py`.
   - `datasets/factory.py` — builds inspect-ai `Sample`s for text and image dialects (`create_single_dataset`, `create_composite_dataset`).
   - `tasks/` — inspect-ai task definitions: `single.py` (`single_lof_task`), `composite.py` (`composite_lof_task`), `prompts.py` (system/user prompt templates).
   - `scorers/lof_scorer.py` — `lof_single_scorer`, `lof_composite_scorer`.
   - `analysis.py` — loads inspect eval logs into pandas for post-hoc analysis.

   Keep `### Representation` as-is (still accurate: nested lists, void/mark normal forms).

   Replace `### Test Structure (tests/)` bullets with the real file names: `test_core.py` (parsing, simplification, generation), `test_renderers.py` (renderer registry and output correctness), `test_scorers.py` (scoring logic).

   Add a new subsection, `### Rendering Architecture (design, not yet built)`, one short paragraph: a dialect-renderer rewrite (archetype + injector two-layer model, containment-relation verification, content-addressed seeding) is designed but not yet implemented; the design is binding for the DB-1/DB-2/DB-3 rendering work. Link both design notes by relative path so implementers find them without a Lattice lookup:
   - `.lattice/notes/design-decisions-2026-07-04.md` — the interview decisions binding all DB tasks (what the benchmark measures, dialect families, task/prompt design, measurement design, budget).
   - `.lattice/notes/rendering-architecture-2026-07-04.md` — the binding architecture design for the renderer rewrite (`ComposedRenderer`, `Archetype`/`Injector`, `DialectSpec`, seed threading, provenance schema).

   State explicitly in this new subsection: the current registry (`canonical`, `noisy_parens`, `circle`, `nested_list`, `sexpr`) is what exists in code today; the archetype/injector design is what the linked notes specify as the target, not yet implemented.

4. **`## Constraints`** — keep as-is; every bullet (Python 3.11+, `uv`, stdlib preference, type hints, no model API calls in core library, notebooks under `notebooks/`) still holds. Check whether `notebooks/` exists; if it doesn't, either drop that bullet or leave it as forward guidance (recommend leaving it — it's a constraint on future work, not a claim about present state, so it isn't "false").

5. **`## Lattice`** — no changes. Byte-for-byte identical before and after.

## Plan: README.org changes

Two small factual fixes, nothing structural (the file is otherwise accurate):

1. In the Task Parameters table, change the `group_size` row's default from `8` to `4` to match `composite_lof_task`'s actual signature default.
2. In the `renderer` row, broaden the description from "Renderer name (canonical, noisy_parens)" to name all five registered renderers: "Renderer name (canonical, noisy_parens, circle, nested_list, sexpr)".

No other README changes. The install/usage/running-evaluations code blocks already match current exports and CLI syntax; do not touch them beyond the two table cells above.

## Plan: agents.md

No changes. Verified byte-for-byte identical to CLAUDE.md's current Lattice section (`diff` confirms). Since the Lattice section is not changing, agents.md needs nothing.

## Acceptance criteria

- No reference to `form.py`, `simplify.py`, `generate.py`, `dataset.py`, `cli.py`, `bench generate`, or `bench validate` remains anywhere in CLAUDE.md, README.org, or agents.md.
- Every command listed in CLAUDE.md's Development Commands section runs (exit code aside — `uv run ruff check .` is allowed to report pre-existing lint findings; it must not fail to invoke). Verify with:
  - `uv run pytest --collect-only -q | tail -3`
  - `uv run ruff check . 2>&1 | tail -3`
  - `uv run inspect eval src/lofbench/tasks/single.py --model anthropic/claude-sonnet-4-20250514 -T n=1 --limit 1` (expect it to fail only on missing API key, not on argument/task construction)
- Both design notes (`design-decisions-2026-07-04.md`, `rendering-architecture-2026-07-04.md`) are linked from CLAUDE.md's architecture section by relative path.
- The `## Lattice` section of CLAUDE.md is byte-for-byte identical to its current content (diff against current file to confirm).
- README.org's `group_size` default and renderer list are corrected.
- agents.md is untouched.
- A lazy implementation that just deletes the stale bullets without adding the real architecture map, without linking the design notes, or without fixing the README table would fail this review.

marker: plan-wave-2-20260704

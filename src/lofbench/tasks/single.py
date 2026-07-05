"""Single expression LoF evaluation task."""

from __future__ import annotations

from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Dataset
from inspect_ai.model import GenerateConfig
from inspect_ai.solver import chain_of_thought, generate, prompt_template, system_message

from lofbench.core import DIFFICULTY_CONFIGS
from lofbench.datasets import create_single_dataset
from lofbench.renderers import get_renderer
from lofbench.scorers import lof_single_scorer
from lofbench.tasks.prompts import SINGLE_SYSTEM_PROMPT, SINGLE_USER_TEMPLATE


def _stamp_dialect_provenance(dataset: Dataset, fallback_dialect: str) -> dict[str, Any]:
    """DB-4 M8: stamp ``form_id`` into every sample's ``render_metadata``, and
    provide ``suite_version``/``dialect_id``/``family`` fallbacks for legacy
    (non-composed) renderers, whose ``render_metadata`` carries none of the
    ``render_provenance`` schema at all (see
    ``.lattice/notes/rendering-architecture-2026-07-04.md``'s "Provenance
    metadata schema").

    ``form_id`` is the one field a composed dialect can never supply on its
    own: ``ComposedRenderer.render`` only ever sees a bare form string, never
    an assigned id (``renderers/pipeline/provenance.py``'s docstring is
    explicit about this gap). DB-5's data pipeline is already coded to read
    it from here (``render_meta.get("form_id")``, see ``pipeline.py``'s
    ``resolve_provenance``), falling back to a content hash of the form
    string only when it is missing -- so stamping the real suite-table id
    here is a strict improvement, not a new field nobody reads.

    ``suite_version``/``dialect_id``/``family`` fallbacks are written ONLY to
    the flat top-level ``sample.metadata`` keys, never into the nested
    ``render_metadata`` dict itself: ``pipeline.py``'s own
    ``resolve_provenance`` already derives a smarter renderer-name-keyed
    family/modality/format fallback (``_RENDERER_FALLBACK``) when
    ``render_metadata`` lacks these keys, and writing a cruder guess
    (``family = renderer name``) directly into ``render_metadata`` would
    shadow that better fallback for every log generated after this change.

    Returns the resolved ``{"suite_version", "dialect_id", "family"}`` for
    the dataset (from the first sample, since one renderer instance renders
    every sample in a task), for use in ``Task.metadata``. Defaults to
    ``{"suite_version": "adhoc", "dialect_id": fallback_dialect, "family":
    fallback_dialect}`` for an empty dataset.
    """
    resolved = {
        "suite_version": "adhoc",
        "dialect_id": fallback_dialect,
        "family": fallback_dialect,
    }
    for i, sample in enumerate(dataset.samples):
        rm = sample.metadata.get("render_metadata")
        if not isinstance(rm, dict):
            rm = {}
            sample.metadata["render_metadata"] = rm
        rm.setdefault("form_id", sample.id)

        suite_version = rm.get("suite_version", "adhoc")
        dialect_id = rm.get("dialect_id", fallback_dialect)
        family = rm.get("family", fallback_dialect)

        sample.metadata["suite_version"] = suite_version
        sample.metadata["dialect_id"] = dialect_id
        sample.metadata["family"] = family
        sample.metadata["form_id"] = rm["form_id"]

        if i == 0:
            resolved = {
                "suite_version": suite_version,
                "dialect_id": dialect_id,
                "family": family,
            }
    return resolved


@task
def single_lof_task(
    n: int = 100,
    seed: int = 2025,
    renderer: str = "canonical",
    render_seed: int | None = None,
    renderer_config: dict | None = None,
) -> Task:
    """Single LoF expression evaluation task.

    Evaluates model ability to reduce individual Laws of Form expressions
    to their canonical form (marked or unmarked).

    Args:
        n: Number of test cases
        seed: Seed for test case generation
        renderer: Renderer name (canonical, noisy_parens, etc.)
        render_seed: Seed for reproducible rendering
        renderer_config: Dict of renderer options (e.g., {"mismatched": true})

    Returns:
        inspect-ai Task instance
    """
    renderer_instance = get_renderer(renderer, **(renderer_config or {}))

    dataset = create_single_dataset(
        n=n,
        seed=seed,
        renderer=renderer_instance,
        render_seed=render_seed,
    )

    dialect_provenance = _stamp_dialect_provenance(dataset, fallback_dialect=renderer)

    # Calculate difficulty distribution for metadata
    base_per_diff = n // len(DIFFICULTY_CONFIGS)
    remainder = n % len(DIFFICULTY_CONFIGS)
    difficulty_counts = {
        config[0]: base_per_diff + (1 if i < remainder else 0)
        for i, config in enumerate(DIFFICULTY_CONFIGS)
    }

    # Model-specific reasoning flags should be passed via CLI:
    #   Claude Opus 4.5: --reasoning-tokens 10000
    #   GPT-5.2: --reasoning-effort high
    #   Gemini 3.0: --reasoning-tokens 10000
    return Task(
        dataset=dataset,
        solver=[
            system_message(SINGLE_SYSTEM_PROMPT),
            chain_of_thought(),
            prompt_template(SINGLE_USER_TEMPLATE),
            generate(),
        ],
        scorer=lof_single_scorer(),
        config=GenerateConfig(
            temperature=1,
            max_tokens=64000,  # Opus max; other models will cap to their limits
        ),
        metadata={
            "n": n,
            "seed": seed,
            "renderer": renderer,
            "render_seed": render_seed,
            "renderer_config": renderer_config,
            "suite_version": dialect_provenance["suite_version"],
            "dialect_id": dialect_provenance["dialect_id"],
            "family": dialect_provenance["family"],
            "difficulty_distribution": difficulty_counts,
            "difficulty_configs": {
                config[0]: {
                    "min_depth": config[1],
                    "max_depth": config[2],
                    "max_width": config[3],
                    "max_marks": config[4],
                }
                for config in DIFFICULTY_CONFIGS
            },
        },
    )

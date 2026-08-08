"""Dataset factory functions for inspect-ai integration."""

from __future__ import annotations

import base64
import random
from hashlib import blake2b, sha256
from typing import TYPE_CHECKING

from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessageUser, ContentImage, ContentText

from lofbench.core import generate_composite_test_cases, generate_test_cases
from lofbench.protocols import ProtocolSpec
from lofbench.renderers.pipeline.composed import ComposedRenderer
from lofbench.suites import LoadedSuite

if TYPE_CHECKING:
    from inspect_ai.dataset import Dataset

    from lofbench.renderers import FormRenderer


def _item_rng(render_seed: int | None, key: str) -> random.Random | None:
    """Content-addressed per-item ``random.Random`` (DB-4 M3).

    Both ``create_single_dataset`` and ``create_composite_dataset`` used to
    build one ``random.Random(render_seed)`` and pass it to every ``render``
    call, so subsetting or reordering the case list shifted every subsequent
    draw. This derives a fresh generator per item from a digest of
    ``render_seed`` and ``key`` (the form string), so the same key always
    starts from the same state regardless of its position in the batch, and
    distinct items still draw independently. Returns ``None`` when
    ``render_seed`` is ``None``, matching the prior default-Random behaviour
    exactly.
    """
    if render_seed is None:
        return None
    digest = blake2b(f"{render_seed}\x00{key}".encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest, "big"))


def create_suite_dataset(
    suite: LoadedSuite,
    *,
    form_set: str,
    dialect_id: str,
    protocol: ProtocolSpec,
) -> Dataset:
    """Build exact public samples from frozen suite cells."""
    if form_set not in suite.form_sets:
        raise ValueError(f"unknown form set {form_set!r}")
    if dialect_id not in suite.specs:
        raise ValueError(f"unknown dialect {dialect_id!r}")
    spec = suite.specs[dialect_id]
    by_form = {form["abstract_form_id"]: form for form in suite.forms}
    by_cell = {(cell["abstract_form_id"], cell["dialect_id"]): cell for cell in suite.cells}
    renderer = ComposedRenderer(spec)
    samples: list[Sample] = []
    for form_id in suite.form_sets[form_set]:
        form = by_form[form_id]
        cell = by_cell[(form_id, dialect_id)]
        if cell["modality"] == "text":
            payload = cell["model_payload"]
            payload_bytes = payload.encode("utf-8")
            symbolic_hash = blake2b(payload_bytes, digest_size=16).hexdigest()
        else:
            rendered = renderer.render(form["reference_transcription"])
            payload = rendered.rendered
            payload_bytes = base64.b64decode(payload.split(",", 1)[1])
            symbolic_hash = rendered.metadata["payload_hash"]
        if sha256(payload_bytes).hexdigest() != cell["model_payload_sha256"]:
            raise RuntimeError(f"frozen model payload hash mismatch for {form_id}/{dialect_id}")
        if symbolic_hash != cell["symbolic_payload_hash"]:
            raise RuntimeError(f"frozen symbolic payload hash mismatch for {form_id}/{dialect_id}")

        user_text = protocol.render_user_text(reading_rule=spec.reading_rule)
        if cell["modality"] == "image":
            sample_input = [
                ChatMessageUser(content=[ContentText(text=user_text), ContentImage(image=payload)])
            ]
        else:
            sample_input = f"{user_text}\n\nstimulus:\n{payload}"
        prompt_material = (
            protocol.system_text + "\x00" + user_text + "\x00" + cell["model_payload_sha256"]
        )
        prompt_hash = sha256(prompt_material.encode()).hexdigest()
        target = protocol.target_for(form)
        samples.append(
            Sample(
                id=f"{form_id}:{dialect_id}:{protocol.protocol_id}",
                input=sample_input,
                target=target,
                metadata={
                    "suite_version": suite.suite_version,
                    "abstract_form_id": form_id,
                    "abstract_form": form["abstract_form"],
                    "reference_transcription": form["reference_transcription"],
                    "normal_value": form["normal_value"],
                    "difficulty": form["difficulty"],
                    "dialect_id": dialect_id,
                    "family": spec.family,
                    "archetype": spec.archetype,
                    "modality": spec.modality,
                    "model_format": spec.model_format,
                    "protocol_id": protocol.protocol_id,
                    "prompt_hash": prompt_hash,
                    "symbolic_payload_hash": cell["symbolic_payload_hash"],
                    "model_payload_sha256": cell["model_payload_sha256"],
                },
            )
        )
    return MemoryDataset(
        samples=samples,
        name=f"lof_{suite.suite_version}_{form_set}_{dialect_id}_{protocol.protocol_id}",
    )


def create_single_dataset(
    n: int = 100,
    seed: int = 2025,
    renderer: FormRenderer | None = None,
    render_seed: int | None = None,
) -> Dataset:
    """Create a dataset for single expression evaluation.

    Args:
        n: Number of test cases
        seed: Seed for test case generation
        renderer: Optional renderer for form transformation
        render_seed: Seed for reproducible rendering

    Returns:
        inspect-ai Dataset with Sample objects
    """
    # Import here to avoid circular imports
    from lofbench.renderers import CanonicalRenderer

    renderer = renderer or CanonicalRenderer()
    cases = generate_test_cases(n=n, seed=seed)

    samples = []

    for case in cases:
        rng = _item_rng(render_seed, case["input"])
        rendered = renderer.render(case["input"], rng)

        # Handle image vs text rendering
        is_image = rendered.metadata.get("format") == "image"
        if is_image:
            # For images, use ContentImage in input
            input_content = [
                ChatMessageUser(
                    content=[
                        ContentImage(image=rendered.rendered),
                        ContentText(text="What does this expression evaluate to?"),
                    ]
                )
            ]
        else:
            # For text, use placeholder (template provides real prompt)
            input_content = "Evaluate the expression"

        samples.append(
            Sample(
                id=case["id"],
                input=input_content,
                target=case["target"],  # "marked" or "unmarked"
                metadata={
                    "expression": rendered.rendered if not is_image else "",
                    "difficulty": case["difficulty"],
                    "depth": case["depth"],
                    "steps": case["steps"],
                    "original_form": case["input"],
                    "renderer": rendered.renderer_name,
                    "render_metadata": rendered.metadata,
                },
            )
        )

    return MemoryDataset(samples=samples, name=f"lof_single_{renderer.name}")


def create_composite_dataset(
    n_groups: int = 100,
    group_size: int = 8,
    seed: int = 2025,
    renderer: FormRenderer | None = None,
    render_seed: int | None = None,
) -> Dataset:
    """Create a dataset for composite (multi-expression) evaluation.

    For composite tasks, the input contains multiple expressions and
    the target is the comma-separated list of expected results (marked/unmarked).

    Args:
        n_groups: Number of test groups
        group_size: Expressions per group
        seed: Seed for test case generation
        renderer: Optional renderer for form transformation
        render_seed: Seed for reproducible rendering

    Returns:
        inspect-ai Dataset with Sample objects
    """
    from lofbench.renderers import CanonicalRenderer

    renderer = renderer or CanonicalRenderer()
    cases = generate_composite_test_cases(
        n_groups=n_groups,
        group_size=group_size,
        seed=seed,
    )

    samples = []

    for case in cases:
        # Render each expression with its own content-addressed generator
        # (M3): no shared, order-dependent Random across the group or batch.
        rendered_exprs = [
            renderer.render(expr, _item_rng(render_seed, expr)) for expr in case["expressions"]
        ]

        # Check if rendering as images
        is_image = rendered_exprs[0].metadata.get("format") == "image"

        if is_image:
            # Build multimodal content with labeled images
            content_parts = []
            for i, r in enumerate(rendered_exprs):
                content_parts.append(ContentText(text=f"E{i + 1}."))
                content_parts.append(ContentImage(image=r.rendered))

            # Add evaluation prompt at the end
            content_parts.append(ContentText(text="What do these expressions evaluate to?"))

            input_content = [ChatMessageUser(content=content_parts)]
            # Still create formatted_input for metadata/debugging
            formatted_input = f"[{len(rendered_exprs)} images]"
        else:
            # For text, format as numbered list
            formatted_input = "\n".join(
                f"E{i + 1}. {r.rendered}" for i, r in enumerate(rendered_exprs)
            )
            input_content = "Evaluate the expressions"  # Placeholder, template provides real prompt

        samples.append(
            Sample(
                id=case["id"],
                input=input_content,
                target=",".join(case["targets"]),  # e.g., "marked,unmarked,marked,marked"
                metadata={
                    "expressions": formatted_input,  # For template substitution
                    "difficulty": case["difficulty"],
                    "group_size": case["group_size"],
                    "original_expressions": case["expressions"],
                    "targets": case["targets"],  # Per-expression targets
                    "renderer": renderer.name,
                    "rendered_expressions": [r.rendered for r in rendered_exprs],
                    # M3/acceptance-9: route per-expression provenance through
                    # the same emit path so a composite Sample carries the
                    # same render_metadata chain a single-task Sample does,
                    # instead of dropping it entirely.
                    "render_metadata": [r.metadata for r in rendered_exprs],
                },
            )
        )

    return MemoryDataset(samples=samples, name=f"lof_composite_{renderer.name}")

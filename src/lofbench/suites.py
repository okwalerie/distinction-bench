"""Freeze, load, and verify the public Laws of Form suite.

The checked-in suite is the publication authority for abstract forms, dialect
specifications, named subsets, and stimulus hashes. Evaluation code consumes this
artifact; it never regenerates a public case from a seed.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
from dataclasses import dataclass, replace
from hashlib import blake2b, sha256
from pathlib import Path
from typing import Any

from lofbench.core import (
    DIFFICULTY_CONFIGS,
    generate_form_string,
    normal_value,
    string_depth,
    string_to_form,
)
from lofbench.renderers import get_renderer
from lofbench.renderers.pipeline.archetype import assert_node_map_complete
from lofbench.renderers.pipeline.composed import ComposedRenderer
from lofbench.renderers.pipeline.nodes import form_to_nodes
from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY
from lofbench.renderers.pipeline.spec import DIALECT_SPECS, DialectSpec

DEFAULT_SUITE_VERSION = "v1"
FULL_FORMS_PER_TIER = 80
CORE_FORMS_PER_TIER = 24
GENERATION_SEED = 20260704
EXCLUDED_DIALECT_IDS = frozenset({"circle"})
SUITES_DIR = Path(__file__).resolve().parents[2] / "suites"

_SPATIAL_ARCHETYPES = {
    "blocks@1",
    "enclosure@1",
    "graph@1",
    "map@1",
    "map_centred@1",
    "paths_lite@1",
    "rna_arc@1",
    "rooms@1",
    "trees@1",
}

_READING_RULES = {
    "parens@1": "Each matching delimiter pair is one mark; nesting is containment.",
    "pattern@1": "Each call-like group is one mark; nested groups are contained marks.",
    "rna_dotbracket@1": "Each matched parenthesis is one mark; dots are inert filler.",
    "tree_indent@1": "Each tree node is one mark; indented descendants are contained marks.",
    "word_brackets@1": "Each BEGIN/END pair is one mark; nested pairs express containment.",
    "prose@1": "Each described group is one mark; groups described inside it are contained.",
    "clause_embedding@1": "Each centre-embedded clause is one mark; embedding is containment.",
    "blocks@1": "Each block is one mark; blocks drawn on a block are contained by it.",
    "enclosure@1": "Each closed oval is one mark; an oval inside another is contained.",
    "graph@1": "Each graph node is one mark; directed parent-child links express containment.",
    "map@1": "Each rectangular region is one mark; regions within regions are contained.",
    "map_centred@1": "Each radial region is one mark; nested sectors express containment.",
    "paths_lite@1": "Each closed wiring path is one mark; nested paths express containment.",
    "rna_arc@1": "Each arc is one mark; arcs spanning other arcs express containment.",
    "rooms@1": "Each room boundary is one mark; rooms drawn inside rooms are contained.",
    "trees@1": "Each drawn tree node is one mark; descendants are contained marks.",
}

_AGENT_DIALECTS = {
    "biopolymer": "biopolymer.rna-dotbracket-plain-v1",
    "blocks": "blocks.plain-v1",
    "embedding": "embedding.center-clause-plain-v1",
    "enclosure": "enclosure.plain-v1",
    "graph": "graph.plain-v1",
    "map": "map.plain-v1",
    "map_centred": "map-centred.plain-v1",
    "parens": "parens.reference-v1",
    "paths": "paths.arc-nest-plain-v1",
    "pattern": "pattern.plain-v1",
    "prose": "prose.containment-plain-v1",
    "rooms": "rooms.plain-v1",
    "trees": "trees.plain-v1",
}


def _tier_rng_seed(base_seed: int, tier_name: str) -> int:
    digest = blake2b(f"{base_seed}\x00{tier_name}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _form_id(reference_transcription: str) -> str:
    digest = blake2b(reference_transcription.encode("utf-8"), digest_size=10).hexdigest()
    return f"lof_{digest}"


def generate_form_table(
    seed: int = GENERATION_SEED, per_tier: int = FULL_FORMS_PER_TIER
) -> list[dict[str, Any]]:
    """Generate a deterministic target-balanced candidate table.

    Public ids depend only on the reference transcription. Generation order and
    requested table size therefore cannot renumber an existing form.
    """
    if per_tier <= 0 or per_tier % 2:
        raise ValueError("per_tier must be a positive even number")

    seen_transcriptions: set[str] = set()
    seen_ids: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    target_quota = per_tier // 2
    # The easy tier has a small finite state space under its depth/mark bounds.
    # Two alternates per target are enough to replace no-op injector collisions
    # without making the 40/40 public quota mathematically impossible.
    candidate_quota = target_quota + 2

    for tier_name, min_depth, max_depth, max_width, max_marks in DIFFICULTY_CONFIGS:
        rng = random.Random(_tier_rng_seed(seed, tier_name))
        by_value = {"marked": 0, "unmarked": 0}
        attempts = 0
        max_attempts = per_tier * 10_000
        while min(by_value.values()) < candidate_quota and attempts < max_attempts:
            attempts += 1
            transcription = generate_form_string(
                min_depth=min_depth,
                max_depth=max_depth,
                max_width=max_width,
                max_marks=max_marks,
                rng=rng,
            )
            if not transcription or transcription in seen_transcriptions:
                continue
            depth = string_depth(transcription)
            mark_count = transcription.count("(")
            if not (min_depth <= depth <= max_depth) or mark_count > max_marks:
                continue
            value = normal_value(transcription)
            if by_value[value] >= candidate_quota:
                continue
            form_id = _form_id(transcription)
            if form_id in seen_ids and seen_ids[form_id] != transcription:
                raise RuntimeError(f"content-id collision for {form_id}")
            seen_ids[form_id] = transcription
            seen_transcriptions.add(transcription)
            candidates.append(
                {
                    "abstract_form_id": form_id,
                    "abstract_form": string_to_form(transcription),
                    "reference_transcription": transcription,
                    "normal_value": value,
                    "difficulty": tier_name,
                    "depth": depth,
                    "mark_count": mark_count,
                }
            )
            by_value[value] += 1
        if min(by_value.values()) < candidate_quota:
            raise RuntimeError(
                f"could not generate candidate pool for {per_tier}-form tier {tier_name!r} "
                f"after {max_attempts} attempts: {by_value}"
            )

    difficulty_order = {name: index for index, (name, *_rest) in enumerate(DIFFICULTY_CONFIGS)}
    candidates.sort(
        key=lambda form: (difficulty_order[form["difficulty"]], form["abstract_form_id"])
    )
    rejected_ids = _text_collision_form_ids(candidates)
    forms: list[dict[str, Any]] = []
    for tier_name, *_rest in DIFFICULTY_CONFIGS:
        for value in ("marked", "unmarked"):
            eligible = [
                form
                for form in candidates
                if form["difficulty"] == tier_name
                and form["normal_value"] == value
                and form["abstract_form_id"] not in rejected_ids
            ]
            if len(eligible) < target_quota:
                raise RuntimeError(
                    f"payload-collision rejection left only {len(eligible)} {value} "
                    f"forms in {tier_name!r}; need {target_quota}"
                )
            forms.extend(eligible[:target_quota])
    return sorted(
        forms,
        key=lambda form: (difficulty_order[form["difficulty"]], form["abstract_form_id"]),
    )


def _document_spec(spec: DialectSpec, suite_version: str) -> DialectSpec:
    modality = "image" if spec.archetype in _SPATIAL_ARCHETYPES else "text"
    label = spec.dialect_id.removesuffix("-v1").replace(".", " · ").replace("-", " ")
    treatment = ", ".join(name for name, _params in spec.injectors)
    description = f"A deterministic {spec.family} rendering of the same containment tree."
    if treatment:
        description += f" This arm applies {treatment}."
    limitations = [
        "Untaught runs mix convention inference with Laws of Form reduction.",
    ]
    if modality == "image":
        limitations.append("Raster layout and image perception remain possible confounds.")
    return replace(
        spec,
        suite_version=suite_version,
        label=label,
        reading_rule=_READING_RULES[spec.archetype],
        description=description,
        modality=modality,
        model_format="image/png" if modality == "image" else "text/plain; charset=utf-8",
        provenance=f"lofbench renderer {spec.archetype}",
        citation="g. spencer-brown, laws of form (1969); renderer implementation in this release",
        limitations=tuple(limitations),
    )


def frozen_dialect_specs(suite_version: str = DEFAULT_SUITE_VERSION) -> dict[str, DialectSpec]:
    specs = {
        dialect_id: _document_spec(spec, suite_version)
        for dialect_id, spec in DIALECT_SPECS.items()
        if dialect_id not in EXCLUDED_DIALECT_IDS
    }
    if len(specs) != 29:
        raise RuntimeError(f"suite v1 requires 29 dialects, found {len(specs)}")
    return dict(sorted(specs.items()))


def _check_node_maps_complete(forms: list[dict[str, Any]], specs: dict[str, DialectSpec]) -> None:
    for archetype_key in sorted({spec.archetype for spec in specs.values()}):
        archetype = ARCHETYPE_REGISTRY[archetype_key]
        for form in forms:
            root = form_to_nodes(form["reference_transcription"])
            base = archetype.build(root, random.Random(0))
            assert_node_map_complete(root, base.node_map)


def _check_dialects_resolve_live(specs: dict[str, DialectSpec]) -> None:
    for dialect_id in specs:
        get_renderer(dialect_id)


def _payload_bytes(payload: str, is_image: bool) -> bytes:
    if not is_image:
        return payload.encode("utf-8")
    prefix = "data:image/png;base64,"
    if not payload.startswith(prefix):
        raise RuntimeError("spatial renderer did not emit a PNG data URI")
    return base64.b64decode(payload.removeprefix(prefix), validate=True)


def compute_cells(
    forms: list[dict[str, Any]], specs: dict[str, DialectSpec]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render and independently repeat every cell, returning collision evidence."""
    cells: list[dict[str, Any]] = []
    by_form_hash: dict[tuple[str, str], list[str]] = {}
    for dialect_id, spec in sorted(specs.items()):
        renderer = ComposedRenderer(spec)
        for form in forms:
            transcription = form["reference_transcription"]
            first = renderer.render(transcription)
            second = renderer.render(transcription)
            if first.rendered != second.rendered or first.metadata != second.metadata:
                raise RuntimeError(
                    f"nondeterministic stimulus {form['abstract_form_id']}/{dialect_id}"
                )
            if first.metadata["structure_verified"] is not True:
                raise RuntimeError(
                    f"structure check failed for {form['abstract_form_id']}/{dialect_id}"
                )
            if first.metadata["modality"] == "text" and first.metadata["roundtrip_ok"] is not True:
                raise RuntimeError(
                    f"round-trip failed for {form['abstract_form_id']}/{dialect_id}"
                )
            is_image = first.metadata["format"] == "image"
            payload_bytes = _payload_bytes(first.rendered, is_image)
            model_hash = sha256(payload_bytes).hexdigest()
            symbolic_hash = first.metadata["payload_hash"]
            cell = {
                "abstract_form_id": form["abstract_form_id"],
                "dialect_id": dialect_id,
                "family": spec.family,
                "archetype": spec.archetype,
                "injectors": [[name, dict(params)] for name, params in spec.injectors],
                "modality": first.metadata["modality"],
                "model_format": spec.model_format,
                "symbolic_payload_hash": symbolic_hash,
                "model_payload_sha256": model_hash,
                "asset_path": (
                    f"stimuli/image/{dialect_id}/{form['abstract_form_id']}.png"
                    if is_image
                    else f"stimuli/text/{dialect_id}.json"
                ),
                "structure_verified": True,
                "roundtrip_ok": first.metadata["roundtrip_ok"],
            }
            if not is_image:
                cell["model_payload"] = first.rendered
            cells.append(cell)
            by_form_hash.setdefault((form["abstract_form_id"], model_hash), []).append(dialect_id)

    collisions = [
        {
            "abstract_form_id": form_id,
            "model_payload_sha256": payload_hash,
            "dialect_ids": sorted(dialect_ids),
        }
        for (form_id, payload_hash), dialect_ids in sorted(by_form_hash.items())
        if len(dialect_ids) > 1
    ]
    return cells, collisions


def _text_collision_form_ids(forms: list[dict[str, Any]]) -> set[str]:
    """Reject candidate forms for which two declared text dialects are byte-identical.

    This admission check deliberately runs without cairo so core generation and CI
    remain usable on a text-only machine. The final full-grid freeze repeats the same
    collision check across text and spatial payloads on the pinned visual toolchain.
    """
    specs = {
        dialect_id: spec
        for dialect_id, spec in frozen_dialect_specs().items()
        if spec.modality == "text"
    }
    _cells, collisions = compute_cells(forms, specs)
    return {collision["abstract_form_id"] for collision in collisions}


def _form_sets(forms: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_tier_value: dict[tuple[str, str], list[str]] = {}
    for form in forms:
        by_tier_value.setdefault((form["difficulty"], form["normal_value"]), []).append(
            form["abstract_form_id"]
        )
    for ids in by_tier_value.values():
        ids.sort()

    core: list[str] = []
    agent: list[str] = []
    probe: list[str] = []
    probe_targets = ("marked", "unmarked", "marked", "unmarked", "marked")
    for index, (tier_name, *_rest) in enumerate(DIFFICULTY_CONFIGS):
        for value in ("marked", "unmarked"):
            core.extend(by_tier_value[(tier_name, value)][:12])
            agent.extend(by_tier_value[(tier_name, value)][:4])
        probe.append(by_tier_value[(tier_name, probe_targets[index])][0])
    return {
        "full": [form["abstract_form_id"] for form in forms],
        "core": core,
        "agent": agent,
        "probe": probe,
    }


def _dialect_sets(specs: dict[str, DialectSpec]) -> dict[str, list[str]]:
    plain_by_archetype: dict[str, list[str]] = {}
    for dialect_id, spec in specs.items():
        if not spec.injectors:
            plain_by_archetype.setdefault(spec.archetype, []).append(dialect_id)
    core: list[str] = []
    for archetype, dialect_ids in sorted(plain_by_archetype.items()):
        preferred = "parens.reference-v1" if archetype == "parens@1" else sorted(dialect_ids)[0]
        core.append(preferred)
    core.append("parens.noisy-mismatched-v1")
    agent = [_AGENT_DIALECTS[family] for family in sorted(_AGENT_DIALECTS)]
    sets = {
        "all": sorted(specs),
        "text": sorted(key for key, spec in specs.items() if spec.modality == "text"),
        "spatial": sorted(key for key, spec in specs.items() if spec.modality == "image"),
        "core": sorted(core),
        "agent": agent,
        "probe_text": ["parens.reference-v1", "prose.containment-plain-v1"],
        "probe_multimodal": ["parens.reference-v1", "enclosure.plain-v1"],
    }
    if len(sets["core"]) != 17 or len(sets["agent"]) != 13:
        raise RuntimeError("dialect subset construction violated v1 counts")
    return sets


@dataclass(frozen=True)
class LoadedSuite:
    suite_version: str
    forms: list[dict[str, Any]]
    specs: dict[str, DialectSpec]
    cells: list[dict[str, Any]]
    form_sets: dict[str, list[str]]
    dialect_sets: dict[str, list[str]]
    collisions: list[dict[str, Any]]


def _suite_path(version: str) -> Path:
    return SUITES_DIR / f"{version}.json"


def freeze_suite(
    version: str = DEFAULT_SUITE_VERSION,
    seed: int = GENERATION_SEED,
    per_tier: int = FULL_FORMS_PER_TIER,
    out_path: Path | None = None,
) -> dict[str, Any]:
    forms = generate_form_table(seed=seed, per_tier=per_tier)
    specs = frozen_dialect_specs(suite_version=version)
    _check_node_maps_complete(forms, specs)
    _check_dialects_resolve_live(specs)
    cells, collisions = compute_cells(forms, specs)
    if collisions:
        first = collisions[0]
        raise RuntimeError(
            f"suite freeze rejected {len(collisions)} payload collisions; first={first}"
        )
    form_sets = _form_sets(forms)
    dialect_sets = _dialect_sets(specs)
    suite = {
        "schema_version": 2,
        "suite_version": version,
        "header": {
            "generation_seed": seed,
            "forms_per_tier": per_tier,
            "total_forms": len(forms),
            "total_dialects": len(specs),
            "total_families": len({spec.family for spec in specs.values()}),
            "total_cells": len(cells),
            "probe_normal_value_split": {"marked": 3, "unmarked": 2},
            "excluded_dialect_ids": sorted(EXCLUDED_DIALECT_IDS),
        },
        "forms": forms,
        "form_sets": form_sets,
        "dialects": {key: spec.to_dict() for key, spec in specs.items()},
        "dialect_sets": dialect_sets,
        "cells": cells,
        "collisions": [],
    }
    target = out_path if out_path is not None else _suite_path(version)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(suite, indent=2, ensure_ascii=False) + "\n")
    return suite


def load_suite(version: str = DEFAULT_SUITE_VERSION, path: Path | None = None) -> LoadedSuite:
    target = path if path is not None else _suite_path(version)
    data = json.loads(target.read_text())
    if data.get("schema_version") != 2:
        raise RuntimeError(f"unsupported suite schema in {target}")
    if data["suite_version"] != version:
        raise RuntimeError(
            f"suite version mismatch: requested {version!r}, file has {data['suite_version']!r}"
        )
    return LoadedSuite(
        suite_version=data["suite_version"],
        forms=data["forms"],
        specs={key: DialectSpec.from_dict(value) for key, value in data["dialects"].items()},
        cells=data["cells"],
        form_sets=data["form_sets"],
        dialect_sets=data["dialect_sets"],
        collisions=data["collisions"],
    )


def verify_suite(version: str = DEFAULT_SUITE_VERSION, path: Path | None = None) -> None:
    suite = load_suite(version=version, path=path)
    forms = {form["abstract_form_id"]: form for form in suite.forms}
    expected = {
        (cell["abstract_form_id"], cell["dialect_id"]): cell for cell in suite.cells
    }
    recomputed, collisions = compute_cells(list(suite.forms), suite.specs)
    problems: list[str] = []
    if collisions:
        problems.append(f"{len(collisions)} payload collision(s)")
    for cell in recomputed:
        key = (cell["abstract_form_id"], cell["dialect_id"])
        if key not in expected:
            problems.append(f"unexpected cell {key}")
            continue
        frozen = expected[key]
        for field in ("symbolic_payload_hash", "model_payload_sha256", "structure_verified"):
            if cell[field] != frozen[field]:
                problems.append(f"{key} field {field} changed")
    if len(expected) != len(recomputed):
        problems.append(f"cell count changed from {len(expected)} to {len(recomputed)}")
    if set(forms) != set(suite.form_sets["full"]):
        problems.append("full form set does not match form table")
    if problems:
        raise RuntimeError(
            f"suite {suite.suite_version!r} rerun gate failed: " + "; ".join(problems[:20])
        )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="freeze and verify a public suite")
    parser.add_argument("--version", default=DEFAULT_SUITE_VERSION)
    parser.add_argument("--seed", type=int, default=GENERATION_SEED)
    parser.add_argument("--per-tier", type=int, default=FULL_FORMS_PER_TIER)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.verify_only:
        suite = freeze_suite(args.version, args.seed, args.per_tier)
        print(
            f"froze {args.version}: {len(suite['forms'])} forms, "
            f"{len(suite['dialects'])} dialects, {len(suite['cells'])} cells"
        )
    verify_suite(args.version)
    print(f"suite {args.version} verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

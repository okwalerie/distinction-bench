"""M7: the frozen suite and payload hashing.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Frozen forms
and payload hashing", and ``docs/measurement-design.md`` section 7 ("Frozen
suite v1 composition"). ``suites/v1.json`` is the checked-in frozen
artifact this module generates and loads; a run cites ``--suite v1``.

Suite composition decisions (documented per the plan: "if a rule is
ambiguous, decide, document in the suite file's header, and note it" --
also recorded in ``suites/v1.json``'s own ``"header"`` field):

- Form count: the measurement doc's section 5 gives the flagship core as
  "about 100 to 150 distinct forms" at "20 to 30 per tier" across the five
  ``DIFFICULTY_CONFIGS`` tiers. This lands at the tier midpoint, 24 per
  tier, for a clean 5 x 24 = 120 total -- the "~120 forms" figure the DB-4
  M7 task names directly.
- Form ids: NOT the ``lof_{i:03d}`` positional scheme ``generate_test_cases``
  uses (section 7 explicitly warns that scheme breaks under a smaller
  ``n``). Ids here are assigned once, in tier order, over the deduplicated
  form table, and then frozen for good in the checked-in JSON.
- Dialect list: every entry in ``pipeline.spec.DIALECT_SPECS`` except
  ``"circle"``. ``"circle"`` is a legacy-registry-key alias for the exact
  same ``enclosure@1`` archetype and empty injector list as
  ``"enclosure.canonical-v1"`` (see ``renderers/archetypes/__init__.py``) --
  including both would manufacture a guaranteed payload collision on every
  form (same archetype, same injectors, only the ``dialect_id`` field
  differs), which is not a real ablation arm, just registry-key bookkeeping.
  Bare legacy renderer keys (``canonical``, ``noisy_parens``, ``sexpr``,
  ``nested_list``) are excluded too: they are not ``ComposedRenderer``
  instances, carry no ``render_provenance`` (no ``item_seed``,
  ``structure_verified``, ``payload_hash``), and the design doc is explicit
  that "a frozen run resolves dialects only from the checked-in
  ``DIALECT_SPECS``". This yields 29 dialects at the time of writing:
  the parens family (canonical, jitter, noisy, noisy-mismatched -- each a
  single-injector arm; no arm combines ``whitespace_jitter`` with
  ``bracket_swap`` in one dialect, a pre-existing composability gap
  documented in ``injectors/bracket_swap.py`` and flagged again here as an
  ablation-lattice limitation, not silently hidden), the pattern family
  (default plus six presets, each a single-injector arm), DB-2's ten text
  dialects (five families x canonical + one jitter arm each), and the nine
  spatial families' canonical-only specs (no spatial injector is
  implemented yet -- ``boundary_jitter``/``distractor_marks`` are named in
  the architecture doc's worked example 3 but never built -- so every
  spatial family contributes one canonical arm and no jitter counterpart,
  also a documented limitation, not a hidden one).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, replace
from hashlib import blake2b
from pathlib import Path
from typing import Any

from lofbench.core import DIFFICULTY_CONFIGS, generate_form_string, simplify_string
from lofbench.renderers import get_renderer
from lofbench.renderers.pipeline.archetype import assert_node_map_complete
from lofbench.renderers.pipeline.composed import ComposedRenderer
from lofbench.renderers.pipeline.nodes import form_to_nodes
from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY
from lofbench.renderers.pipeline.spec import DIALECT_SPECS, DialectSpec

DEFAULT_SUITE_VERSION = "v1"
CORE_FORMS_PER_TIER = 24  # 5 tiers x 24 = 120 -- see module docstring
GENERATION_SEED = 20260704

# Legacy-alias / non-composed registry keys excluded from every frozen
# suite -- see module docstring for the full rationale.
EXCLUDED_DIALECT_IDS = frozenset({"circle"})

SUITES_DIR = Path(__file__).resolve().parents[2] / "suites"


def _tier_rng_seed(base_seed: int, tier_name: str) -> int:
    """Deterministic per-tier substream seed, so tier generation order
    never perturbs another tier's draws (same discipline as the pipeline's
    own content-addressed seeding)."""
    digest = blake2b(f"{base_seed}\x00{tier_name}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def generate_form_table(
    seed: int = GENERATION_SEED, per_tier: int = CORE_FORMS_PER_TIER
) -> list[dict[str, Any]]:
    """Generate the frozen core's ``form_id -> form_string`` table:
    ``per_tier`` distinct forms per ``DIFFICULTY_CONFIGS`` tier,
    deduplicated by form string across the whole table (not just within a
    tier), assigned stable ids once and for all in tier order.
    """
    seen_forms: set[str] = set()
    forms: list[dict[str, Any]] = []
    form_counter = 1

    for tier_name, min_d, max_d, max_w, max_m in DIFFICULTY_CONFIGS:
        rng = random.Random(_tier_rng_seed(seed, tier_name))
        collected = 0
        attempts = 0
        # Generous attempt cap: duplicates are expected to be rare above
        # trivial depths, but the easy tier's small state space can repeat.
        max_attempts = per_tier * 200
        while collected < per_tier and attempts < max_attempts:
            attempts += 1
            form_string = (
                generate_form_string(
                    min_depth=min_d, max_depth=max_d, max_width=max_w, max_marks=max_m, rng=rng
                )
                or "()"
            )
            if form_string in seen_forms:
                continue
            seen_forms.add(form_string)
            canonical, _steps = simplify_string(form_string)
            target = "marked" if canonical == "()" else "unmarked"
            forms.append(
                {
                    "form_id": f"lof_{form_counter:03d}",
                    "form_string": form_string,
                    "difficulty": tier_name,
                    "target": target,
                }
            )
            form_counter += 1
            collected += 1
        if collected < per_tier:
            raise RuntimeError(
                f"could not generate {per_tier} distinct forms for tier {tier_name!r} "
                f"after {max_attempts} attempts (got {collected})"
            )

    return forms


def frozen_dialect_specs(suite_version: str = DEFAULT_SUITE_VERSION) -> dict[str, DialectSpec]:
    """Every named dialect eligible for a frozen suite (see module
    docstring for the exclusion rationale), with ``suite_version`` threaded
    into each spec -- this is the "loader threads suite_version into
    DialectSpec" requirement, shared by both the freezer and the loader so
    there is one source of truth for it.
    """
    return {
        dialect_id: replace(spec, suite_version=suite_version)
        for dialect_id, spec in DIALECT_SPECS.items()
        if dialect_id not in EXCLUDED_DIALECT_IDS
    }


def _check_node_maps_complete(forms: list[dict[str, Any]], specs: dict[str, DialectSpec]) -> None:
    """Freeze-time gate: node-map exactly-once, checked directly against
    every distinct archetype the frozen dialect set touches (an archetype
    is shared by several dialects -- e.g. every ``pattern.*`` dialect
    shares ``pattern@1`` -- so this checks each archetype once per form,
    not once per dialect). Raises on the first violation with the
    archetype/form pair named, per ``assert_node_map_complete``.
    """
    archetype_keys = {spec.archetype for spec in specs.values()}
    for archetype_key in sorted(archetype_keys):
        archetype = ARCHETYPE_REGISTRY[archetype_key]
        for form in forms:
            root = form_to_nodes(form["form_string"])
            base = archetype.build(root, random.Random(0))
            if base.node_map:  # some M1-era fixtures ship an intentionally empty map
                assert_node_map_complete(root, base.node_map)


def _check_dialects_resolve_live(specs: dict[str, DialectSpec]) -> None:
    """Freeze-time sanity check: every frozen ``dialect_id`` must also
    construct through the live ``get_renderer`` registry path -- catching
    a dialect that is still a ``DIALECT_SPECS`` entry but was, say,
    accidentally dropped from the renderer registry. Construction only
    (not a full render): this is a reachability check, not a duplicate of
    ``compute_cells``'s own rendering pass.
    """
    for dialect_id in specs:
        get_renderer(dialect_id)


def compute_cells(
    forms: list[dict[str, Any]], specs: dict[str, DialectSpec]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render every (form, dialect) cell once, recording its stamped
    ``payload_hash`` (computed by ``ComposedRenderer.render`` itself --
    see ``pipeline.emit``/``pipeline.composed`` -- never recomputed here,
    so there is one source of truth for what a payload hash means).

    Builds directly from each ``DialectSpec`` via ``ComposedRenderer``
    rather than going through ``get_renderer``/the live renderer registry:
    the frozen suite's identity is the structured spec, not a registry key
    (per the design doc's "named specs are the only frozen-suite source"),
    and this lets a caller (e.g. a test) pass in ad-hoc specs under
    dialect_ids that were never separately registered.

    Returns ``(cells, collisions)``. A collision is two or more distinct
    ``dialect_id``s sharing one ``payload_hash`` for the same ``form_id``
    -- flagged in the returned list, per the doc's own worked scenario
    (a trivial form under a jitter injector with nothing to jitter), not
    silently hidden and not a hard failure of the freeze itself.
    """
    cells: list[dict[str, Any]] = []
    by_form_hash: dict[tuple[str, str], list[str]] = {}

    for dialect_id, spec in sorted(specs.items()):
        renderer = ComposedRenderer(spec)
        for form in forms:
            result = renderer.render(form["form_string"])
            payload_hash = result.metadata["payload_hash"]
            cells.append(
                {
                    "form_id": form["form_id"],
                    "dialect_id": dialect_id,
                    "family": spec.family,
                    "modality": result.metadata["modality"],
                    "format": result.metadata["format"],
                    "structure_verified": result.metadata["structure_verified"],
                    "payload_hash": payload_hash,
                }
            )
            by_form_hash.setdefault((form["form_id"], payload_hash), []).append(dialect_id)

    collisions = [
        {"form_id": form_id, "payload_hash": payload_hash, "dialect_ids": sorted(dialect_ids)}
        for (form_id, payload_hash), dialect_ids in sorted(by_form_hash.items())
        if len(dialect_ids) > 1
    ]
    return cells, collisions


@dataclass(frozen=True)
class LoadedSuite:
    """What ``load_suite`` hands back to a caller (e.g. an eval-run script
    that cites ``--suite v1``): the frozen form table plus every dialect
    spec with ``suite_version`` already threaded through."""

    suite_version: str
    forms: list[dict[str, Any]]
    specs: dict[str, DialectSpec]
    cells: list[dict[str, Any]]
    collisions: list[dict[str, Any]]


def _suite_path(version: str) -> Path:
    return SUITES_DIR / f"{version}.json"


def freeze_suite(
    version: str = DEFAULT_SUITE_VERSION,
    seed: int = GENERATION_SEED,
    per_tier: int = CORE_FORMS_PER_TIER,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """(Re)generate the frozen suite from scratch: form table, dialect
    specs, per-cell payload hashes, and the collision check -- everything
    ``suites/v1.json`` holds. Writes the file when ``out_path`` is given
    (defaults to ``suites/<version>.json`` when not explicitly suppressed
    by passing ``out_path=False`` is not supported -- callers that only
    want the in-memory dict for testing pass an ``out_path`` under a
    temp directory).
    """
    forms = generate_form_table(seed=seed, per_tier=per_tier)
    specs = frozen_dialect_specs(suite_version=version)
    _check_node_maps_complete(forms, specs)
    _check_dialects_resolve_live(specs)
    cells, collisions = compute_cells(forms, specs)

    suite = {
        "suite_version": version,
        "header": {
            "generation_seed": seed,
            "forms_per_tier": per_tier,
            "total_forms": len(forms),
            "total_dialects": len(specs),
            "total_cells": len(cells),
            "excluded_dialect_ids": sorted(EXCLUDED_DIALECT_IDS),
            "note": (
                "Form count and per-tier split, dialect exclusions, and the "
                "ablation-lattice gaps (no combined parens injector arm; no "
                "spatial-family jitter arm) are documented decisions -- see "
                "lofbench.suites's module docstring for the full rationale."
            ),
        },
        "forms": forms,
        "dialects": {dialect_id: spec.to_dict() for dialect_id, spec in sorted(specs.items())},
        "cells": cells,
        "collisions": collisions,
    }

    if collisions:
        print(  # noqa: T201 -- deliberate freeze-time visibility, not a logger
            f"WARNING: {len(collisions)} payload collision(s) found across "
            f"{len(specs)} dialects x {len(forms)} forms -- see the "
            "'collisions' field. Flagged, not hidden; not a hard failure "
            "(see lofbench.suites's module docstring).",
            file=sys.stderr,
        )

    target = out_path if out_path is not None else _suite_path(version)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(suite, indent=2, sort_keys=False) + "\n")
    return suite


def load_suite(version: str = DEFAULT_SUITE_VERSION, path: Path | None = None) -> LoadedSuite:
    """Load a checked-in frozen suite. This is the ``--suite v1`` entry
    point: it does not re-render anything, only parses the checked-in
    JSON and rebuilds ``DialectSpec`` objects (``suite_version`` already
    threaded in, since it was frozen that way).
    """
    target = path if path is not None else _suite_path(version)
    data = json.loads(target.read_text())
    specs = {
        dialect_id: DialectSpec.from_dict(spec_dict)
        for dialect_id, spec_dict in data["dialects"].items()
    }
    return LoadedSuite(
        suite_version=data["suite_version"],
        forms=data["forms"],
        specs=specs,
        cells=data["cells"],
        collisions=data["collisions"],
    )


def verify_suite(version: str = DEFAULT_SUITE_VERSION, path: Path | None = None) -> None:
    """The rerun gate: re-render every frozen cell and assert its payload
    hash matches. Fails loudly (raises ``RuntimeError`` naming every
    mismatched cell) rather than warning and continuing -- a payload-hash
    mismatch means an un-versioned renderer edit changed a stimulus, per
    the doc's "Frozen forms and payload hashing".

    Rebuilds each cell's renderer from ``suite.specs[dialect_id]`` (the
    frozen, ``suite_version``-threaded spec ``load_suite`` just parsed
    back out of the checked-in JSON) via ``ComposedRenderer`` directly --
    not ``get_renderer(dialect_id)``, which would resolve against
    whatever is *currently* live in ``pipeline.spec.DIALECT_SPECS``. Since
    the content-addressed item seed folds in ``suite_version`` (part of
    ``DialectSpec.seed_digest()``), and a live named-dialect factory
    always builds with ``suite_version="adhoc"`` (see
    ``make_named_dialect_factory``), going through the live registry here
    would make every injector-bearing dialect "fail" the gate on a
    suite_version mismatch alone -- a false positive with nothing to do
    with a real renderer edit. Rebuilding from the frozen spec is what
    "named specs are the only frozen-suite source" means in practice.
    """
    suite = load_suite(version=version, path=path)
    forms_by_id = {form["form_id"]: form["form_string"] for form in suite.forms}
    mismatches: list[str] = []

    for cell in suite.cells:
        dialect_id = cell["dialect_id"]
        renderer = ComposedRenderer(suite.specs[dialect_id])
        result = renderer.render(forms_by_id[cell["form_id"]])
        recomputed = result.metadata["payload_hash"]
        if recomputed != cell["payload_hash"]:
            mismatches.append(
                f"{cell['form_id']}/{dialect_id}: frozen={cell['payload_hash']} "
                f"recomputed={recomputed}"
            )

    if mismatches:
        raise RuntimeError(
            f"suite {suite.suite_version!r} rerun gate failed: "
            f"{len(mismatches)} payload hash mismatch(es):\n" + "\n".join(mismatches)
        )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lofbench.suites", description="(Re)generate and verify a frozen suite."
    )
    parser.add_argument("--version", default=DEFAULT_SUITE_VERSION)
    parser.add_argument("--seed", type=int, default=GENERATION_SEED)
    parser.add_argument("--per-tier", type=int, default=CORE_FORMS_PER_TIER)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip regeneration; only run the rerun gate against the checked-in file.",
    )
    args = parser.parse_args(argv)

    if not args.verify_only:
        suite = freeze_suite(version=args.version, seed=args.seed, per_tier=args.per_tier)
        print(  # noqa: T201
            f"Froze suite {args.version!r}: {suite['header']['total_forms']} forms, "
            f"{suite['header']['total_dialects']} dialects, "
            f"{suite['header']['total_cells']} cells, "
            f"{len(suite['collisions'])} collision(s)."
        )

    verify_suite(version=args.version)
    print(f"Suite {args.version!r} rerun gate: OK.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

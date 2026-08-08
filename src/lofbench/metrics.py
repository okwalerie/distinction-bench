"""Family-balanced competence, invariance, and controlled-effect metrics."""

from __future__ import annotations

import itertools
import json
import math
import random
from collections import defaultdict
from hashlib import blake2b
from pathlib import Path
from typing import Any

import pandas as pd

from lofbench.records import RunManifest
from lofbench.suites import LoadedSuite

PROFILE_GROUP_COLUMNS = [
    "execution_surface",
    "requested_model_id",
    "resolved_model_id",
    "protocol_id",
    "reasoning",
]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _prediction(row: pd.Series) -> str:
    return str(row["prediction"]) if row["parse_status"] == "valid" else "__invalid__"


def _pairwise_invariance(
    rows: pd.DataFrame,
    *,
    partition_columns: list[str],
) -> float | None:
    disagreements: list[float] = []
    for _key, group in rows.groupby(partition_columns, dropna=False):
        predictions = [_prediction(row) for _index, row in group.iterrows()]
        if len(predictions) < 2:
            continue
        disagreements.extend(
            float(left != right) for left, right in itertools.combinations(predictions, 2)
        )
    mean_disagreement = _mean(disagreements)
    return None if mean_disagreement is None else 1.0 - mean_disagreement


def _mcnemar_exact(b: int, c: int) -> float:
    discordant = b + c
    if discordant == 0:
        return 1.0
    lower = min(b, c)
    tail = sum(math.comb(discordant, k) for k in range(lower + 1)) / (2**discordant)
    return min(1.0, 2 * tail)


def _bootstrap_interval(
    differences: list[float], *, seed_material: str, draws: int = 2000
) -> tuple[float, float]:
    if not differences:
        return (math.nan, math.nan)
    seed = int.from_bytes(blake2b(seed_material.encode(), digest_size=8).digest(), "big")
    rng = random.Random(seed)
    estimates = []
    for _draw in range(draws):
        sampled = [differences[rng.randrange(len(differences))] for _ in differences]
        estimates.append(sum(sampled) / len(sampled))
    estimates.sort()
    return estimates[int(0.025 * (draws - 1))], estimates[int(0.975 * (draws - 1))]


def _enrich_trials(
    trials: pd.DataFrame,
    runs: list[RunManifest],
    suite: LoadedSuite,
) -> pd.DataFrame:
    if trials.empty:
        return trials.copy()
    run_by_id = {run.run_id: run for run in runs if run.status == "admitted"}
    unknown = set(trials["run_id"]) - set(run_by_id)
    if unknown:
        raise RuntimeError(f"trials reference non-admitted runs: {sorted(unknown)}")
    spec_by_id = suite.specs
    enriched = trials.copy()
    enriched["reasoning"] = enriched["run_id"].map(
        lambda run_id: json.dumps(run_by_id[run_id].reasoning, sort_keys=True)
    )
    enriched["family"] = enriched["dialect_id"].map(lambda value: spec_by_id[value].family)
    enriched["archetype"] = enriched["dialect_id"].map(
        lambda value: spec_by_id[value].archetype
    )
    enriched["modality"] = enriched["dialect_id"].map(
        lambda value: spec_by_id[value].modality
    )
    enriched["is_plain"] = enriched["dialect_id"].map(
        lambda value: not spec_by_id[value].injectors
    )
    return enriched


def compute_profiles(
    trials: pd.DataFrame,
    runs: list[RunManifest],
    suite: LoadedSuite,
) -> pd.DataFrame:
    enriched = _enrich_trials(trials, runs, suite)
    if enriched.empty:
        return pd.DataFrame()
    expected_by_group: dict[tuple[Any, ...], int] = defaultdict(int)
    for run in runs:
        if run.status != "admitted":
            continue
        key = (
            run.execution_surface,
            run.requested_model_id,
            run.resolved_model_id,
            run.protocol_id,
            json.dumps(run.reasoning, sort_keys=True),
        )
        expected_by_group[key] += len(run.expected_trial_ids)

    profiles: list[dict[str, Any]] = []
    for key, group in enriched.groupby(PROFILE_GROUP_COLUMNS, dropna=False):
        dialect_accuracy = group.groupby("dialect_id")["correct"].mean().to_dict()
        dialect_family = {
            dialect_id: suite.specs[dialect_id].family for dialect_id in dialect_accuracy
        }
        family_scores: dict[str, list[float]] = defaultdict(list)
        for dialect_id, accuracy in dialect_accuracy.items():
            family_scores[dialect_family[dialect_id]].append(float(accuracy))
        family_accuracy = {
            family: sum(values) / len(values) for family, values in family_scores.items()
        }
        competence = _mean(list(family_accuracy.values()))
        modality_competence: dict[str, float | None] = {}
        for modality in ("text", "image"):
            modality_families = {
                family: score
                for family, score in family_accuracy.items()
                if any(
                    suite.specs[dialect_id].modality == modality
                    for dialect_id, dialect_family_value in dialect_family.items()
                    if dialect_family_value == family
                )
            }
            modality_competence[modality] = _mean(list(modality_families.values()))

        within = _pairwise_invariance(
            group,
            partition_columns=["abstract_form_id", "family", "archetype"],
        )
        cross: dict[str, float | None] = {}
        for modality in ("text", "image"):
            plain = group[(group["is_plain"]) & (group["modality"] == modality)]
            cross[modality] = _pairwise_invariance(
                plain,
                partition_columns=["abstract_form_id"],
            )
        expected = expected_by_group[tuple(key)]
        profiles.append(
            {
                **dict(zip(PROFILE_GROUP_COLUMNS, key, strict=True)),
                "competence": competence,
                "text_competence": modality_competence["text"],
                "spatial_competence": modality_competence["image"],
                "within_family_invariance": within,
                "cross_family_text_invariance": cross["text"],
                "cross_family_spatial_invariance": cross["image"],
                "invalid_output_rate": float((group["parse_status"] != "valid").mean()),
                "coverage": len(group) / expected if expected else 0.0,
                "observed_trials": len(group),
                "expected_trials": expected,
                "mean_latency_ms": float(group["latency_ms"].mean()),
                "input_tokens": int(group["input_tokens"].sum()),
                "output_tokens": int(group["output_tokens"].sum()),
                "reasoning_tokens": int(group["reasoning_tokens"].sum()),
                "cost_usd": float(group["observed_cost_usd"].sum()),
                "dialect_accuracy": json.dumps(dialect_accuracy, sort_keys=True),
                "family_accuracy": json.dumps(family_accuracy, sort_keys=True),
            }
        )
    return pd.DataFrame(profiles).sort_values(PROFILE_GROUP_COLUMNS).reset_index(drop=True)


def compute_controlled_effects(
    trials: pd.DataFrame,
    runs: list[RunManifest],
    suite: LoadedSuite,
) -> pd.DataFrame:
    enriched = _enrich_trials(trials, runs, suite)
    if enriched.empty:
        return pd.DataFrame()
    effects: list[dict[str, Any]] = []
    for key, group in enriched.groupby(PROFILE_GROUP_COLUMNS, dropna=False):
        for (family, archetype), arms in group.groupby(["family", "archetype"]):
            plain_ids = sorted(arms.loc[arms["is_plain"], "dialect_id"].unique())
            treatment_ids = sorted(arms.loc[~arms["is_plain"], "dialect_id"].unique())
            if not plain_ids or not treatment_ids:
                continue
            plain_id = "parens.reference-v1" if "parens.reference-v1" in plain_ids else plain_ids[0]
            plain = arms[arms["dialect_id"] == plain_id].drop_duplicates("abstract_form_id")
            for treatment_id in treatment_ids:
                treatment = arms[arms["dialect_id"] == treatment_id].drop_duplicates(
                    "abstract_form_id"
                )
                paired = plain.merge(
                    treatment,
                    on="abstract_form_id",
                    suffixes=("_plain", "_treatment"),
                )
                if paired.empty:
                    continue
                differences = [
                    float(treatment_correct) - float(plain_correct)
                    for plain_correct, treatment_correct in zip(
                        paired["correct_plain"], paired["correct_treatment"], strict=True
                    )
                ]
                b = int(((paired["correct_plain"]) & (~paired["correct_treatment"])).sum())
                c = int(((~paired["correct_plain"]) & (paired["correct_treatment"])).sum())
                seed_material = json.dumps([*key, plain_id, treatment_id])
                low, high = _bootstrap_interval(differences, seed_material=seed_material)
                effects.append(
                    {
                        **dict(zip(PROFILE_GROUP_COLUMNS, key, strict=True)),
                        "family": family,
                        "archetype": archetype,
                        "plain_dialect_id": plain_id,
                        "treatment_dialect_id": treatment_id,
                        "signed_paired_effect": sum(differences) / len(differences),
                        "bootstrap_low": low,
                        "bootstrap_high": high,
                        "mcnemar_b": b,
                        "mcnemar_c": c,
                        "mcnemar_exact_p": _mcnemar_exact(b, c),
                        "paired_forms": len(paired),
                        "coverage": len(paired) / max(len(plain), len(treatment)),
                    }
                )
    if not effects:
        return pd.DataFrame()
    return pd.DataFrame(effects).sort_values(
        [*PROFILE_GROUP_COLUMNS, "family", "archetype", "treatment_dialect_id"]
    ).reset_index(drop=True)


def write_release_metrics(
    bundle_root: Path,
    *,
    suite: LoadedSuite,
    runs: list[RunManifest],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trials = pd.read_parquet(bundle_root / "trials.parquet")
    profiles = compute_profiles(trials, runs, suite)
    effects = compute_controlled_effects(trials, runs, suite)
    profiles.to_parquet(bundle_root / "profiles.parquet", index=False)
    effects.to_parquet(bundle_root / "effects.parquet", index=False)
    return profiles, effects

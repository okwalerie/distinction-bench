from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from lofbench.records import RunManifest, TrialRecord
from lofbench.release_bundle import ReleaseBundle


def _clean_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "anchor").write_text("test\n")
    subprocess.run(["git", "add", "anchor"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test"], cwd=path, check=True)
    return path


def _run(*, status: str = "complete", suite_version: str = "v1") -> RunManifest:
    return RunManifest.plan(
        suite_version=suite_version,
        form_set="probe",
        dialect_set="probe_text",
        protocol_id="reduce-infer-v1",
        requested_model_id="example/model",
        resolved_model_id="example/model-20260808",
        execution_surface="direct_api",
        provider="openrouter",
        endpoint="provider/example",
        routing_policy={"allow_fallbacks": False},
        privacy_policy={"data_collection": "deny", "zdr": True},
        sdk_version="test",
        reasoning={"effort": "default"},
        generation={"temperature": 0, "max_tokens": 512},
        billing_channel="test",
        expected_trial_ids=("trial_one",),
        status=status,
    )


def _trial(run: RunManifest, **overrides) -> TrialRecord:
    values = {
        "trial_id": "trial_one",
        "run_id": run.run_id,
        "suite_version": run.suite_version,
        "abstract_form_id": "lof_example",
        "dialect_id": "parens.reference-v1",
        "protocol_id": run.protocol_id,
        "execution_surface": run.execution_surface,
        "requested_model_id": run.requested_model_id,
        "resolved_model_id": run.resolved_model_id,
        "provider": run.provider,
        "endpoint": run.endpoint,
        "prompt_hash": "a" * 64,
        "symbolic_payload_hash": "b" * 32,
        "model_payload_sha256": "c" * 64,
        "parse_status": "valid",
        "attempt_count": 1,
        "latency_ms": 10.0,
        "input_tokens": 20,
        "output_tokens": 4,
        "reasoning_tokens": 0,
        "observed_cost_usd": 0.001,
        "prediction": "marked",
        "normal_value": "marked",
        "correct": True,
    }
    values.update(overrides)
    return TrialRecord(**values)


@pytest.fixture
def working(tmp_path):
    repository = _clean_repository(tmp_path / "repo")
    run = _run()
    bundle = ReleaseBundle.create_working(
        tmp_path / "release",
        release_id="v1.0.0-test",
        repository_url="https://example.invalid/repo",
        repository_root=repository,
        expected_run_ids=(run.run_id,),
    )
    return bundle, repository, run


def test_create_validate_admit_and_seal(working):
    bundle, repository, run = working
    bundle.validate()
    bundle.admit_run(run, [_trial(run)])
    bundle.seal(repository_root=repository)
    reopened = ReleaseBundle.open(bundle.root)
    assert reopened.manifest["status"] == "sealed"
    assert reopened.manifest["admitted_run_ids"] == [run.run_id]
    assert reopened.manifest["files"]
    reopened.validate()


def test_incomplete_run_is_rejected(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="incomplete"):
        bundle.admit_run(run, [])


def test_noncomplete_and_pilot_runs_are_rejected(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="only complete"):
        bundle.admit_run(replace(run, status="probed"), [_trial(run)])
    pilot = _run(suite_version="pilot-v0")
    with pytest.raises(RuntimeError, match="does not match"):
        bundle.admit_run(pilot, [_trial(pilot)])


def test_resolved_model_drift_is_rejected(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="resolved model drift"):
        bundle.admit_run(run, [_trial(run, resolved_model_id="substituted/model")])


def test_dirty_repository_blocks_seal(working):
    bundle, repository, run = working
    bundle.admit_run(run, [_trial(run)])
    (repository / "dirty").write_text("dirty")
    with pytest.raises(RuntimeError, match="dirty worktree"):
        bundle.seal(repository_root=repository)


def test_unlisted_root_file_is_rejected(working):
    bundle, _repository, _run_manifest = working
    (bundle.root / "surprise.txt").write_text("nope")
    with pytest.raises(RuntimeError, match="root files"):
        bundle.validate()


def test_checksum_drift_is_rejected(working):
    bundle, repository, run = working
    bundle.admit_run(run, [_trial(run)])
    bundle.seal(repository_root=repository)
    with (bundle.root / "transcripts.jsonl").open("a") as handle:
        handle.write("{}\n")
    with pytest.raises(RuntimeError, match="checksum drift"):
        bundle.validate()


def test_sealed_bundle_refuses_mutation(working):
    bundle, repository, run = working
    bundle.admit_run(run, [_trial(run)])
    bundle.seal(repository_root=repository)
    with pytest.raises(RuntimeError, match="immutable"):
        bundle.admit_run(run, [_trial(run)])


def test_schema_mismatch_is_rejected(working):
    bundle, _repository, _run_manifest = working
    release_path = bundle.root / "release.json"
    value = json.loads(release_path.read_text())
    value["bundle_schema_version"] = 999
    release_path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="unsupported"):
        ReleaseBundle.open(bundle.root)

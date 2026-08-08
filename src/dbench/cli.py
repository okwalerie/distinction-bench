"""Release-operator CLI; all provider and secret wiring lives in this package."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from inspect_ai import Task

from dbench.agent_cli import (
    ClaudeCliExecutor,
    CodexCliExecutor,
    agent_subprocess_env,
    cli_version,
)
from dbench.config import load_env_file
from dbench.openrouter import OpenRouterInspectExecutor, fetch_openrouter_endpoint
from lofbench.accounting import SpendLedger
from lofbench.metrics import write_release_metrics
from lofbench.orchestration import DEFAULT_GLOBAL_CAP_USD, RunOrchestrator
from lofbench.publication import (
    archive_release,
    export_inspect_bundle,
    sanitize_public_mapping,
)
from lofbench.records import CallRecord, RunManifest, TrialRecord
from lofbench.release_bundle import ReleaseBundle
from lofbench.run_models import ExecutionSpec, TrialExecutor, plan_run
from lofbench.state_io import read_jsonl, write_json_atomic
from lofbench.suites import load_suite
from lofbench.tasks.single import single_lof_task

COHORT_CAPS_USD = {"sample": 30.0, "broad": 18.0, "frontier": 7.5, "reserve": 4.5}
SAMPLE_PROTOCOLS = (
    "reduce-infer-v1",
    "reduce-taught-v1",
    "transcribe-infer-v1",
    "transcribe-taught-v1",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ledger(state_root: Path) -> SpendLedger:
    return SpendLedger(
        state_root / "spend-ledger.jsonl",
        global_cap=DEFAULT_GLOBAL_CAP_USD,
        cohort_caps=COHORT_CAPS_USD,
    )


def _load_secret_env(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    return load_env_file(path)


def _state_dir(args: argparse.Namespace) -> Path:
    return args.state_root / args.run_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dbench")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--release", type=Path, required=True)
    plan.add_argument("--state-root", type=Path, required=True)
    plan.add_argument("--release-id", required=True)
    plan.add_argument("--repository-url", required=True)
    plan.add_argument("--suite", type=Path, default=Path("suites/v1.json"))
    plan.add_argument("--model", required=True)
    plan.add_argument("--form-set", default="probe")
    plan.add_argument("--dialect", required=True)
    plan.add_argument("--protocol", action="append", required=True)
    plan.add_argument(
        "--execution-surface",
        choices=("direct_api", "codex_cli", "claude_cli"),
        default="direct_api",
    )
    plan.add_argument("--env-file", type=Path)
    plan.add_argument("--cohort", choices=tuple(COHORT_CAPS_USD), default="sample")
    plan.add_argument("--max-transport-attempts", type=int, default=1)
    plan.add_argument("--approved-by")
    plan.add_argument("--approval-scope")
    for name in ("run", "resume"):
        command = subparsers.add_parser(name)
        command.add_argument("--release", type=Path, required=True)
        command.add_argument("--state-root", type=Path, required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--env-file", type=Path)
        command.add_argument("--approve-paid-run", action="store_true")
        command.add_argument("--max-spend-usd", type=float)
    status = subparsers.add_parser("status")
    status.add_argument("--state-root", type=Path, required=True)
    status.add_argument("--run-id", required=True)
    admit = subparsers.add_parser("admit")
    admit.add_argument("--release", type=Path, required=True)
    admit.add_argument("--state-root", type=Path, required=True)
    admit.add_argument("--run-id", required=True)
    seal = subparsers.add_parser("seal")
    seal.add_argument("--release", type=Path, required=True)
    seal.add_argument("--env-file", type=Path)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--release", type=Path, required=True)
    inspect_export = subparsers.add_parser("export-inspect")
    inspect_export.add_argument("--release", type=Path, required=True)
    inspect_export.add_argument("--out", type=Path, required=True)
    archive = subparsers.add_parser("archive")
    archive.add_argument("--release", type=Path, required=True)
    archive.add_argument("--out", type=Path, required=True)
    return parser


def _agent_execution(args: argparse.Namespace, modality: str) -> ExecutionSpec:
    if modality != "text":
        raise RuntimeError("subscription CLI surfaces currently admit text dialects only")
    command = args.execution_surface.removesuffix("_cli")
    environment = agent_subprocess_env()
    return ExecutionSpec(
        requested_model_id=args.model,
        resolved_model_id=args.model,
        execution_surface=args.execution_surface,
        provider=command,
        endpoint=command,
        routing_policy={
            "local_cli": command,
            "environment_allowlist": sorted(environment),
            "secrets_scrubbed": True,
        },
        privacy_policy={"local_process": True, "secret_environment_inheritance": False},
        sdk_version=cli_version(command, environment=environment),
        reasoning={"effort": "default"},
        generation={"temperature": 0, "max_tokens": 512},
        billing_channel="subscription_unmetered",
        cohort=args.cohort,
        max_transport_attempts=args.max_transport_attempts,
    )


def _validate_sample_plan(args: argparse.Namespace) -> dict[str, object] | None:
    if args.release_id != "v1.0.0-sample.1":
        return None
    if (
        args.form_set != "probe"
        or args.dialect != "enclosure.plain-v1"
        or args.execution_surface != "direct_api"
        or args.max_transport_attempts != 1
        or tuple(args.protocol) != SAMPLE_PROTOCOLS
    ):
        raise RuntimeError("v1.0.0-sample.1 plan does not match its frozen contract")
    return {
        "protocol_ids": list(SAMPLE_PROTOCOLS),
        "form_set": "probe",
        "dialect_id": "enclosure.plain-v1",
        "execution_surface": "direct_api",
        "max_transport_attempts": 1,
        "trials_per_run": 5,
        "total_attempts": 20,
    }


def _plan(args: argparse.Namespace) -> int:
    if args.release.exists():
        raise FileExistsError("plan requires a new release directory")
    sample_contract = _validate_sample_plan(args)
    suite = load_suite(path=args.suite)
    if args.dialect not in suite.specs:
        raise ValueError(f"unknown frozen dialect {args.dialect!r}")
    spec = suite.specs[args.dialect]
    if args.execution_surface == "direct_api":
        if not args.approved_by or not args.approval_scope:
            raise RuntimeError(
                "direct-api planning requires an explicit human approval identity and scope"
            )
        values = _load_secret_env(args.env_file)
        api_key = values.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("authenticated endpoint planning requires OPENROUTER_API_KEY")
        selection = fetch_openrouter_endpoint(
            args.model,
            api_key=api_key,
            required_modality="image" if spec.modality != "text" else None,
        )
        execution = selection.execution_spec(
            cohort=args.cohort,
            max_transport_attempts=args.max_transport_attempts,
        )
    else:
        selection = None
        execution = _agent_execution(args, spec.modality)
    tasks_and_runs: list[tuple[Task, RunManifest]] = []
    for protocol_id in args.protocol:
        task = single_lof_task(
            suite=str(args.suite),
            form_set=args.form_set,
            dialect=args.dialect,
            protocol=protocol_id,
        )
        run = plan_run(
            task,
            suite_version=suite.suite_version,
            form_set=args.form_set,
            dialect_set=args.dialect,
            protocol_id=protocol_id,
            execution=execution,
        )
        tasks_and_runs.append((task, run))
    run_ids = [run.run_id for _task, run in tasks_and_runs]
    paid_approval = None
    if selection is not None:
        paid_approval = {
            "approved_by": args.approved_by,
            "approved_at": _now(),
            "scope": args.approval_scope,
            "max_spend_usd": DEFAULT_GLOBAL_CAP_USD,
            "release_id": args.release_id,
            "run_ids": run_ids,
            "model_id": selection.model_id,
            "endpoint": selection.endpoint_tag,
            "provider": selection.provider_name,
            "paid_calls": sum(len(run.expected_trial_ids) for _task, run in tasks_and_runs),
        }
    ReleaseBundle.create_working(
        args.release,
        release_id=args.release_id,
        repository_url=args.repository_url,
        repository_root=Path.cwd(),
        suite_path=args.suite,
        expected_run_ids=run_ids,
        paid_run_approval=paid_approval,
        materialize_stimuli=True,
        sample_contract=sample_contract,
    )
    cost_sheet: dict[str, object] = {
        "model_id": args.model,
        "execution_surface": args.execution_surface,
        "runs": [],
    }
    if selection is not None:
        cost_sheet.update(
            {
                "catalog_retrieved_at": selection.retrieved_at,
                "endpoint": selection.endpoint_tag,
                "provider": selection.provider_name,
                "pricing": selection.pricing,
                "authenticated_zdr_intersection": True,
            }
        )
    run_costs: list[dict[str, object]] = []
    for task, run in tasks_and_runs:
        state_dir = args.state_root / run.run_id
        write_json_atomic(state_dir / "run.json", run.to_dict())
        projected = (
            sum(RunOrchestrator.reservation(run, sample) for sample in task.dataset.samples)
            if run.billing_channel != "subscription_unmetered"
            else 0.0
        )
        run_costs.append(
            {
                "run_id": run.run_id,
                "protocol_id": run.protocol_id,
                "trials": len(run.expected_trial_ids),
                "conservative_reservation_usd": projected,
            }
        )
    cost_sheet["runs"] = run_costs
    write_json_atomic(args.state_root / "cost-sheet.json", cost_sheet)
    print(
        json.dumps(
            {
                "release_id": args.release_id,
                "run_ids": [run.run_id for _task, run in tasks_and_runs],
                "paid_calls": sum(len(run.expected_trial_ids) for _task, run in tasks_and_runs),
            },
            indent=2,
        )
    )
    return 0


def _executor(run: RunManifest, *, secrets: dict[str, str]) -> TrialExecutor:
    if run.execution_surface == "direct_api":
        return OpenRouterInspectExecutor(api_key=secrets.get("OPENROUTER_API_KEY", ""))
    environment = agent_subprocess_env(secret_names=secrets)
    if run.execution_surface == "codex_cli":
        return CodexCliExecutor(environment=environment)
    if run.execution_surface == "claude_cli":
        return ClaudeCliExecutor(environment=environment)
    raise RuntimeError(f"unsupported execution surface {run.execution_surface!r}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        return _plan(args)
    if args.command == "status":
        run = json.loads((_state_dir(args) / "run.json").read_text())
        observed, reserved = _ledger(args.state_root).totals(run["cohort"])
        print(
            json.dumps(
                {
                    "run_id": run["run_id"],
                    "status": run["status"],
                    "cohort_observed_spend_usd": observed,
                    "cohort_reserved_spend_usd": reserved,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "admit":
        state_dir = _state_dir(args)
        run = RunManifest.from_dict(json.loads((state_dir / "run.json").read_text()))
        ReleaseBundle.open(args.release).admit_run(
            run,
            [TrialRecord(**row) for row in read_jsonl(state_dir / "trials.jsonl")],
            calls=[CallRecord(**row) for row in read_jsonl(state_dir / "calls.jsonl")],
            ledger_events=_ledger(args.state_root).events_for_run(run.run_id),
            transcripts=[
                sanitize_public_mapping(row) for row in read_jsonl(state_dir / "transcripts.jsonl")
            ],
        )
        return 0
    if args.command == "seal":
        values = _load_secret_env(args.env_file)
        ReleaseBundle.open(args.release).seal(
            repository_root=Path.cwd(), publication_environment=values
        )
        return 0
    if args.command == "prepare":
        from lofsite.build import build_site

        bundle = ReleaseBundle.open(args.release)
        if bundle.manifest["status"] != "working":
            raise RuntimeError("prepare requires a working release bundle")
        suite = load_suite(
            version=bundle.manifest["suite_version"], path=bundle.root / "suite.json"
        )
        write_release_metrics(bundle.root, suite=suite, runs=bundle.runs())
        build_site(bundle.root, bundle.root / "site")
        bundle.validate()
        return 0
    if args.command == "export-inspect":
        bundle = ReleaseBundle.open(args.release)
        bundle.validate()
        if bundle.manifest["status"] != "sealed":
            raise RuntimeError("inspect export requires a sealed bundle")
        export_inspect_bundle(bundle.root, args.out)
        return 0
    if args.command == "archive":
        bundle = ReleaseBundle.open(args.release)
        bundle.validate()
        if bundle.manifest["status"] != "sealed":
            raise RuntimeError("archive requires a sealed bundle")
        archive_release(bundle.root, args.out)
        return 0

    values = _load_secret_env(args.env_file)
    state_dir = _state_dir(args)
    run = RunManifest.from_dict(json.loads((state_dir / "run.json").read_text()))
    bundle = ReleaseBundle.open(args.release)
    bundle.require_repository_state(Path.cwd())
    bundle.validate()
    if run.run_id not in bundle.manifest["expected_run_ids"]:
        raise RuntimeError("run is not declared by this release bundle")
    if run.execution_surface == "direct_api" and "OPENROUTER_API_KEY" not in values:
        raise RuntimeError("direct-api execution requires OPENROUTER_API_KEY")
    if run.billing_channel != "subscription_unmetered" and args.approve_paid_run:
        approval = bundle.manifest.get("paid_run_approval") or {}
        if (
            approval.get("max_spend_usd") != DEFAULT_GLOBAL_CAP_USD
            or run.run_id not in approval.get("run_ids", [])
            or approval.get("model_id") != run.requested_model_id
            or approval.get("endpoint") != run.endpoint
        ):
            raise RuntimeError("paid-run approval does not match this exact run identity")
    task = single_lof_task(
        suite=str(args.release / "suite.json"),
        form_set=run.form_set,
        dialect=run.dialect_set,
        protocol=run.protocol_id,
    )
    updated = RunOrchestrator(
        state_dir,
        _executor(run, secrets=values),
        ledger=_ledger(args.state_root),
    ).execute(
        run,
        task,
        approve_paid_run=args.approve_paid_run,
        max_spend_usd=args.max_spend_usd,
    )
    print(json.dumps({"run_id": updated.run_id, "status": updated.status}, indent=2))
    return 0

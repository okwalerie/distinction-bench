from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lofbench.authority import PROTOCOL_REGISTRY_GIT_PATH, SUITE_REGISTRY_GIT_PATH
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY
from lofbench.release_bundle import ReleaseBundle, ReleasePolicyContext
from lofbench.suites import DEFAULT_SUITE_REGISTRY
from lofsite.build import (
    PUBLIC_EFFECT_SCHEMA,
    PUBLIC_PROFILE_SCHEMA,
    SAFE_DOWNLOADS,
    build_site,
    verify_public_site,
    verify_site_tree,
)

_ROOT_FILES = {
    "index.html",
    "forms.html",
    "atlas.html",
    "runs.html",
    "presentation.html",
    "human.html",
    "downloads.html",
    "CNAME",
}
_FORBIDDEN_FILENAMES = {
    "release.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "transcripts.jsonl",
    "request-started.jsonl",
    "ledger.jsonl",
}


@pytest.fixture(scope="session")
def authority_repository(tmp_path_factory):
    repository = tmp_path_factory.mktemp("static-authority-repo")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repository, check=True)
    (repository / "anchor").write_text("test\n")
    suite_target = repository / SUITE_REGISTRY_GIT_PATH
    suite_target.parent.mkdir(parents=True)
    protocol_target = repository / PROTOCOL_REGISTRY_GIT_PATH
    protocol_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_SUITE_REGISTRY, suite_target)
    shutil.copyfile(DEFAULT_PROTOCOL_REGISTRY, protocol_target)
    subprocess.run(["git", "add", "anchor", "src"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "test"], cwd=repository, check=True)
    return repository


@pytest.fixture(scope="session")
def site_publication(tmp_path_factory, authority_repository):
    repository = authority_repository
    bundle = ReleaseBundle.create_working(
        tmp_path_factory.mktemp("static-site") / "release",
        release_id="v1.0.0-test",
        repository_url="https://example.invalid/repo",
        repository_root=repository,
        spend_caps_usd={"global": 30.0, "cohorts": {}},
    )
    payload = b"test spatial asset\n"
    digest = sha256(payload).hexdigest()
    publication = bundle.publication()
    cells = []
    for cell in publication.suite.cells:
        row = dict(cell)
        if row["modality"] != "text":
            row["model_payload_sha256"] = digest
            target = publication.root / row["asset_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        cells.append(row)
    publication = replace(
        publication,
        status="sealed",
        stimuli_materialized=True,
        suite=replace(publication.suite, cells=cells),
    )
    build_site(publication, bundle.root / "site")
    return publication


def _site_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _hardlink_site(source: Path, target: Path) -> None:
    shutil.copytree(source, target, copy_function=os.link)


def _replace_file(path: Path, value: str) -> None:
    path.unlink()
    path.write_text(value)


def _projected_rows(rows, schema: pa.Schema) -> list[dict]:
    return [{field.name: row.get(field.name) for field in schema} for row in rows]


def _populated_publication(publication):
    dialect_id = "enclosure.plain-v1"
    family = publication.suite.specs[dialect_id].family
    profile = {
        "execution_surface": "direct_api",
        "requested_model_id": "example/model",
        "resolved_model_id": "example/model-20260811",
        "protocol_id": "reduce-infer-v1",
        "reasoning": '{"effort": "default"}',
        "competence": 0.4,
        "text_competence": 0.4,
        "spatial_competence": None,
        "within_family_invariance": 1.0,
        "invalid_output_rate": 0.0,
        "coverage": 1.0,
        "observed_trials": 5,
        "expected_trials": 5,
        "mean_latency_ms": 123.0,
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": 0,
        "cost_usd": 0.0125,
        "dialect_accuracy": '{"enclosure.plain-v1": 0.4}',
        "family_accuracy": f'{{"{family}": 0.4}}',
    }
    effect = {
        "resolved_model_id": "example/model-20260811",
        "protocol_id": "reduce-infer-v1",
        "family": family,
        "plain_dialect_id": dialect_id,
        "treatment_dialect_id": "enclosure.distractor-v1",
        "signed_paired_effect": -0.2,
        "bootstrap_low": -0.4,
        "bootstrap_high": 0.0,
        "mcnemar_exact_p": 0.5,
    }
    private_run_id = "run_0123456789abcdef01234567"
    private_trial_id = "trial_0123456789abcdef01234567"
    private_endpoint = "private-provider-endpoint"
    private_response = '{"value":"private-answer"}'
    return replace(
        publication,
        sample_contract={"protocol_ids": ["reduce-infer-v1"] * 4, "trials_per_run": 5},
        runs=(SimpleNamespace(run_id=private_run_id, endpoint=private_endpoint),),
        trials=(
            {
                "trial_id": private_trial_id,
                "run_id": private_run_id,
                "prompt_hash": "1" * 64,
                "response_text": private_response,
                "completion_evidence_sha256": "2" * 64,
            },
        ),
        profiles=(profile,),
        effects=(effect,),
    ), {
        private_run_id,
        private_trial_id,
        private_endpoint,
        private_response,
        "1" * 64,
        "2" * 64,
    }


def _sample_publication(publication):
    populated, private_values = _populated_publication(publication)
    base = dict(populated.profiles[0])
    values = (
        ("reduce-infer-v1", 0.4, 16644.7748, 135364, 60, 0.016965499999999998),
        ("reduce-taught-v1", 0.4, 16596.1298, 135454, 60, 0.016976750000000002),
        ("transcribe-infer-v1", 0.0, 17079.903199999997, 137804, 880, 0.0178855),
        ("transcribe-taught-v1", 0.0, 17142.7474, 137869, 594, 0.017679125),
    )
    profiles = []
    for protocol, competence, latency, input_tokens, output_tokens, cost in values:
        profiles.append(
            {
                **base,
                "resolved_model_id": "google/gemini-3.1-flash-lite-20260507",
                "protocol_id": protocol,
                "competence": competence,
                "text_competence": None,
                "spatial_competence": competence,
                "mean_latency_ms": latency,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "dialect_accuracy": f'{{"enclosure.plain-v1": {competence}}}',
            }
        )
    return replace(
        populated,
        sample_contract={
            "protocol_ids": [value[0] for value in values],
            "dialect_id": "enclosure.plain-v1",
            "execution_surface": "direct_api",
            "trials_per_run": 5,
        },
        profiles=tuple(reversed(profiles)),
    ), private_values


class _AccessibilityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.labelled_by: list[tuple[str, ...]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))
        if tag == "svg" and attributes.get("role") == "img":
            self.labelled_by.append(tuple(str(attributes.get("aria-labelledby", "")).split()))


def test_static_site_is_built_only_inside_a_working_bundle(tmp_path: Path, authority_repository):
    bundle = ReleaseBundle.create_working(
        tmp_path / "release",
        release_id="v1.0.0-test",
        repository_url="https://example.invalid/repo",
        repository_root=authority_repository,
        spend_caps_usd={"global": 30.0, "cohorts": {}},
    )
    with pytest.raises(RuntimeError, match="working bundle"):
        build_site(bundle.publication(), tmp_path / "outside")


def test_static_site_has_exact_safe_surface_and_every_dialect_exemplar(site_publication):
    publication = site_publication
    site = publication.root / "site"
    expected_images = {
        f"assets/{cell['asset_path']}"
        for cell in publication.suite.cells
        if cell["modality"] != "text"
    }
    expected = (
        _ROOT_FILES
        | {f"dialects/{dialect_id}.html" for dialect_id in publication.suite.specs}
        | {f"downloads/{name}" for name in SAFE_DOWNLOADS}
        | expected_images
    )
    assert _site_files(site) == expected
    assert len(expected) == 3_642
    verify_public_site(publication, site)

    index = (site / "index.html").read_text()
    atlas = (site / "atlas.html").read_text()
    probe_ids = set(publication.suite.form_sets["probe"])
    medium_forms = [
        form["abstract_form_id"]
        for form in publication.suite.forms
        if form["abstract_form_id"] in probe_ids and form["difficulty"] == "2. medium"
    ]
    assert len(medium_forms) == 1
    exemplar_form_id = medium_forms[0]
    for dialect_id in publication.suite.specs:
        marker = f'data-dialect-exemplar="{dialect_id}"'
        assert marker in index
        assert marker in atlas
        assert (site / "dialects" / f"{dialect_id}.html").is_file()
    assert index.count("data-dialect-exemplar=") == 29
    assert atlas.count("data-dialect-exemplar=") == 29
    exemplar_label = f"same frozen form · <code>{exemplar_form_id}</code>"
    assert index.count(exemplar_label) == 29
    assert atlas.count(exemplar_label) == 29
    assert index.count("<img loading=lazy") == 9
    assert atlas.count("<img loading=lazy") == 9
    assert index.count("<div class=stimulus><pre>") == 20
    assert atlas.count("<div class=stimulus><pre>") == 20
    assert "connect-src 'none'" in index


def test_release_policy_context_uses_the_shared_publication_projection(site_publication):
    manifest = json.loads((site_publication.root / "release.json").read_text())
    manifest.update(status="sealed", stimuli_materialized=True)
    context = ReleasePolicyContext(
        root=site_publication.root,
        manifest=manifest,
        suite=site_publication.suite,
        runs=site_publication.runs,
        trials=site_publication.trials,
        calls=(),
        sealing=True,
    )
    projected = context.publication()
    assert projected == site_publication
    assert projected.root == site_publication.root
    assert projected.suite is site_publication.suite
    assert projected.status == "sealed"
    verify_public_site(projected, site_publication.root / "site")


def test_static_site_retains_explanations_human_pilot_and_only_safe_downloads(
    site_publication,
):
    publication = site_publication
    site = publication.root / "site"
    forms = (site / "forms.html").read_text()
    assert "system prompt" in forms
    assert "every form and its set membership" in forms
    assert "possible confounds" in forms
    dialect = next((site / "dialects").glob("*.html")).read_text()
    assert "symbolic hash" in dialect
    downloads = (site / "downloads.html").read_text()
    assert "public benchmark data" in downloads
    assert "sha256" in downloads
    assert "caveats" in downloads
    for name in ("suite.json", "protocols.json", "human-trial.schema.json"):
        assert (site / "downloads" / name).read_bytes() == (publication.root / name).read_bytes()
    profiles = pq.read_table(site / "downloads" / "profiles.parquet")
    effects = pq.read_table(site / "downloads" / "effects.parquet")
    assert profiles.schema == PUBLIC_PROFILE_SCHEMA
    assert effects.schema == PUBLIC_EFFECT_SCHEMA
    assert profiles.to_pylist() == []
    assert effects.to_pylist() == []
    assert "run_id" not in profiles.column_names
    assert "run_id" not in effects.column_names
    assert not (_site_files(site) & {f"downloads/{name}" for name in _FORBIDDEN_FILENAMES})
    assert not any(path.name in _FORBIDDEN_FILENAMES for path in site.rglob("*"))

    human = (site / "human.html").read_text()
    assert "12 local-only stimuli" in human
    assert "participant_code" in human
    assert 'minlength=3 maxlength=64 pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}"' in human
    assert "familiarity" in human
    assert "confidence" in human
    assert "elapsed_ms" in human
    assert "fetch(" not in human
    assert "method=post" not in human.lower()
    assert "connect-src 'none'" in human


def test_populated_results_render_only_comprehensible_aggregates(site_publication, tmp_path: Path):
    publication, private_values = _populated_publication(site_publication)
    output = tmp_path / "public"
    build_site(publication, output)
    runs = (output / "runs.html").read_text()
    for marker in (
        "aggregate scope",
        "1 resolved model, 1 protocol, 5 observations across 1 profile",
        "how these rows are derived",
        "competence + invariance profiles",
        "aggregate resource use",
        "controlled dialect effects",
        "dialect matrix",
        "family matrix",
        "reasoning contrasts",
        "example/model-20260811",
        "reduce-infer-v1",
        "direct_api",
        "0.0125",
    ):
        assert marker in runs
    presentation = (output / "presentation.html").read_text()
    index = (output / "index.html").read_text()
    for page in (index, runs, presentation):
        assert "one model" not in page
        assert "one spatial dialect" not in page
        assert "four protocols" not in page
        assert "five observations per protocol" not in page
        assert "the two reduce protocols observed the same result" not in page
        assert "the two transcription protocols observed the same result" not in page
    assert "aggregate presentation" in presentation
    assert "sample presentation" not in presentation
    for private in private_values:
        assert private not in runs
    for marker in (
        "exact endpoint",
        "catalog_row",
        "routing_policy",
        "provider_request_id",
        "response_text",
        "prompt_hash",
        "call_id",
        "trial_id",
        "run_id",
    ):
        assert marker not in runs.lower()
    profiles = pq.read_table(output / "downloads" / "profiles.parquet")
    effects = pq.read_table(output / "downloads" / "effects.parquet")
    assert profiles.schema == PUBLIC_PROFILE_SCHEMA
    assert effects.schema == PUBLIC_EFFECT_SCHEMA
    assert profiles.to_pylist() == _projected_rows(publication.profiles, PUBLIC_PROFILE_SCHEMA)
    assert effects.to_pylist() == _projected_rows(publication.effects, PUBLIC_EFFECT_SCHEMA)
    aggregate_values = {
        value
        for table in (profiles, effects)
        for field in table.schema
        if pa.types.is_string(field.type)
        for value in table[field.name].to_pylist()
        if value is not None
    }
    assert aggregate_values.isdisjoint(private_values)


def test_non_sample_populated_profiles_use_only_generic_scope(site_publication, tmp_path: Path):
    publication, _private_values = _populated_publication(site_publication)
    publication = replace(publication, sample_contract=None)
    output = tmp_path / "non-sample"
    build_site(publication, output)
    for name in ("index.html", "runs.html", "presentation.html"):
        page = (output / name).read_text()
        assert "aggregate scope" in page
        assert "sample outcome" not in page
        assert "sample presentation" not in page
        assert "smoke test" not in page
        assert "observed the same result" not in page


def test_sample_charts_are_aggregate_only_accessible_and_presentation_ready(
    site_publication, tmp_path: Path
):
    publication, private_values = _sample_publication(site_publication)
    output = tmp_path / "sample-charts"
    build_site(publication, output)
    verify_public_site(publication, output)
    rebuilt = tmp_path / "sample-charts-rebuilt"
    build_site(publication, rebuilt)
    verify_site_tree(output, rebuilt)

    index = (output / "index.html").read_text()
    results = (output / "runs.html").read_text()
    presentation = (output / "presentation.html").read_text()
    assert index.count('data-chart="outcome-by-protocol"') == 1
    assert 'data-chart="valid-versus-correct"' not in index
    assert 'data-chart="resource-footprint"' not in index
    for page in (results, presentation):
        assert page.count('data-chart="outcome-by-protocol"') == 1
        assert page.count('data-chart="valid-versus-correct"') == 1
        assert page.count('data-chart="resource-footprint"') == 1
        assert "validity 100%" in page
        assert "correctness 0%" in page
        assert "$0.069506875" in page
        assert "20 observations" in page
        assert "546,491 input tokens" in page
        assert "1,594 output tokens" in page
        assert "exact outcome values" in page
        assert "exact validity and correctness values" in page
        assert "exact resource values" in page
        assert "1 model, 1 spatial dialect, 4 protocols, and n=5 per protocol" in page
        assert "cannot estimate dialect sensitivity, invariance, or controlled effects" in page
        for exact_value in (
            "16.6447748",
            "16.5961298",
            "17.079903199999997",
            "17.1427474",
        ):
            assert f"<td>{exact_value}</td>" in page
        for exact_value in ("12", "176", "118.8"):
            assert f"<td>{exact_value}</td>" in page
        assert "<td>16.645</td>" not in page
        assert "<td>118.800</td>" not in page
        offsets = [
            page.index(protocol)
            for protocol in (
                "reduce-infer-v1",
                "reduce-taught-v1",
                "transcribe-infer-v1",
                "transcribe-taught-v1",
            )
        ]
        assert offsets == sorted(offsets)
        parser = _AccessibilityParser()
        parser.feed(page)
        parser.close()
        assert len(parser.labelled_by) == 3
        assert len(parser.ids) == len(set(parser.ids))
        assert all(len(labels) == 2 for labels in parser.labelled_by)
        assert all(label in parser.ids for labels in parser.labelled_by for label in labels)
    assert presentation.count('role="img"') == 3
    assert "<canvas" not in presentation
    assert "<script" not in presentation
    assert "connect-src 'none'" in presentation
    assert "sample presentation" in presentation
    assert "the two reduce protocols observed the same result" in presentation
    assert "the two transcription protocols observed the same result" in presentation
    assert all(private not in index + results + presentation for private in private_values)


def test_empty_profiles_share_an_honest_chart_state(site_publication):
    site = site_publication.root / "site"
    for name in ("index.html", "runs.html", "presentation.html"):
        page = (site / name).read_text()
        assert "no admitted aggregate profiles; charts are not available" in page
        assert 'data-chart="' not in page
        assert 'role="img"' not in page
        assert ">nan<" not in page.lower()
        assert "nan%" not in page.lower()
        assert "sample outcome" not in page
        assert "sample presentation" not in page
        assert "smoke test" not in page
        assert "observed the same result" not in page


def test_empty_profiles_do_not_activate_sample_copy_from_contract(site_publication, tmp_path: Path):
    publication = replace(
        site_publication,
        sample_contract={
            "protocol_ids": [
                "reduce-infer-v1",
                "reduce-taught-v1",
                "transcribe-infer-v1",
                "transcribe-taught-v1",
            ],
            "dialect_id": "enclosure.plain-v1",
            "execution_surface": "direct_api",
            "trials_per_run": 5,
        },
    )
    output = tmp_path / "empty-with-contract"
    build_site(publication, output)
    for name in ("index.html", "runs.html", "presentation.html"):
        page = (output / name).read_text()
        assert "no admitted aggregate profiles; charts are not available" in page
        assert "sample outcome" not in page
        assert "sample presentation" not in page
        assert "smoke test" not in page
        assert "observed the same result" not in page


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("competence", True),
        ("competence", float("nan")),
        ("invalid_output_rate", 1.01),
        ("observed_trials", -1),
        ("observed_trials", 6),
        ("input_tokens", -1),
        ("reasoning_tokens", -1),
        ("mean_latency_ms", float("inf")),
        ("cost_usd", -0.01),
    ),
)
def test_chart_projection_rejects_invalid_numeric_profiles(
    site_publication, tmp_path: Path, field: str, value: object
):
    publication, _private_values = _populated_publication(site_publication)
    profile = dict(publication.profiles[0])
    profile[field] = value
    forged = replace(publication, profiles=(profile,))
    with pytest.raises(RuntimeError, match="published profile"):
        build_site(forged, tmp_path / f"invalid-{field}-{type(value).__name__}")


def test_public_site_verifier_fails_closed(site_publication, tmp_path: Path):
    publication = site_publication
    source = publication.root / "site"

    unexpected = tmp_path / "unexpected"
    _hardlink_site(source, unexpected)
    (unexpected / "owned.txt").write_text("surprise")
    with pytest.raises(RuntimeError, match="file set is not exact"):
        verify_public_site(publication, unexpected)

    broken = tmp_path / "broken"
    _hardlink_site(source, broken)
    index = broken / "index.html"
    _replace_file(index, index.read_text().replace('href="forms.html"', 'href="missing.html"'))
    with pytest.raises(RuntimeError, match="broken local site reference"):
        verify_public_site(publication, broken)

    forbidden = tmp_path / "forbidden"
    _hardlink_site(source, forbidden)
    runs = forbidden / "runs.html"
    _replace_file(runs, runs.read_text() + "<p>run_0123456789abcdef01234567</p>")
    with pytest.raises(RuntimeError, match="private execution detail"):
        verify_public_site(publication, forbidden)

    secret = tmp_path / "secret"
    _hardlink_site(source, secret)
    index = secret / "index.html"
    _replace_file(index, index.read_text() + "<p>OPENROUTER_API_KEY=not-a-real-key</p>")
    with pytest.raises(RuntimeError, match="publication secret scan failed"):
        verify_public_site(publication, secret)


@pytest.mark.parametrize(
    "reference",
    (
        "https://external.invalid/path",
        "//external.invalid/path",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "&#x68;ttps://external.invalid/path",
        "https%3A%2F%2Fexternal.invalid%2Fpath",
        "%252F%252Fexternal.invalid%252Fpath",
    ),
)
def test_public_site_rejects_every_external_reference(
    site_publication, tmp_path: Path, reference: str
):
    output = tmp_path / "external"
    _hardlink_site(site_publication.root / "site", output)
    index = output / "index.html"
    _replace_file(index, index.read_text() + f'<a href="{reference}">external</a>')
    with pytest.raises(RuntimeError, match="external site reference"):
        verify_public_site(site_publication, output)


@pytest.mark.parametrize(
    "markup",
    (
        '<img srcset="https://external.invalid/one.png 1x">',
        '<img SRCSET="assets/stimuli/image/local.png 1x">',
        '<svg><use xlink:href="https%3A%2F%2Fexternal.invalid%2Fshape"></use></svg>',
        '<svg><use XLINK:HREF="&#x68;ttps://external.invalid/shape"></use></svg>',
        '<form action="//external.invalid/submit"></form>',
    ),
)
def test_public_site_rejects_unsupported_url_bearing_attributes(
    site_publication, tmp_path: Path, markup: str
):
    output = tmp_path / "unsupported-url-attribute"
    _hardlink_site(site_publication.root / "site", output)
    index = output / "index.html"
    _replace_file(index, index.read_text() + markup)
    with pytest.raises(RuntimeError, match="unsupported url-bearing attribute"):
        verify_public_site(site_publication, output)


def test_public_site_accepts_local_queries_and_fragments(site_publication, tmp_path: Path):
    output = tmp_path / "local-reference"
    _hardlink_site(site_publication.root / "site", output)
    index = output / "index.html"
    _replace_file(
        index,
        index.read_text().replace(
            'href="forms.html"',
            'href="forms.html?from=index#protocols"',
            1,
        )
        + '<a href="#claim">fragment</a><a href="?view=all#claim">query</a>',
    )
    verify_public_site(site_publication, output)


def test_public_aggregate_downloads_reject_private_schemas_and_values(
    site_publication, tmp_path: Path
):
    forged_schema = tmp_path / "forged-schema"
    _hardlink_site(site_publication.root / "site", forged_schema)
    effects = forged_schema / "downloads" / "effects.parquet"
    effects.unlink()
    pq.write_table(
        pa.table({"run_id": ["run_0123456789abcdef01234567"]}),
        effects,
    )
    with pytest.raises(RuntimeError, match="aggregate schema is not exact"):
        verify_public_site(site_publication, forged_schema)

    publication, _private_values = _populated_publication(site_publication)
    private_profile = dict(publication.profiles[0])
    private_profile["resolved_model_id"] = publication.runs[0].run_id
    private_publication = replace(publication, profiles=(private_profile,))
    with pytest.raises(RuntimeError, match="private execution detail appears in profiles.parquet"):
        build_site(private_publication, tmp_path / "forged-value")


def test_sealed_bundle_rebuild_is_byte_identical(site_publication, tmp_path: Path):
    publication = site_publication
    output = tmp_path / "rebuilt"
    build_site(publication, output)
    verify_public_site(publication, output)
    verify_site_tree(publication.root / "site", output)


def test_site_tree_equality_rejects_a_byte_mismatch(site_publication, tmp_path: Path):
    output = tmp_path / "rebuilt"
    _hardlink_site(site_publication.root / "site", output)
    index = output / "index.html"
    _replace_file(index, "forged site")
    with pytest.raises(RuntimeError, match="does not match"):
        verify_site_tree(site_publication.root / "site", output)


def test_nonempty_site_output_is_refused(site_publication, tmp_path: Path):
    output = tmp_path / "site"
    output.mkdir()
    (output / "owned.txt").write_text("keep")
    with pytest.raises(FileExistsError, match="not empty"):
        build_site(site_publication, output)
    assert (output / "owned.txt").read_text() == "keep"

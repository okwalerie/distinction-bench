"""Build the public, bundle-only static benchmark gallery."""

from __future__ import annotations

import html
import json
import shutil
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from lofbench.protocols import ProtocolSpec
from lofbench.records import RunManifest
from lofbench.release_bundle import PublicationView
from lofbench.suites import LoadedSuite

_DOWNLOADS = (
    "suite.json",
    "protocols.json",
    "human-trial.schema.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "profiles.parquet",
    "effects.parquet",
    "transcripts.jsonl",
    "request-started.jsonl",
    "ledger.jsonl",
)

_CSS = """
:root{color-scheme:light;--ink:#171713;--muted:#68685f;--line:#d7d6c9;--paper:#f7f6ee;
--accent:#5d3fc0}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.52 system-ui,sans-serif}main,header,footer{max-width:1120px;margin:auto;padding:1.2rem}
nav{display:flex;gap:1rem;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:1rem}
a{color:var(--accent)}h1{font-size:clamp(2rem,6vw,4.5rem);line-height:1;margin:.8em 0 .25em}
h2{margin-top:2.2rem}.lede{max-width:70ch;font-size:1.2rem}.grid{display:grid;
grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}
.card{border:1px solid var(--line);background:#fff;padding:1rem;border-radius:.35rem}
.metric{font-size:2rem;font-variant-numeric:tabular-nums}.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;background:#fff}th,td{padding:.5rem;
border:1px solid var(--line);text-align:left;vertical-align:top}
pre{white-space:pre-wrap;overflow-wrap:anywhere}.stimulus{min-height:12rem;display:grid;
place-items:center;background:#fff;border:1px solid var(--line);padding:1rem}
.stimulus img{max-width:100%;max-height:18rem}.tabs button{padding:.5rem 1rem;margin-right:.3rem}
[hidden]{display:none!important}.notice{border-left:.35rem solid #c08900;padding:.7rem 1rem;
background:#fff8df}code{font-size:.88em}.answer button{padding:.7rem 1rem;margin:.2rem}
.dialect-cells details{margin:.5rem 0}
footer{margin-top:4rem;border-top:1px solid var(--line);color:var(--muted)}
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _nav(prefix: str = "") -> str:
    links = (
        ("index.html", "what is tested"),
        ("forms.html", "forms + protocols"),
        ("atlas.html", "dialect atlas"),
        ("runs.html", "models + runs"),
        ("human.html", "human pilot"),
        ("downloads.html", "downloads + citation"),
    )
    return (
        "<nav>"
        + "".join(f'<a href="{prefix}{href}">{_e(label)}</a>' for href, label in links)
        + "</nav>"
    )


def _page(title: str, body: str, *, prefix: str = "") -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'\">"
        f"<title>{_e(title)} · distinction benchmark</title><style>{_CSS}</style></head><body>"
        f"<header>{_nav(prefix)}</header><main>{body}</main>"
        "<footer>distinction benchmark · code: mit · suite and site content: cc by 4.0"
        "</footer></body></html>"
    )


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def site_tree_checksums(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_site_tree(expected: Path, rebuilt: Path) -> None:
    """Require exact path and byte equality for a regenerated sealed site."""
    if site_tree_checksums(expected) != site_tree_checksums(rebuilt):
        raise RuntimeError("rebuilt site tree does not match the sealed bundle site")


def _overview(
    publication: PublicationView,
    suite: LoadedSuite,
    protocols: dict[str, ProtocolSpec],
) -> str:
    runs = publication.runs
    trials = len(publication.trials)
    families = {spec.family for spec in suite.specs.values()}
    metrics = (
        (len(suite.forms), "frozen abstract forms"),
        (len(suite.specs), "documented dialects"),
        (len(families), "representation families"),
        (len(protocols), "task protocols"),
        (len(runs), "admitted runs"),
        (trials, "admitted trials"),
    )
    cards = "".join(
        f"<div class=card><div class=metric>{number}</div><div>{_e(label)}</div></div>"
        for number, label in metrics
    )
    caveat = (
        "this is a protocol smoke test, not a benchmark result or model ranking."
        if publication.sample_contract
        else "results shown here are only admitted bundle records."
    )
    return (
        "<h1>distinction benchmark</h1>"
        "<p class=lede>an inspectable benchmark of whether a model can preserve laws of form "
        "structure and reduce it across genuinely different representational dialects.</p>"
        f"<p class=notice>release <strong>{_e(publication.release_id)}</strong>: "
        f"{_e(caveat)}</p>"
        f"<section class=grid>{cards}</section><h2>the claim</h2>"
        "<p>competence and representational invariance are reported separately. a model can be "
        "consistently wrong; that does not make it competent. plain and treated dialect effects "
        "are paired only within an archetype.</p>"
    )


def _forms(suite: LoadedSuite, protocols: dict[str, ProtocolSpec]) -> str:
    by_difficulty: dict[str, list[dict[str, Any]]] = {}
    for form in suite.forms:
        by_difficulty.setdefault(form["difficulty"], []).append(form)
    rows = "".join(
        f"<tr><td>{_e(name)}</td><td>{len(forms)}</td>"
        f"<td>{sum(f['normal_value'] == 'marked' for f in forms)}</td>"
        f"<td>{sum(f['normal_value'] == 'unmarked' for f in forms)}</td></tr>"
        for name, forms in by_difficulty.items()
    )
    memberships = {
        form["abstract_form_id"]: sorted(
            name for name, ids in suite.form_sets.items() if form["abstract_form_id"] in ids
        )
        for form in suite.forms
    }
    form_rows = "".join(
        "<tr>"
        f"<td><code>{_e(form['abstract_form_id'])}</code></td>"
        f"<td><code>{_e(form['reference_transcription'])}</code></td>"
        f"<td>{_e(form['normal_value'])}</td><td>{_e(form['difficulty'])}</td>"
        f"<td>{_e(', '.join(memberships[form['abstract_form_id']]))}</td></tr>"
        for form in suite.forms
    )
    protocol_cards = "".join(
        "<article class=card>"
        f"<h3><code>{_e(spec.protocol_id)}</code></h3>"
        f"<p><strong>system prompt</strong></p><pre>{_e(spec.system_text)}</pre>"
        f"<p><strong>user prompt template</strong></p><pre>{_e(spec.user_template)}</pre>"
        f"<p class=muted>answer: {_e(spec.answer_kind)} · scorer: {_e(spec.scorer_id)}@"
        f"{_e(spec.scorer_version)} · dialect legend: {_e(spec.includes_dialect_legend)}</p>"
        "<details><summary>exact response schema</summary>"
        f"<pre>{_e(json.dumps(spec.response_json_schema, indent=2))}</pre></details>"
        "</article>"
        for spec in protocols.values()
    )
    form_sets = "".join(
        f"<tr><td><code>{_e(name)}</code></td><td>{len(ids)}</td>"
        f"<td>{_e(', '.join(ids[:8]))}{' …' if len(ids) > 8 else ''}</td></tr>"
        for name, ids in sorted(suite.form_sets.items())
    )
    return (
        "<h1>forms + protocols</h1><p class=lede>the abstract form, visual dialect, task protocol, "
        "model endpoint, and execution surface are separate frozen variables.</p>"
        "<h2>balanced form table</h2><table><thead><tr><th>difficulty</th><th>forms</th>"
        f"<th>marked</th><th>unmarked</th></tr></thead><tbody>{rows}</tbody></table>"
        f"<h2>frozen form sets</h2><table><thead><tr><th>set</th><th>forms</th>"
        f"<th>first ids</th></tr></thead><tbody>{form_sets}</tbody></table>"
        "<h2>protocol registry</h2><p class=notice><strong>possible confounds:</strong> infer "
        "protocols measure convention inference "
        "and task performance together; taught protocols expose the reading rule but may measure "
        "instruction use. image dialects additionally confound raster perception and layout.</p>"
        f"<section class=grid>{protocol_cards}</section>"
        "<h2>every form and its set membership</h2><table><thead><tr><th>form id</th>"
        "<th>reference form</th><th>normal value</th><th>difficulty</th><th>sets</th></tr>"
        f"</thead><tbody>{form_rows}</tbody></table>"
    )


def _cell_markup(cell: dict[str, Any], *, prefix: str = "") -> str:
    if cell["modality"] != "text":
        stimulus = (
            f'<img loading=lazy src="{prefix}assets/{_e(cell["asset_path"])}" '
            f'alt="frozen stimulus {_e(cell["abstract_form_id"])}">'
        )
    else:
        stimulus = f"<pre>{_e(cell['model_payload'])}</pre>"
    return (
        f"<details><summary><code>{_e(cell['abstract_form_id'])}</code></summary>"
        f"<div class=stimulus>{stimulus}</div><p class=muted>sha256 "
        f"<code>{_e(cell['model_payload_sha256'])}</code><br>symbolic hash "
        f"<code>{_e(cell['symbolic_payload_hash'])}</code></p></details>"
    )


def _atlas(out: Path, suite: LoadedSuite) -> str:
    cells_by_dialect: dict[str, list[dict[str, Any]]] = {}
    for cell in suite.cells:
        cells_by_dialect.setdefault(cell["dialect_id"], []).append(cell)
    cards = []
    for dialect_id, spec in suite.specs.items():
        cells = cells_by_dialect[dialect_id]
        cards.append(
            "<article class=card>"
            f"<h3>{_e(spec.label)}</h3><p><code>{_e(dialect_id)}</code></p>"
            f"<p>{_e(spec.description)}</p><p class=muted>{_e(spec.modality)} · "
            f"{_e(spec.family)} · {_e(spec.archetype)} · {len(cells)} cells</p>"
            f'<a href="dialects/{_e(dialect_id)}.html">inspect every frozen cell</a></article>'
        )
        cell_markup = "".join(_cell_markup(cell, prefix="../") for cell in cells)
        limitations = "".join(f"<li>{_e(item)}</li>" for item in spec.limitations)
        body = (
            f"<h1>{_e(spec.label)}</h1><p><code>{_e(dialect_id)}</code></p>"
            f"<p class=lede>{_e(spec.description)}</p><div class=grid>"
            f"<article class=card><h2>reading rule</h2><p>{_e(spec.reading_rule)}</p></article>"
            "<article class=card><h2>identity</h2>"
            f"<pre>{_e(json.dumps(spec.to_dict(), indent=2))}</pre>"
            "</article></div><h2>limitations</h2>"
            f"<ul>{limitations}</ul><h2>all frozen cells</h2>"
            f"<section class=dialect-cells>{cell_markup}</section>"
        )
        _write(out / "dialects" / f"{dialect_id}.html", _page(spec.label, body, prefix="../"))
    return (
        "<h1>dialect atlas</h1><p class=lede>every dialect declares its reading rule, provenance, "
        "modality, limits, structural family, and exact frozen payload hashes.</p>"
        f"<section class=grid>{''.join(cards)}</section>"
    )


def _records_table(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    if not rows:
        return "<p class=notice>no admitted records for this table.</p>"
    heading = "".join(f"<th>{_e(column.replace('_', ' '))}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(row.get(column, ''))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"


def _worked_path(
    publication: PublicationView,
    runs: list[RunManifest],
    profiles: list[dict[str, Any]],
) -> str:
    trials = publication.trials
    if not runs or not trials:
        return "<p class=notice>no admitted trial is available for a worked path.</p>"
    run = runs[0]
    trial = next(row for row in trials if row["run_id"] == run.run_id)
    suite = publication.suite
    protocols = publication.protocols
    form = next(
        item for item in suite.forms if item["abstract_form_id"] == trial["abstract_form_id"]
    )
    cell = next(
        item
        for item in suite.cells
        if item["abstract_form_id"] == trial["abstract_form_id"]
        and item["dialect_id"] == trial["dialect_id"]
    )
    spec = suite.specs[trial["dialect_id"]]
    protocol = protocols[trial["protocol_id"]]
    prompt = protocol.render_user_text(reading_rule=spec.reading_rule)
    reasoning = json.dumps(run.reasoning, sort_keys=True)
    profile = next(
        (
            row
            for row in profiles
            if row.get("execution_surface") == run.execution_surface
            and row.get("resolved_model_id") == run.resolved_model_id
            and row.get("protocol_id") == run.protocol_id
            and row.get("reasoning") == reasoning
        ),
        None,
    )
    if cell["modality"] == "text":
        stimulus = f"<pre>{_e(cell['model_payload'])}</pre>"
    else:
        stimulus = (
            f'<img src="assets/{_e(cell["asset_path"])}" '
            f'alt="actual rendered stimulus {_e(cell["abstract_form_id"])}">'
        )
    profile_value = (
        "not available"
        if profile is None
        else (
            f"this trial contributes {int(bool(trial['correct']))} to the correct-count "
            f"numerator; profile competence {profile.get('competence')}, coverage "
            f"{profile.get('coverage')}, mean latency {profile.get('mean_latency_ms')} ms"
        )
    )
    cards = (
        (
            "1 · containment tree and frozen form",
            f"<pre>{_e(json.dumps(form['abstract_form'], separators=(',', ':')))}</pre>"
            f"<p>reference transcription <code>{_e(form['reference_transcription'])}</code>; "
            f"normal value <strong>{_e(form['normal_value'])}</strong></p>",
        ),
        (
            "2 · actual rendered stimulus",
            f"<div class=stimulus>{stimulus}</div><p>dialect <code>{_e(trial['dialect_id'])}"
            f"</code>; family {_e(spec.family)}</p><p>symbolic hash "
            f"<code>{_e(cell['symbolic_payload_hash'])}</code><br>payload sha256 "
            f"<code>{_e(cell['model_payload_sha256'])}</code></p>",
        ),
        (
            "3 · reading rule, protocol, and exact prompt",
            f"<p><strong>reading rule</strong></p><pre>{_e(spec.reading_rule)}</pre>"
            f"<p>protocol <code>{_e(protocol.protocol_id)}</code></p>"
            f"<p><strong>system prompt</strong></p><pre>{_e(protocol.system_text)}</pre>"
            f"<p><strong>user prompt</strong></p><pre>{_e(prompt)}</pre>"
            f"<p>prompt hash <code>{_e(trial['prompt_hash'])}</code></p>",
        ),
        (
            "4 · target and recorded response",
            f"<p>target</p><pre>{_e(protocol.target_for(form))}</pre>"
            f"<p>response</p><pre>{_e(trial.get('response_text', ''))}</pre>",
        ),
        (
            "5 · parse and scorer identity",
            f"<p>parse status <strong>{_e(trial['parse_status'])}</strong>; prediction "
            f"<code>{_e(trial['prediction'])}</code></p><p>scorer "
            f"<code>{_e(protocol.scorer_id)}@{_e(protocol.scorer_version)}</code>; correctness "
            f"<strong>{_e(trial['correct'])}</strong></p>",
        ),
        ("6 · profile contribution", f"<p>{_e(profile_value)}</p>"),
    )
    return "".join(
        f"<article class=card><h3>{_e(label)}</h3>{value}</article>" for label, value in cards
    )


def _accuracy(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "not run"
    correct = sum(bool(row["correct"]) for row in rows)
    return f"{correct}/{len(rows)} ({correct / len(rows):.0%})"


def _coverage_matrices(
    suite: LoadedSuite,
    runs: list[RunManifest],
    trials: list[dict[str, Any]],
) -> tuple[str, str]:
    run_columns = sorted(runs, key=lambda run: (run.protocol_id, run.run_id))
    headings = "".join(f"<th>{_e(run.protocol_id)}</th>" for run in run_columns)
    dialect_rows = []
    for dialect_id, spec in sorted(suite.specs.items()):
        values = []
        for run in run_columns:
            rows = [
                row
                for row in trials
                if row["run_id"] == run.run_id and row["dialect_id"] == dialect_id
            ]
            values.append(f"<td>{_e(_accuracy(rows))}</td>")
        dialect_rows.append(
            f"<tr><td><code>{_e(dialect_id)}</code></td><td>{_e(spec.family)}</td>"
            f"{''.join(values)}</tr>"
        )
    families = sorted({spec.family for spec in suite.specs.values()})
    family_rows = []
    for family in families:
        dialect_ids = {
            dialect_id for dialect_id, spec in suite.specs.items() if spec.family == family
        }
        values = []
        for run in run_columns:
            rows = [
                row
                for row in trials
                if row["run_id"] == run.run_id and row["dialect_id"] in dialect_ids
            ]
            values.append(f"<td>{_e(_accuracy(rows))}</td>")
        family_rows.append(f"<tr><td>{_e(family)}</td>{''.join(values)}</tr>")
    dialect = (
        "<table><thead><tr><th>dialect</th><th>family</th>"
        f"{headings}</tr></thead><tbody>{''.join(dialect_rows)}</tbody></table>"
    )
    family = (
        f"<table><thead><tr><th>family</th>{headings}</tr></thead>"
        f"<tbody>{''.join(family_rows)}</tbody></table>"
    )
    return dialect, family


def _reasoning_contrasts(profiles: list[dict[str, Any]]) -> str:
    if not profiles:
        return "<p class=notice>not run: no admitted profile.</p>"
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for profile in profiles:
        key = (
            str(profile["execution_surface"]),
            str(profile["resolved_model_id"]),
            str(profile["protocol_id"]),
        )
        groups.setdefault(key, []).append(profile)
    rows = []
    for key, values in sorted(groups.items()):
        contrast = (
            "admitted contrast"
            if len({str(value["reasoning"]) for value in values}) > 1
            else "not run: no second reasoning setting"
        )
        for value in values:
            rows.append(
                "<tr>"
                f"<td>{_e(key[0])}</td><td>{_e(key[1])}</td><td>{_e(key[2])}</td>"
                f"<td><code>{_e(value['reasoning'])}</code></td>"
                f"<td>{_e(value.get('competence'))}</td>"
                f"<td>{_e(value.get('within_family_invariance'))}</td>"
                f"<td>{_e(value.get('mean_latency_ms'))}</td>"
                f"<td>{_e(value.get('input_tokens'))}/{_e(value.get('output_tokens'))}/"
                f"{_e(value.get('reasoning_tokens'))}</td><td>{_e(contrast)}</td></tr>"
            )
    return (
        "<table><thead><tr><th>surface</th><th>model</th><th>protocol</th><th>reasoning</th>"
        "<th>competence</th><th>invariance</th><th>mean latency ms</th>"
        "<th>input/output/reasoning tokens</th><th>contrast status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _runs_page(
    publication: PublicationView,
    profiles: list[dict[str, Any]],
    effects: list[dict[str, Any]],
) -> str:
    runs = list(publication.runs)
    suite = publication.suite
    trials = publication.trials
    grouped: dict[str, list[RunManifest]] = {"direct_api": [], "agent": []}
    for run in runs:
        grouped["direct_api" if run.execution_surface == "direct_api" else "agent"].append(run)

    def table(items: list[RunManifest]) -> str:
        if not items:
            return "<p class=notice>no admitted runs on this execution surface.</p>"
        rows = "".join(
            f"<tr><td><code>{_e(run.run_id)}</code></td>"
            f"<td>{_e(run.requested_model_id)}<br>{_e(run.resolved_model_id)}</td>"
            f"<td>{_e(run.protocol_id)}<br>{_e(run.dialect_id)}</td>"
            f"<td>{_e(run.execution_surface)}<br>{_e(run.provider)}<br>"
            f"<code>{_e(run.endpoint)}</code></td>"
            f"<td><code>{_e(json.dumps(run.reasoning, sort_keys=True))}</code></td>"
            f"<td>{run.latency_ms:.2f}</td><td>{run.token_usage.get('input_tokens', 0)}/"
            f"{run.token_usage.get('output_tokens', 0)}/"
            f"{run.token_usage.get('reasoning_tokens', 0)}</td>"
            f"<td>{run.cost_usd:.8f}<br>{_e(run.billing_channel)}</td>"
            f"<td>{_e(run.sdk_version)}</td></tr>"
            for run in items
        )
        return (
            "<table><thead><tr><th>run</th><th>requested/resolved model</th>"
            "<th>protocol/dialect</th><th>surface/provider/exact endpoint</th>"
            "<th>reasoning</th><th>latency ms</th><th>input/output/reasoning tokens</th>"
            "<th>cost usd/billing</th><th>sdk</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    profile_table = _records_table(
        profiles,
        (
            "resolved_model_id",
            "protocol_id",
            "competence",
            "within_family_invariance",
            "text_competence",
            "spatial_competence",
            "invalid_output_rate",
            "coverage",
        ),
    )
    effect_table = _records_table(
        effects,
        (
            "resolved_model_id",
            "protocol_id",
            "family",
            "plain_dialect_id",
            "treatment_dialect_id",
            "signed_paired_effect",
            "bootstrap_low",
            "bootstrap_high",
            "mcnemar_exact_p",
        ),
    )
    dialect_matrix, family_matrix = _coverage_matrices(suite, runs, trials)
    provenance_rows = []
    for run in runs:
        exact_provenance = {
            "provider": run.provider,
            "endpoint": run.endpoint,
            "routing_policy": run.routing_policy,
            "privacy_policy": run.privacy_policy,
            "catalog_retrieved_at": run.catalog_retrieved_at,
            "catalog_row": run.catalog_row,
        }
        provenance_rows.append(
            "<details><summary><code>"
            f"{_e(run.run_id)}</code> exact endpoint and routing provenance</summary><pre>"
            f"{_e(json.dumps(exact_provenance, indent=2, sort_keys=True))}"
            "</pre></details>"
        )
    provenance = "".join(provenance_rows)
    return (
        "<h1>models + runs</h1><p class=lede>direct api calls and agent-mediated runs "
        "are different "
        "experimental surfaces and are never silently pooled.</p>"
        "<div class=tabs><button data-tab=direct_api>direct api</button>"
        "<button data-tab=agent>agent</button></div>"
        f"<section id=direct_api>{table(grouped['direct_api'])}</section>"
        f"<section id=agent hidden>{table(grouped['agent'])}</section>"
        "<script>document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{"
        "document.querySelectorAll('main>section[id]').forEach(s=>s.hidden=s.id!==b.dataset.tab)})"
        "</script><h2>exact endpoint + routing provenance</h2>"
        f"{provenance}"
        "<h2>worked result path</h2><p>one admitted result traced from the frozen "
        "abstract form through its dialect payload and exact protocol to parsing and scoring.</p>"
        f"<section class=grid>{_worked_path(publication, runs, profiles)}</section>"
        "<h2>competence + invariance profiles</h2>"
        f"{profile_table}"
        "<h2>controlled dialect effects</h2>"
        f"{effect_table}"
        "<h2>dialect matrix</h2><p>every frozen dialect is explicit; absent cells say not run.</p>"
        f"{dialect_matrix}"
        "<h2>family matrix</h2><p>family aggregates are shown only where admitted trials exist.</p>"
        f"{family_matrix}"
        "<h2>reasoning contrasts</h2><p>competence, invariance, latency, and tokens stay "
        "separate across reasoning settings.</p>"
        f"{_reasoning_contrasts(profiles)}"
    )


def _human(suite: LoadedSuite, protocols: dict[str, ProtocolSpec], release_id: str) -> str:
    protocol_ids = list(protocols)
    form_ids = suite.form_sets["probe"]
    dialect_ids = sorted(suite.specs)
    chosen: list[dict[str, Any]] = []
    cell_map = {(c["abstract_form_id"], c["dialect_id"]): c for c in suite.cells}
    for index in range(len(protocol_ids) * 3):
        form_id = form_ids[index % len(form_ids)]
        dialect_id = dialect_ids[index % len(dialect_ids)]
        cell = cell_map[(form_id, dialect_id)]
        spec = suite.specs[dialect_id]
        protocol = protocols[protocol_ids[index % len(protocol_ids)]]
        chosen.append(
            {
                "trial": index + 1,
                "abstract_form_id": form_id,
                "dialect_id": dialect_id,
                "protocol_id": protocol.protocol_id,
                "answer_kind": protocol.answer_kind,
                "prompt": protocol.render_user_text(reading_rule=spec.reading_rule),
                "modality": cell["modality"],
                "payload": cell.get("model_payload"),
                "asset": f"assets/{cell['asset_path']}",
            }
        )
    payload = json.dumps(chosen, ensure_ascii=False).replace("</", "<\\/")
    return (
        f"<h1>human pilot</h1><p class=lede>{len(chosen)} local-only stimuli, balanced across "
        f"{len(protocol_ids)} frozen protocols. nothing is posted; export stays on your device.</p>"
        "<p class=notice>this informal pilot is not an admitted model run. use a pseudonymous "
        "participant code; no data leaves this page. every exported HumanTrialRecord conforms "
        'to the <a href="downloads/human-trial.schema.json">published schema</a>.</p>'
        "<div class=card><label>participant code <input id=participant required minlength=3 "
        'maxlength=64 pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}"></label> '
        "<label>laws of form familiarity <select id=familiarity><option value=none>none</option>"
        "<option value=some>some</option><option value=expert>expert</option>"
        "</select></label></div>"
        "<div id=pilot class=card></div><div class=answer id=answer></div>"
        "<label>confidence <select id=confidence><option value=low>low</option>"
        "<option value=medium selected>medium</option><option value=high>high</option>"
        "</select></label> "
        "<button id=next>save answer + next</button> "
        "<button id=download disabled>download json</button>"
        f"<script>const stimuli={payload};const release={json.dumps(release_id)};"
        "let i=0;let started=performance.now();const rows=[];"
        "const pilot=document.querySelector('#pilot'),answer=document.querySelector('#answer');"
        "function render(){if(i>=stimuli.length){pilot.textContent='pilot complete';"
        "answer.innerHTML='';document.querySelector('#next').disabled=true;"
        "document.querySelector('#download').disabled=false;return}"
        "const s=stimuli[i];pilot.innerHTML=`<p><strong>${s.trial} / ${stimuli.length}</strong> · "
        "<code>${s.protocol_id}</code> · <code>${s.dialect_id}</code></p><p>${s.prompt}</p>`+"
        "(s.modality!=='text'?`<div class=stimulus><img src=\"${s.asset}\" "
        'alt="frozen stimulus"></div>`:`<div class=stimulus><pre></pre></div>`);'
        "if(s.modality==='text')pilot.querySelector('pre').textContent=s.payload;"
        "answer.innerHTML=s.answer_kind==='normal_value'?"
        "'<button data-v=marked>marked</button> <button data-v=unmarked>unmarked</button>':"
        "'<textarea rows=6 cols=60 aria-label=transcription></textarea>';"
        "answer.querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>{"
        "answer.dataset.value=b.dataset.v})}document.querySelector('#next').onclick=()=>{"
        "const s=stimuli[i];const participant=document.querySelector('#participant').value.trim();"
        "const familiarity_band=document.querySelector('#familiarity').value;"
        "if(!/^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/.test(participant))return;"
        "const value=s.answer_kind==='normal_value'?"
        "answer.dataset.value:(answer.querySelector('textarea')?.value||'');"
        "if(!value)return;rows.push({participant_code:participant,familiarity_band,"
        "abstract_form_id:s.abstract_form_id,dialect_id:s.dialect_id,protocol_id:s.protocol_id,"
        "answer:s.answer_kind==='normal_value'?value:null,"
        "transcription:s.answer_kind!=='normal_value'?value:null,confidence:"
        "document.querySelector('#confidence').value,elapsed_ms:Math.round(performance.now()-started)});"
        "answer.dataset.value='';i++;started=performance.now();render()};"
        "document.querySelector('#download').onclick=()=>{"
        "const blob=new Blob([JSON.stringify({schema_version:1,release_id:release,records:rows},"
        "null,2)],{type:'application/json'});"
        "const a=document.createElement('a');a.href=URL.createObjectURL(blob);"
        "a.download='distinction-human-pilot.json';"
        "a.click();URL.revokeObjectURL(a.href)};render()</script>"
    )


def _downloads(publication: PublicationView) -> str:
    rows = "".join(
        f'<tr><td><a href="downloads/{_e(name)}">{_e(name)}</a></td>'
        f"<td>{(publication.root / name).stat().st_size}</td>"
        f"<td><code>{sha256((publication.root / name).read_bytes()).hexdigest()}</code></td></tr>"
        for name in _DOWNLOADS
    )
    citation = (
        f"distinction benchmark contributors ({publication.release_id}). "
        "distinction benchmark: laws of form representation invariance evaluation."
    )
    release_id = quote(publication.release_id, safe="")
    repository = urlsplit(publication.repository_url)
    if (
        repository.scheme != "https"
        or not repository.hostname
        or repository.username is not None
        or repository.password is not None
        or repository.query
        or repository.fragment
    ):
        raise RuntimeError("repository URL cannot define safe release asset links")
    repository_url = publication.repository_url.rstrip("/").removesuffix(".git")
    archive_name = f"distinction-bench-{publication.release_id}.tar.gz"
    manifest_name = f"distinction-bench-{publication.release_id}.release.json"
    asset_base = f"{repository_url}/releases/download/{release_id}"
    archive_url = f"{asset_base}/{quote(archive_name, safe='')}"
    manifest_url = f"{asset_base}/{quote(manifest_name, safe='')}"
    return (
        "<h1>downloads + citation</h1><p class=lede>the full sealed distribution and its "
        "embedded evidence exports are separate, explicit artifacts.</p>"
        "<h2>full sealed distribution</h2><ul>"
        f'<li><a href="{_e(archive_url)}">{_e(archive_name)}</a> — complete sealed bundle</li>'
        f'<li><a href="{_e(manifest_url)}">{_e(manifest_name)}</a> — byte-identical sealed '
        "<code>release.json</code> sidecar</li></ul>"
        "<p>the manifest is also the archive root and authenticates every in-bundle artifact. "
        "both distribution files are published after sealing; embedding either the final "
        "manifest in its checksummed site or the archive inside that bundle would recurse.</p>"
        "<h2>embedded evidence exports</h2><p>the selected files below are copied verbatim from "
        "the bundle used to build these pages; this table is not the complete sealed bundle.</p>"
        f"<table><thead><tr><th>artifact</th><th>bytes</th><th>sha256</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<h2>caveats</h2><ul><li>sample releases are protocol smoke tests, not rankings.</li>"
        "<li>untaught runs combine dialect inference with task performance.</li>"
        "<li>image runs include raster perception and layout as possible confounds.</li>"
        "<li>checksums above cover exact embedded evidence exports; the external sealed "
        "manifest covers every in-bundle artifact.</li></ul>"
        f"<h2>suggested citation</h2><pre>{_e(citation)}</pre>"
        "<p>code is mit licensed. the frozen suite, stimuli, documentation, and site content are "
        "licensed cc by 4.0.</p>"
    )


def build_site(publication: PublicationView, out: Path) -> None:
    try:
        out.resolve().relative_to((publication.root / "site").resolve())
        inside_bundle_site = True
    except ValueError:
        inside_bundle_site = False
    if publication.status != "sealed" and not inside_bundle_site:
        raise RuntimeError("a working bundle may only build its own site/ directory")
    if out.exists():
        if any(out.iterdir()):
            raise FileExistsError(f"site output is not empty: {out}")
    else:
        out.mkdir(parents=True)

    suite = publication.suite
    protocols = publication.protocols
    profiles = list(publication.profiles)
    effects = list(publication.effects)
    _write(
        out / "index.html",
        _page("what is tested", _overview(publication, suite, protocols)),
    )
    _write(out / "forms.html", _page("forms + protocols", _forms(suite, protocols)))
    _write(out / "atlas.html", _page("dialect atlas", _atlas(out, suite)))
    _write(
        out / "runs.html",
        _page("models + runs", _runs_page(publication, profiles, effects)),
    )
    _write(
        out / "human.html",
        _page("human pilot", _human(suite, protocols, publication.release_id)),
    )
    _write(
        out / "downloads.html",
        _page("downloads + citation", _downloads(publication)),
    )
    downloads = out / "downloads"
    downloads.mkdir()
    for name in _DOWNLOADS:
        shutil.copyfile(publication.root / name, downloads / name)
    if publication.stimuli_materialized:
        shutil.copytree(publication.root / "stimuli", out / "assets" / "stimuli")
    (out / "CNAME").write_text("distinction.valeriekim.ca\n")

"""Build the public, bundle-only static benchmark gallery."""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from lofbench.protocols import ProtocolSpec, load_protocol_registry
from lofbench.records import RunManifest
from lofbench.release_bundle import ReleaseBundle
from lofbench.suites import LoadedSuite, load_suite

_DOWNLOADS = (
    "suite.json",
    "protocols.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "profiles.parquet",
    "effects.parquet",
    "transcripts.jsonl",
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
    return "<nav>" + "".join(
        f'<a href="{prefix}{href}">{_e(label)}</a>' for href, label in links
    ) + "</nav>"


def _page(title: str, body: str, *, prefix: str = "") -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'self\'; img-src \'self\' data:; style-src \'unsafe-inline\'; '
        'script-src \'unsafe-inline\'; connect-src \'none\'; form-action \'none\'">'
        f"<title>{_e(title)} · distinction benchmark</title><style>{_CSS}</style></head><body>"
        f"<header>{_nav(prefix)}</header><main>{body}</main>"
        "<footer>distinction benchmark · code: mit · suite and site content: cc by 4.0"
        "</footer></body></html>"
    )


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def _overview(bundle: ReleaseBundle, suite: LoadedSuite, protocols: dict[str, ProtocolSpec]) -> str:
    runs = bundle.runs()
    trials = pq.read_table(bundle.root / "trials.parquet").num_rows
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
        f'<div class=card><div class=metric>{number}</div><div>{_e(label)}</div></div>'
        for number, label in metrics
    )
    status = _e(bundle.manifest["status"])
    return (
        "<h1>distinction benchmark</h1>"
        '<p class=lede>an inspectable benchmark of whether a model can preserve laws of form '
        "structure and reduce it across genuinely different representational dialects.</p>"
        f'<p class=notice>release <strong>{_e(bundle.manifest["release_id"])}</strong> is '
        f'<strong>{status}</strong>. results shown here are only admitted bundle records.</p>'
        f'<section class=grid>{cards}</section><h2>the claim</h2>'
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
    protocol_cards = "".join(
        "<article class=card>"
        f"<h3><code>{_e(spec.protocol_id)}</code></h3>"
        f"<p>{_e(spec.user_template)}</p>"
        f"<p class=muted>answer: {_e(spec.answer_kind)} · scorer: {_e(spec.scorer_id)}@"
        f"{_e(spec.scorer_version)} · dialect legend: {_e(spec.includes_dialect_legend)}</p>"
        "<details><summary>exact response schema</summary>"
        f"<pre>{_e(json.dumps(spec.response_json_schema, indent=2))}</pre></details>"
        "</article>"
        for spec in protocols.values()
    )
    return (
        "<h1>forms + protocols</h1><p class=lede>the abstract form, visual dialect, task protocol, "
        "model endpoint, and execution surface are separate frozen variables.</p>"
        "<h2>balanced form table</h2><table><thead><tr><th>difficulty</th><th>forms</th>"
        f"<th>marked</th><th>unmarked</th></tr></thead><tbody>{rows}</tbody></table>"
        f"<h2>protocol registry</h2><section class=grid>{protocol_cards}</section>"
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
        f'<div class=stimulus>{stimulus}</div><p class=muted>sha256 '
        f"<code>{_e(cell['model_payload_sha256'])}</code></p></details>"
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
            f'<section class=dialect-cells>{cell_markup}</section>'
        )
        _write(out / "dialects" / f"{dialect_id}.html", _page(spec.label, body, prefix="../"))
    return (
        "<h1>dialect atlas</h1><p class=lede>every dialect declares its reading rule, provenance, "
        "modality, limits, structural family, and exact frozen payload hashes.</p>"
        f'<section class=grid>{"".join(cards)}</section>'
    )


def _runs_page(bundle: ReleaseBundle) -> str:
    runs = bundle.runs()
    grouped: dict[str, list[RunManifest]] = {"direct_api": [], "agent": []}
    for run in runs:
        grouped.setdefault(run.execution_surface, []).append(run)

    def table(items: list[RunManifest]) -> str:
        if not items:
            return '<p class=notice>no admitted runs on this execution surface.</p>'
        rows = "".join(
            f"<tr><td><code>{_e(run.run_id)}</code></td><td>{_e(run.resolved_model_id)}</td>"
            f"<td>{_e(run.protocol_id)}</td><td>{len(run.expected_trial_ids)}</td>"
            f"<td>{run.cost_usd:.8f}</td><td>{_e(run.provider)}</td></tr>"
            for run in items
        )
        return (
            "<table><thead><tr><th>run</th><th>resolved model</th><th>protocol</th>"
            "<th>trials</th><th>cost usd</th><th>provider</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    return (
        "<h1>models + runs</h1><p class=lede>direct api calls and agent-mediated runs "
        "are different "
        "experimental surfaces and are never silently pooled.</p>"
        '<div class=tabs><button data-tab=direct_api>direct api</button>'
        '<button data-tab=agent>agent</button></div>'
        f'<section id=direct_api>{table(grouped["direct_api"])}</section>'
        f'<section id=agent hidden>{table(grouped["agent"])}</section>'
        "<script>document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{"
        "document.querySelectorAll('main>section[id]').forEach(s=>s.hidden=s.id!==b.dataset.tab)})"
        "</script>"
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
        '<p class=notice>this informal pilot is not an admitted model run and collects no '
        "identity data.</p>"
        '<div id=pilot class=card></div><div class=answer id=answer></div>'
        '<button id=next>save answer + next</button> '
        '<button id=download disabled>download json</button>'
        f"<script>const stimuli={payload};const release={json.dumps(release_id)};"
        "let i=0;const rows=[];"
        "const pilot=document.querySelector('#pilot'),answer=document.querySelector('#answer');"
        "function render(){if(i>=stimuli.length){pilot.textContent='pilot complete';"
        "answer.innerHTML='';document.querySelector('#next').disabled=true;"
        "document.querySelector('#download').disabled=false;return}"
        "const s=stimuli[i];pilot.innerHTML=`<p><strong>${s.trial} / ${stimuli.length}</strong> · "
        "<code>${s.protocol_id}</code> · <code>${s.dialect_id}</code></p><p>${s.prompt}</p>`+"
        "(s.modality!=='text'?`<div class=stimulus><img src=\"${s.asset}\" "
        "alt=\"frozen stimulus\"></div>`:`<div class=stimulus><pre></pre></div>`);"
        "if(s.modality==='text')pilot.querySelector('pre').textContent=s.payload;"
        "answer.innerHTML=s.protocol_id.startsWith('reduce')?"
        "'<button data-v=marked>marked</button> <button data-v=unmarked>unmarked</button>':"
        "'<textarea rows=6 cols=60 aria-label=transcription></textarea>';"
        "answer.querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>{"
        "answer.dataset.value=b.dataset.v})}document.querySelector('#next').onclick=()=>{"
        "const s=stimuli[i];const value=s.protocol_id.startsWith('reduce')?"
        "answer.dataset.value:(answer.querySelector('textarea')?.value||'');"
        "if(!value)return;rows.push({...s,response:value});answer.dataset.value='';"
        "i++;render()};document.querySelector('#download').onclick=()=>{"
        "const blob=new Blob([JSON.stringify({schema_version:1,release_id:release,"
        "responses:rows},null,2)],{type:'application/json'});"
        "const a=document.createElement('a');a.href=URL.createObjectURL(blob);"
        "a.download='distinction-human-pilot.json';"
        "a.click();URL.revokeObjectURL(a.href)};render()</script>"
    )


def _downloads(bundle: ReleaseBundle) -> str:
    rows = "".join(
        f'<tr><td><a href="downloads/{_e(name)}">{_e(name)}</a></td>'
        f"<td>{(bundle.root / name).stat().st_size}</td></tr>"
        for name in _DOWNLOADS
    )
    citation = (
        f"distinction benchmark contributors ({bundle.manifest['release_id']}). "
        "distinction benchmark: laws of form representation invariance evaluation."
    )
    return (
        "<h1>downloads + citation</h1><p class=lede>machine-readable release artifacts are copied "
        "verbatim from the bundle used to build these pages.</p>"
        f"<table><thead><tr><th>artifact</th><th>bytes</th></tr></thead><tbody>{rows}</tbody></table>"
        f"<h2>suggested citation</h2><pre>{_e(citation)}</pre>"
        "<p>code is mit licensed. the frozen suite, stimuli, documentation, and site content are "
        "licensed cc by 4.0.</p>"
    )


def build_site(release_dir: Path, out: Path) -> None:
    bundle = ReleaseBundle.open(release_dir)
    bundle.validate()
    try:
        out.resolve().relative_to((bundle.root / "site").resolve())
        inside_bundle_site = True
    except ValueError:
        inside_bundle_site = False
    if bundle.manifest["status"] != "sealed" and not inside_bundle_site:
        raise RuntimeError("a working bundle may only build its own site/ directory")
    if out.exists():
        if any(out.iterdir()):
            raise FileExistsError(f"site output is not empty: {out}")
    else:
        out.mkdir(parents=True)

    suite = load_suite(version=bundle.manifest["suite_version"], path=bundle.root / "suite.json")
    protocols = load_protocol_registry(bundle.root / "protocols.json")
    _write(out / "index.html", _page("what is tested", _overview(bundle, suite, protocols)))
    _write(out / "forms.html", _page("forms + protocols", _forms(suite, protocols)))
    _write(out / "atlas.html", _page("dialect atlas", _atlas(out, suite)))
    _write(out / "runs.html", _page("models + runs", _runs_page(bundle)))
    _write(
        out / "human.html",
        _page("human pilot", _human(suite, protocols, bundle.manifest["release_id"])),
    )
    _write(out / "downloads.html", _page("downloads + citation", _downloads(bundle)))
    downloads = out / "downloads"
    downloads.mkdir()
    for name in _DOWNLOADS:
        shutil.copyfile(bundle.root / name, downloads / name)
    if bundle.manifest.get("stimuli_materialized"):
        shutil.copytree(bundle.root / "stimuli", out / "assets" / "stimuli")
    (out / "CNAME").write_text("distinction.valeriekim.ca\n")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build the bundle-only static benchmark site")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    build_site(args.release, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

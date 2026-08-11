"""Build the public, bundle-only static benchmark gallery."""

from __future__ import annotations

import html
import json
import re
import shutil
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from lofbench.protocols import ProtocolSpec
from lofbench.publication import scan_publication
from lofbench.release_bundle import PublicationView
from lofbench.renderers.pipeline.spec import DialectSpec
from lofbench.suites import LoadedSuite

SAFE_DOWNLOADS = (
    "suite.json",
    "protocols.json",
    "human-trial.schema.json",
    "profiles.parquet",
    "effects.parquet",
)
_ROOT_FILES = {
    "index.html",
    "forms.html",
    "atlas.html",
    "runs.html",
    "human.html",
    "downloads.html",
    "CNAME",
}
_FORBIDDEN_PATH_NAMES = {
    "release.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "transcripts.jsonl",
    "request-started.jsonl",
    "ledger.jsonl",
}
_FORBIDDEN_HTML_MARKERS = (
    "/releases/download/",
    ".tar.gz",
    ".zip",
    "release.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "transcripts.jsonl",
    "request-started.jsonl",
    "ledger.jsonl",
    "exact endpoint",
    "catalog_row",
    "routing_policy",
    "provider_evidence",
    "provider_request_id",
    "response_text",
    "prompt_hash",
    "completion_evidence_sha256",
    "call_id",
    "trial_id",
    "run_id",
)
_OPAQUE_EXECUTION_ID = re.compile(rb"(?:run|trial|call)_[0-9a-f]{16,}")
_EXEMPLAR_DIFFICULTY = "2. medium"

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
.dialect-cells details{margin:.5rem 0}.exemplar .stimulus{min-height:9rem;max-height:18rem;
overflow:auto}.exemplar h3{margin-bottom:.25rem}
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


class _LocalReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.references.extend(
            value for name, value in attrs if name in {"href", "src"} and value is not None
        )


def _spatial_assets(publication: PublicationView) -> dict[str, str]:
    cells = [cell for cell in publication.suite.cells if cell["modality"] != "text"]
    assets = {f"assets/{cell['asset_path']}": cell["model_payload_sha256"] for cell in cells}
    if len(assets) != len(cells) or any(
        not path.startswith("assets/stimuli/image/")
        or ".." in Path(path).parts
        or Path(path).is_absolute()
        for path in assets
    ):
        raise RuntimeError("frozen spatial cells do not define unique safe image assets")
    return assets


def _expected_site_files(publication: PublicationView) -> set[str]:
    dialects = {f"dialects/{dialect_id}.html" for dialect_id in publication.suite.specs}
    downloads = {f"downloads/{name}" for name in SAFE_DOWNLOADS}
    return _ROOT_FILES | dialects | downloads | set(_spatial_assets(publication))


def _expected_site_directories(expected_files: set[str]) -> set[str]:
    directories: set[str] = set()
    for relative in expected_files:
        parent = Path(relative).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _verify_local_references(root: Path, html_paths: list[Path]) -> None:
    for path in html_paths:
        parser = _LocalReferenceParser()
        parser.feed(path.read_text())
        for reference in parser.references:
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                if parsed.scheme not in {"http", "https", "data"}:
                    raise RuntimeError(f"unsafe site reference in {path.name}: {reference}")
                continue
            local = unquote(parsed.path)
            if not local:
                continue
            if "\\" in local or local.startswith("/"):
                raise RuntimeError(f"unsafe local site reference in {path.name}: {reference}")
            target = (path.parent / local).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise RuntimeError(
                    f"local site reference escapes output in {path.name}: {reference}"
                ) from exc
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                raise RuntimeError(f"broken local site reference in {path.name}: {reference}")


def _verify_forbidden_content(publication: PublicationView, html_paths: list[Path]) -> None:
    run_values = {
        value for run in publication.runs for value in (run.run_id, run.endpoint) if value
    }
    trial_values = {
        str(row[field])
        for row in publication.trials
        for field in (
            "trial_id",
            "run_id",
            "prompt_hash",
            "response_text",
            "completion_evidence_sha256",
        )
        if row.get(field)
    }
    private_values = {value.encode() for value in run_values | trial_values if len(value) >= 8}
    for path in html_paths:
        payload = path.read_bytes()
        unescaped = html.unescape(payload.decode()).encode()
        lowered = unescaped.lower()
        marker = next(
            (value for value in _FORBIDDEN_HTML_MARKERS if value.encode() in lowered), None
        )
        if marker is not None:
            raise RuntimeError(f"forbidden public output marker in {path.name}: {marker}")
        if _OPAQUE_EXECUTION_ID.search(unescaped) or any(
            value in unescaped for value in private_values
        ):
            raise RuntimeError(f"private execution detail appears in {path.name}")


def verify_public_site(publication: PublicationView, root: Path) -> None:
    """Require the exact public projection, resolvable links, and no private evidence."""
    if not root.is_dir():
        raise RuntimeError("public site output is not a directory")
    expected_files = _expected_site_files(publication)
    expected_directories = _expected_site_directories(expected_files)
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"public site contains a symlink: {relative}")
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_directories.add(relative)
        else:
            raise RuntimeError(f"public site contains a special file: {relative}")
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unexpected = sorted(actual_files - expected_files)
        raise RuntimeError(
            f"public site file set is not exact: missing={missing}, unexpected={unexpected}"
        )
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        unexpected = sorted(actual_directories - expected_directories)
        raise RuntimeError(
            f"public site directory set is not exact: missing={missing}, unexpected={unexpected}"
        )
    if any(Path(relative).name in _FORBIDDEN_PATH_NAMES for relative in actual_files):
        raise RuntimeError("public site contains a forbidden evidence artifact")
    for name in SAFE_DOWNLOADS:
        if (root / "downloads" / name).read_bytes() != (publication.root / name).read_bytes():
            raise RuntimeError(f"public download does not match validated bundle input: {name}")
    for relative, digest in _spatial_assets(publication).items():
        payload = (root / relative).read_bytes()
        if sha256(payload).hexdigest() != digest:
            raise RuntimeError(f"public spatial asset does not match frozen cell: {relative}")
    html_paths = sorted(root.rglob("*.html"))
    _verify_local_references(root, html_paths)
    _verify_forbidden_content(publication, html_paths)
    scan_publication(root)


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
    exemplars = _dialect_exemplar_cards(suite)
    caveat = (
        "this is a four-protocol by five-form smoke test, not a model ranking."
        if publication.sample_contract
        else "results shown here are aggregate views of admitted bundle records."
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
        "<h2>every dialect, one frozen form</h2>"
        "<p>the same medium probe form is rendered once in each documented dialect; open a card "
        "for its reading rule, provenance, limitations, and all 400 frozen cells.</p>"
        f"<section class=grid>{exemplars}</section>"
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


def _stimulus_markup(cell: dict[str, Any], *, prefix: str = "") -> str:
    if cell["modality"] != "text":
        return (
            f'<img loading=lazy src="{prefix}assets/{_e(cell["asset_path"])}" '
            f'alt="frozen stimulus {_e(cell["abstract_form_id"])}">'
        )
    return f"<pre>{_e(cell['model_payload'])}</pre>"


def _cell_markup(cell: dict[str, Any], *, prefix: str = "") -> str:
    stimulus = _stimulus_markup(cell, prefix=prefix)
    return (
        f"<details><summary><code>{_e(cell['abstract_form_id'])}</code></summary>"
        f"<div class=stimulus>{stimulus}</div><p class=muted>sha256 "
        f"<code>{_e(cell['model_payload_sha256'])}</code><br>symbolic hash "
        f"<code>{_e(cell['symbolic_payload_hash'])}</code></p></details>"
    )


def _dialect_exemplars(suite: LoadedSuite) -> tuple[str, dict[str, dict[str, Any]]]:
    probe_ids = set(suite.form_sets["probe"])
    candidates = [
        form["abstract_form_id"]
        for form in suite.forms
        if form["abstract_form_id"] in probe_ids and form["difficulty"] == _EXEMPLAR_DIFFICULTY
    ]
    if len(candidates) != 1:
        raise RuntimeError("probe set must contain exactly one medium exemplar form")
    exemplar_form_id = candidates[0]
    cells = {
        cell["dialect_id"]: cell
        for cell in suite.cells
        if cell["abstract_form_id"] == exemplar_form_id
    }
    if set(cells) != set(suite.specs):
        raise RuntimeError("dialect exemplars do not cover the frozen registry")
    return exemplar_form_id, cells


def _dialect_exemplar_card(
    *,
    dialect_id: str,
    spec: DialectSpec,
    cell: dict[str, Any],
    exemplar_form_id: str,
    cell_count: int | None,
) -> str:
    description = f"<p>{_e(spec.description)}</p>" if cell_count is not None else ""
    count = f" · {cell_count} cells" if cell_count is not None else ""
    link_text = (
        "inspect every frozen cell" if cell_count is not None else "reading rule + all frozen cells"
    )
    return (
        '<article class="card exemplar" '
        f'data-dialect-exemplar="{_e(dialect_id)}">'
        f"<h3>{_e(spec.label)}</h3><p><code>{_e(dialect_id)}</code></p>"
        f"<div class=stimulus>{_stimulus_markup(cell)}</div>"
        f"<p class=muted>same frozen form · <code>{_e(exemplar_form_id)}</code></p>"
        f"{description}<p class=muted>{_e(spec.modality)} · {_e(spec.family)} · "
        f"{_e(spec.archetype)}{count}</p>"
        f'<a href="dialects/{_e(dialect_id)}.html">{link_text}</a></article>'
    )


def _dialect_exemplar_cards(suite: LoadedSuite) -> str:
    exemplar_form_id, cells = _dialect_exemplars(suite)
    return "".join(
        _dialect_exemplar_card(
            dialect_id=dialect_id,
            spec=spec,
            cell=cells[dialect_id],
            exemplar_form_id=exemplar_form_id,
            cell_count=None,
        )
        for dialect_id, spec in sorted(suite.specs.items())
    )


def _atlas(out: Path, suite: LoadedSuite) -> str:
    cells_by_dialect: dict[str, list[dict[str, Any]]] = {}
    for cell in suite.cells:
        cells_by_dialect.setdefault(cell["dialect_id"], []).append(cell)
    cards = []
    exemplar_form_id, exemplars = _dialect_exemplars(suite)
    for dialect_id, spec in suite.specs.items():
        cells = cells_by_dialect[dialect_id]
        cards.append(
            _dialect_exemplar_card(
                dialect_id=dialect_id,
                spec=spec,
                cell=exemplars[dialect_id],
                exemplar_form_id=exemplar_form_id,
                cell_count=len(cells),
            )
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


def _profile_aggregate(profile: dict[str, Any], field: str) -> dict[str, float]:
    value = profile.get(field)
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"published profile has invalid {field}") from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or isinstance(child, bool) or not isinstance(child, (int, float))
        for key, child in parsed.items()
    ):
        raise RuntimeError(f"published profile has invalid {field}")
    return {key: float(child) for key, child in parsed.items()}


def _profile_label(profile: dict[str, Any]) -> str:
    return f"{profile['resolved_model_id']} · {profile['protocol_id']}"


def _coverage_matrices(
    suite: LoadedSuite,
    profiles: list[dict[str, Any]],
) -> tuple[str, str]:
    columns = sorted(
        profiles,
        key=lambda row: (
            str(row["execution_surface"]),
            str(row["resolved_model_id"]),
            str(row["protocol_id"]),
            str(row["reasoning"]),
        ),
    )
    headings = "".join(f"<th>{_e(_profile_label(profile))}</th>" for profile in columns)
    dialect_aggregates = [_profile_aggregate(profile, "dialect_accuracy") for profile in columns]
    family_aggregates = [_profile_aggregate(profile, "family_accuracy") for profile in columns]
    dialect_rows = []
    for dialect_id, spec in sorted(suite.specs.items()):
        values = [
            "<td>not run</td>"
            if dialect_id not in aggregate
            else f"<td>{aggregate[dialect_id]:.0%}</td>"
            for aggregate in dialect_aggregates
        ]
        dialect_rows.append(
            f"<tr><td><code>{_e(dialect_id)}</code></td><td>{_e(spec.family)}</td>"
            f"{''.join(values)}</tr>"
        )
    families = sorted({spec.family for spec in suite.specs.values()})
    family_rows = []
    for family in families:
        values = [
            "<td>not run</td>" if family not in aggregate else f"<td>{aggregate[family]:.0%}</td>"
            for aggregate in family_aggregates
        ]
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
                f"{_e(value.get('reasoning_tokens'))}</td>"
                f"<td>{float(value.get('cost_usd') or 0):.8f}</td>"
                f"<td>{_e(contrast)}</td></tr>"
            )
    return (
        "<table><thead><tr><th>surface</th><th>model</th><th>protocol</th><th>reasoning</th>"
        "<th>competence</th><th>invariance</th><th>mean latency ms</th>"
        "<th>input/output/reasoning tokens</th><th>cost usd</th>"
        "<th>contrast status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _runs_page(
    publication: PublicationView,
    profiles: list[dict[str, Any]],
    effects: list[dict[str, Any]],
) -> str:
    suite = publication.suite
    profile_table = _records_table(
        profiles,
        (
            "execution_surface",
            "requested_model_id",
            "resolved_model_id",
            "protocol_id",
            "reasoning",
            "competence",
            "within_family_invariance",
            "text_competence",
            "spatial_competence",
            "invalid_output_rate",
            "coverage",
            "observed_trials",
            "expected_trials",
        ),
    )
    resource_table = _records_table(
        profiles,
        (
            "execution_surface",
            "resolved_model_id",
            "protocol_id",
            "mean_latency_ms",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cost_usd",
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
    dialect_matrix, family_matrix = _coverage_matrices(suite, profiles)
    scope = (
        "this sample is a four-protocol by five-form smoke test (twenty aggregate "
        "observations), not a model ranking."
        if publication.sample_contract
        else "all tables are aggregates recomputed from admitted bundle records."
    )
    return (
        "<h1>models + aggregate results</h1><p class=lede>api and agent-mediated execution "
        "surfaces remain separate experimental conditions. only recomputed aggregate rows "
        "are published here.</p>"
        f"<p class=notice>{_e(scope)}</p>"
        "<h2>how these rows are derived</h2><p>validated scored observations are grouped by "
        "model, protocol, execution surface, and reasoning setting. competence balances "
        "families; coverage compares observed with planned observations; invariance measures "
        "agreement across representations. individual exchanges are not part of this site.</p>"
        "<h2>competence + invariance profiles</h2>"
        f"{profile_table}"
        "<h2>aggregate resource use</h2>"
        f"{resource_table}"
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
        for name in SAFE_DOWNLOADS
    )
    citation = (
        f"distinction benchmark contributors ({publication.release_id}). "
        "distinction benchmark: laws of form representation invariance evaluation."
    )
    return (
        "<h1>downloads + citation</h1><p class=lede>these public files define the test and "
        "support the aggregate claims displayed on this website.</p>"
        "<p class=notice>the complete audit authority remains offline. individual execution "
        "records and provider exchanges are deliberately not published.</p>"
        "<h2>public benchmark data</h2><p>each file below is copied byte for byte from the "
        "validated local authority used to build this projection.</p>"
        f"<table><thead><tr><th>artifact</th><th>bytes</th><th>sha256</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<h2>caveats</h2><ul><li>sample releases are protocol smoke tests, not rankings.</li>"
        "<li>untaught runs combine dialect inference with task performance.</li>"
        "<li>image runs include raster perception and layout as possible confounds.</li>"
        "<li>the checksums above cover the exact public data files shown here.</li></ul>"
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
    if not publication.stimuli_materialized:
        raise RuntimeError("public site generation requires all frozen stimuli materialized")
    if out.exists():
        if any(out.iterdir()):
            raise FileExistsError(f"site output is not empty: {out}")
    else:
        out.mkdir(parents=True)

    suite = publication.suite
    protocols = publication.protocols
    profiles = [dict(row) for row in publication.profiles]
    effects = [dict(row) for row in publication.effects]
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
    for name in SAFE_DOWNLOADS:
        shutil.copyfile(publication.root / name, downloads / name)
    for relative in sorted(_spatial_assets(publication)):
        source = publication.root / relative.removeprefix("assets/")
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (out / "CNAME").write_text("distinction.valeriekim.ca\n")
    verify_public_site(publication, out)

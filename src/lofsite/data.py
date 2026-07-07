"""Location, presence, and schema checks for DB-5's tidy parquet/jsonl artifacts.

DB-6 (this site) depends on DB-5 (the results data pipeline,
``lofbench.pipeline``) for the headline chart, model-by-dialect matrix, and
transcript walkthroughs. DB-5 owns the artifact schema; this module owns
*whether the site can find and trust a file*, so that pages depending on it
degrade to a clear placeholder instead of crashing (or silently rendering
from the wrong columns) when the artifact is absent or stamped with an
unexpected schema version.

Phase-2 fix (DB-8 planner finding): phase 1 guessed at a
``headline.parquet`` artifact the pipeline never writes. The real contract,
read straight from ``lofbench.pipeline.run_pipeline``/``write_tidy``/
``write_transcripts``, is:

    <out_dir>/<suite_version>/items.parquet
    <out_dir>/<suite_version>/calls.parquet
    <out_dir>/<suite_version>/sensitivity.parquet
    <out_dir>/<suite_version>/transcripts.jsonl

``LOFSITE_DATA_DIR`` is the ``<out_dir>`` a deploy step points at (wherever
DB-5 last wrote); ``LOFSITE_SUITE_VERSION`` picks which ``<suite_version>``
subdirectory to serve. Every row DB-5 writes carries its own
``schema_version`` column (not just the file, so a partial rewrite or a
concat of two runs is caught too) -- ``load_*`` below refuses to hand back a
DataFrame whose stamped version doesn't match what this site was written
against, so a DB-5 schema change fails loudly (a visible mismatch message)
rather than silently rendering stale or missing columns.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from lofbench.pipeline import SCHEMA_VERSION as EXPECTED_SCHEMA_VERSION

# Overridable via env var so a deploy step can point at wherever DB-5 writes,
# and at which suite_version subdirectory to serve.
DATA_DIR = Path(os.environ.get("LOFSITE_DATA_DIR", "data/site-artifacts"))
SUITE_VERSION = os.environ.get("LOFSITE_SUITE_VERSION", "pilot-v0")

ITEMS_ARTIFACT = "items.parquet"
CALLS_ARTIFACT = "calls.parquet"
SENSITIVITY_ARTIFACT = "sensitivity.parquet"
TRANSCRIPTS_ARTIFACT = "transcripts.jsonl"


@dataclass(frozen=True)
class ArtifactStatus:
    """Whether a DB-5 artifact is present, and where it was looked for.

    Presence-only (no parsing, no schema check) -- cheap enough to call on
    every page render. Callers that need the data itself go through the
    ``load_*`` functions below, which do parse and do check schema_version.
    """

    available: bool
    path: Path


def _suite_dir(data_dir: Path | None) -> Path:
    """Resolve the directory actually containing the artifacts.

    When a caller passes ``data_dir`` explicitly (tests, mostly), it is used
    literally as the artifact directory -- no ``suite_version`` join -- so
    existing phase-1 call sites and tests that pass a bare tmp_path keep
    working unchanged. The default (no override) is
    ``DATA_DIR / SUITE_VERSION``, matching the pipeline's actual layout.
    """
    return data_dir if data_dir is not None else (DATA_DIR / SUITE_VERSION)


def _status(filename: str, data_dir: Path | None) -> ArtifactStatus:
    path = _suite_dir(data_dir) / filename
    return ArtifactStatus(available=path.is_file(), path=path)


def items_status(data_dir: Path | None = None) -> ArtifactStatus:
    """Check whether DB-5's per-sample items artifact is present."""
    return _status(ITEMS_ARTIFACT, data_dir)


def calls_status(data_dir: Path | None = None) -> ArtifactStatus:
    """Check whether DB-5's per-API-call artifact is present."""
    return _status(CALLS_ARTIFACT, data_dir)


def sensitivity_status(data_dir: Path | None = None) -> ArtifactStatus:
    """Check whether DB-5's paired-comparison sensitivity artifact is present."""
    return _status(SENSITIVITY_ARTIFACT, data_dir)


def transcripts_status(data_dir: Path | None = None) -> ArtifactStatus:
    """Check whether DB-5's transcript-extract artifact is present."""
    return _status(TRANSCRIPTS_ARTIFACT, data_dir)


@dataclass(frozen=True)
class LoadResult:
    """A loaded artifact, or a plain-English reason it could not be used.

    ``frame`` is ``None`` whenever ``error`` is set: a missing file, a parse
    failure, or a schema_version mismatch all refuse to hand back a
    DataFrame the caller might otherwise trust. Chart pages degrade to a
    placeholder built from ``error`` instead of rendering partial/stale data.
    """

    frame: pd.DataFrame | None
    error: str | None


def _check_schema_version(versions: Any, artifact_name: str) -> str | None:
    """Plain-English mismatch message, or None if every row matches.

    DB-5 stamps ``schema_version`` per row (see the module docstring), so
    this checks the full distinct set rather than just the first row -- a
    partial rewrite or an accidental concat of two pipeline versions is
    caught, not just a whole-file version bump.
    """
    distinct = {v for v in versions if v is not None}
    if not distinct:
        return None
    if distinct != {EXPECTED_SCHEMA_VERSION}:
        found = ", ".join(repr(v) for v in sorted(str(v) for v in distinct))
        return (
            f"{artifact_name} schema_version mismatch: this site expects "
            f"{EXPECTED_SCHEMA_VERSION!r}, found {{{found}}}. Refusing to "
            "render from an artifact written by a different lofbench.pipeline "
            "schema -- re-run the pipeline with a matching lofbench checkout, "
            "or update lofsite to the new schema."
        )
    return None


def _load_parquet(status: ArtifactStatus, artifact_name: str) -> LoadResult:
    if not status.available:
        return LoadResult(frame=None, error=f"not found at {status.path}")
    try:
        df = pd.read_parquet(status.path)
    except Exception as e:  # noqa: BLE001 - report, never crash the page
        return LoadResult(frame=None, error=f"failed to read {status.path}: {e}")
    err = _check_schema_version(df.get("schema_version", pd.Series(dtype=object)), artifact_name)
    if err:
        return LoadResult(frame=None, error=err)
    return LoadResult(frame=df, error=None)


def load_items(data_dir: Path | None = None) -> LoadResult:
    """Load ``items.parquet``, schema_version-checked."""
    return _load_parquet(items_status(data_dir), ITEMS_ARTIFACT)


def load_calls(data_dir: Path | None = None) -> LoadResult:
    """Load ``calls.parquet``, schema_version-checked."""
    return _load_parquet(calls_status(data_dir), CALLS_ARTIFACT)


def load_sensitivity(data_dir: Path | None = None) -> LoadResult:
    """Load ``sensitivity.parquet``, schema_version-checked."""
    return _load_parquet(sensitivity_status(data_dir), SENSITIVITY_ARTIFACT)


def load_transcripts(data_dir: Path | None = None) -> LoadResult:
    """Load ``transcripts.jsonl`` (one JSON object per line), schema_version-checked."""
    status = transcripts_status(data_dir)
    if not status.available:
        return LoadResult(frame=None, error=f"not found at {status.path}")
    records: list[dict[str, Any]] = []
    try:
        with status.path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception as e:  # noqa: BLE001 - report, never crash the page
        return LoadResult(frame=None, error=f"failed to read {status.path}: {e}")
    versions = (r.get("schema_version") for r in records)
    err = _check_schema_version(versions, TRANSCRIPTS_ARTIFACT)
    if err:
        return LoadResult(frame=None, error=err)
    return LoadResult(frame=pd.DataFrame(records), error=None)

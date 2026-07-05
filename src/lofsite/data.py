"""Location and presence check for DB-5's suite-v1 parquet artifacts.

DB-6 (this site) depends on DB-5 (the results data pipeline) for the
headline chart, model-by-dialect matrix, paired-delta charts, and transcript
walkthroughs. DB-5 owns the artifact schema; this module only owns *whether
the site can find a file*, so that pages depending on it degrade to a clear
placeholder instead of crashing the app when the artifact is absent -- per
the DB-6 plan's "what works before DB-4/DB-5 land" section.

The exact artifact path/schema convention is a DB-5/DB-6 cross-plan item not
yet fully fixed (see the plan's Risks section). `DATA_DIR` and the two
expected filenames below are this site's documented, overridable
placeholder for that convention -- not a claim about DB-5's final layout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Overridable via env var so a deploy step can point at wherever DB-5 writes.
DATA_DIR = Path(os.environ.get("LOFSITE_DATA_DIR", "data/suite_v1"))

ITEMS_ARTIFACT = "items.parquet"
HEADLINE_ARTIFACT = "headline.parquet"


@dataclass(frozen=True)
class ArtifactStatus:
    """Whether a DB-5 artifact is present, and where it was looked for."""

    available: bool
    path: Path


def items_status(data_dir: Path | None = None) -> ArtifactStatus:
    """Check whether DB-5's per-sample items artifact is present."""
    base = data_dir if data_dir is not None else DATA_DIR
    path = base / ITEMS_ARTIFACT
    return ArtifactStatus(available=path.is_file(), path=path)


def headline_status(data_dir: Path | None = None) -> ArtifactStatus:
    """Check whether DB-5's headline sensitivity-aggregate artifact is present."""
    base = data_dir if data_dir is not None else DATA_DIR
    path = base / HEADLINE_ARTIFACT
    return ArtifactStatus(available=path.is_file(), path=path)

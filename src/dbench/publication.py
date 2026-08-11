"""Validated application facade for opening public release bundles."""

from __future__ import annotations

from pathlib import Path

from dbench.provider_evidence import project_provider_evidence
from dbench.release_policy import validate_sample_release
from lofbench.release_bundle import ReleaseBundle


def open_release(
    root: Path,
    *,
    repository_root: Path | None = None,
) -> ReleaseBundle:
    return ReleaseBundle.open(
        root,
        repository_root=repository_root,
        evidence_projector=project_provider_evidence,
        release_policy_validator=validate_sample_release,
    )

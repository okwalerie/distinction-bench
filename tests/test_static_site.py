from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lofbench.authority import PROTOCOL_REGISTRY_GIT_PATH, SUITE_REGISTRY_GIT_PATH
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY
from lofbench.release_bundle import ReleaseBundle
from lofbench.suites import DEFAULT_SUITE_REGISTRY
from lofsite.build import build_site, verify_site_tree


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
    subprocess.run(
        ["git", "add", "anchor", "src"], cwd=repository, check=True
    )
    subprocess.run(["git", "commit", "-qm", "test"], cwd=repository, check=True)
    return repository


@pytest.fixture
def working(tmp_path: Path, authority_repository):
    repository = authority_repository
    bundle = ReleaseBundle.create_working(
        tmp_path / "release",
        release_id="v1.0.0-test",
        repository_url="https://example.invalid/repo",
        repository_root=repository,
        spend_caps_usd={"global": 30.0, "cohorts": {}},
    )
    return bundle, repository


def test_static_site_is_built_only_inside_a_working_bundle(working):
    bundle, _repository = working
    outside = bundle.root.parent / "outside"
    with pytest.raises(RuntimeError, match="working bundle"):
        build_site(bundle.root, outside)


def test_static_site_exposes_suite_protocol_atlas_and_local_human_pilot(working):
    bundle, _repository = working
    build_site(bundle.root, bundle.root / "site")

    expected = {
        "index.html",
        "forms.html",
        "atlas.html",
        "runs.html",
        "human.html",
        "downloads.html",
        "CNAME",
    }
    assert expected <= {path.name for path in (bundle.root / "site").iterdir()}
    assert len(list((bundle.root / "site" / "dialects").glob("*.html"))) == 29
    index = (bundle.root / "site" / "index.html").read_text()
    assert "400</div><div>frozen abstract forms" in index
    assert "29</div><div>documented dialects" in index
    forms = (bundle.root / "site" / "forms.html").read_text()
    assert "system prompt" in forms
    assert "every form and its set membership" in forms
    assert "possible confounds" in forms
    dialect = next((bundle.root / "site" / "dialects").glob("*.html")).read_text()
    assert "symbolic hash" in dialect
    runs = (bundle.root / "site" / "runs.html").read_text()
    assert "worked result path" in runs
    assert "competence + invariance profiles" in runs
    assert "controlled dialect effects" in runs
    downloads = (bundle.root / "site" / "downloads.html").read_text()
    assert "sha256" in downloads
    assert "caveats" in downloads
    human = (bundle.root / "site" / "human.html").read_text()
    assert "12 local-only stimuli" in human
    assert "participant_code" in human
    assert 'minlength=3 maxlength=64 pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}"' in human
    assert "familiarity" in human
    assert "confidence" in human
    assert "elapsed_ms" in human
    assert "fetch(" not in human
    assert "method=post" not in human.lower()
    assert "connect-src 'none'" in human


def test_sealed_bundle_can_build_an_external_copy(working):
    bundle, repository = working
    bundle.seal(repository_root=repository)
    output = bundle.root.parent / "external"
    build_site(bundle.root, output)
    assert (output / "index.html").is_file()


def test_working_site_is_byte_identical_when_rebuilt_from_sealed_bundle(working):
    bundle, repository = working
    build_site(bundle.root, bundle.root / "site")
    bundle.seal(repository_root=repository)
    output = bundle.root.parent / "rebuilt"
    build_site(bundle.root, output)
    verify_site_tree(bundle.root / "site", output)


def test_site_verifier_rejects_a_rebuilt_byte_mismatch(working):
    bundle, repository = working
    build_site(bundle.root, bundle.root / "site")
    bundle.seal(repository_root=repository)
    output = bundle.root.parent / "rebuilt"
    build_site(bundle.root, output)
    (output / "index.html").write_text("forged site")
    with pytest.raises(RuntimeError, match="does not match"):
        verify_site_tree(bundle.root / "site", output)


def test_nonempty_site_output_is_refused(working):
    bundle, _repository = working
    output = bundle.root / "site"
    (output / "owned.txt").write_text("keep")
    with pytest.raises(FileExistsError, match="not empty"):
        build_site(bundle.root, output)
    assert (output / "owned.txt").read_text() == "keep"

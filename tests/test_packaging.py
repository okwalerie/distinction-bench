from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_contains_both_authority_registries_and_runs_default_task(
    tmp_path: Path,
):
    repository = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("lofbench-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "lofbench/registries/suites-v1.json" in names
        assert "lofbench/registries/protocols-v1.json" in names

    installed = tmp_path / "installed"
    subprocess.run(
        ["uv", "pip", "install", "--target", str(installed), "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    script = f"""
import shutil
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, {str(installed)!r})
from lofbench.authority import (
    PROTOCOL_REGISTRY_GIT_PATH,
    SUITE_REGISTRY_GIT_PATH,
    authority_from_git,
    verify_authority_copies,
)
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY
from lofbench.suites import DEFAULT_SUITE_REGISTRY, load_suite
from lofbench.tasks.single import single_lof_task
suite = load_suite()
assert SUITE_REGISTRY_GIT_PATH == 'src/lofbench/registries/suites-v1.json'
assert DEFAULT_SUITE_REGISTRY.read_bytes()
assert len(suite.forms) == 400
task = single_lof_task(form_set='probe')
assert len(task.dataset.samples) == 5
assert task.metadata['dialect_id'] == 'parens.reference-v1'
repository = Path({str(tmp_path / "authority")!r})
repository.mkdir()
subprocess.run(['git', 'init', '-q'], cwd=repository, check=True)
subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=repository, check=True)
subprocess.run(['git', 'config', 'user.name', 'test'], cwd=repository, check=True)
for source, relative in (
    (DEFAULT_SUITE_REGISTRY, SUITE_REGISTRY_GIT_PATH),
    (DEFAULT_PROTOCOL_REGISTRY, PROTOCOL_REGISTRY_GIT_PATH),
):
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
subprocess.run(['git', 'add', 'src'], cwd=repository, check=True)
subprocess.run(['git', 'commit', '-qm', 'authority'], cwd=repository, check=True)
authority, suite_bytes, protocol_bytes = authority_from_git(repository)
verify_authority_copies(
    authority,
    suite_bytes=suite_bytes,
    protocol_bytes=protocol_bytes,
    repository_root=repository,
)
"""
    subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

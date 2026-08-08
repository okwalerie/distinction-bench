from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

from lofbench import state_io
from lofbench.state_io import (
    FileLockUnavailableError,
    append_jsonl_fsynced,
    exclusive_file_lock,
    read_jsonl,
    write_json_atomic,
)


def test_append_retries_interrupted_and_short_writes(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    real_write = os.write
    calls = 0

    def interrupted_then_short(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError
        return real_write(descriptor, payload[:7])

    monkeypatch.setattr(state_io.os, "write", interrupted_then_short)
    append_jsonl_fsynced(path, {"event": "durable", "sequence": 1})

    assert calls > 2
    assert read_jsonl(path) == [{"event": "durable", "sequence": 1}]


def test_first_append_fsyncs_file_and_parent_but_later_append_only_fsyncs_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "events.jsonl"
    real_fsync = os.fsync
    synced: list[str] = []

    def observe_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        synced.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(state_io.os, "fsync", observe_fsync)
    append_jsonl_fsynced(path, {"sequence": 1})
    assert synced == ["file", "directory"]

    synced.clear()
    append_jsonl_fsynced(path, {"sequence": 2})
    assert synced == ["file"]
    assert read_jsonl(path) == [{"sequence": 1}, {"sequence": 2}]


def test_atomic_json_fsyncs_payload_then_parent_after_replace(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    real_fsync = os.fsync
    synced: list[str] = []

    def observe_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        synced.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(state_io.os, "fsync", observe_fsync)
    write_json_atomic(path, {"status": "complete"})

    assert synced[-2:] == ["file", "directory"]
    assert json.loads(path.read_text()) == {"status": "complete"}


def test_first_lock_creation_fsyncs_lock_file_and_parent(tmp_path, monkeypatch):
    path = tmp_path / "run-state" / ".run.lock"
    real_fsync = os.fsync
    synced: list[str] = []

    def observe_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        synced.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(state_io.os, "fsync", observe_fsync)
    with exclusive_file_lock(path, blocking=False):
        pass

    assert synced[-2:] == ["file", "directory"]


def test_nonblocking_lock_reports_that_an_owner_is_active(tmp_path):
    path = tmp_path / "run-state" / ".run.lock"
    with exclusive_file_lock(path, blocking=False):
        with pytest.raises(FileLockUnavailableError, match="already held"):
            with exclusive_file_lock(path, blocking=False):
                pass


def test_first_creation_and_append_survive_a_real_subprocess(tmp_path):
    path = tmp_path / "events.jsonl"
    script = """
import sys
from pathlib import Path
from lofbench.state_io import append_jsonl_fsynced
path = Path(sys.argv[1])
append_jsonl_fsynced(path, {"sequence": 1})
append_jsonl_fsynced(path, {"sequence": 2})
"""
    subprocess.run([sys.executable, "-c", script, str(path)], check=True)
    assert read_jsonl(path) == [{"sequence": 1}, {"sequence": 2}]

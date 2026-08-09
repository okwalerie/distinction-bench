"""Durable local state primitives used by release applications."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class FileLockUnavailableError(RuntimeError):
    """A nonblocking advisory lock is already owned elsewhere."""


_lifecycle_local = threading.local()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_parent(path: Path) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        _fsync_directory(path.parent.parent)


def _open_for_append(path: Path) -> tuple[int, bool]:
    flags = os.O_APPEND | os.O_WRONLY
    while True:
        try:
            return os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600), True
        except FileExistsError:
            try:
                return os.open(path, flags), False
            except FileNotFoundError:
                continue


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        try:
            written = os.write(descriptor, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("durable write made no progress")
        remaining = remaining[written:]


def append_jsonl_fsynced(path: Path, value: dict[str, Any]) -> None:
    _ensure_parent(path)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor, created = _open_for_append(path)
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        if created:
            _fsync_directory(path.parent)
    finally:
        os.close(descriptor)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _ensure_parent(path)
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive_file_lock(path: Path, *, blocking: bool) -> Iterator[None]:
    """Hold one advisory file lock, durably creating its inode when needed."""
    import fcntl

    _ensure_parent(path)
    flags = os.O_RDWR
    while True:
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            break
        except FileExistsError:
            try:
                descriptor = os.open(path, flags)
                created = False
                break
            except FileNotFoundError:
                continue
    try:
        if created:
            os.fsync(descriptor)
            _fsync_directory(path.parent)
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise FileLockUnavailableError(f"file lock is already held: {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def state_lifecycle_lock_path(state_root: Path) -> Path:
    """Return the stable lock inode shared by all writers of one state root."""
    return state_root.parent / f".{state_root.name}.lifecycle.lock"


@contextmanager
def state_lifecycle_lock(state_root: Path, *, blocking: bool) -> Iterator[None]:
    """Serialize state-root lifecycles before narrower run and ledger locks."""
    identity = state_root.resolve(strict=False)
    held = getattr(_lifecycle_local, "held", set())
    if identity in held:
        yield
        return
    with exclusive_file_lock(state_lifecycle_lock_path(state_root), blocking=blocking):
        _lifecycle_local.held = {*held, identity}
        try:
            yield
        finally:
            _lifecycle_local.held = held


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

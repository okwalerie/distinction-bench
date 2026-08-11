"""Application configuration and secret loading."""

from __future__ import annotations

import stat
from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Load a strict key-value env file without logging its contents."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(f"env file must be mode 0600 or stricter, found {mode:o}")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError("env file contains a non-assignment line")
        key, value = stripped.split("=", 1)
        if not key or not key.replace("_", "").isalnum():
            raise ValueError("env file contains an invalid variable name")
        values[key] = value
    return values

"""Fail fast unless CI exactly matches the frozen v1 raster authority."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import subprocess
from pathlib import Path

from lofbench.renderers.runtime import visual_runtime_available

EXPECTED_PACKAGES = {
    "fontconfig": "2.15.0-2.3",
    "fonts-dejavu-core": "2.37-8",
    "git": "1:2.47.3-0+deb13u1",
    "git-man": "1:2.47.3-0+deb13u1",
    "libcairo2:amd64": "1.18.4-1+b1",
    "libffi8:amd64": "3.4.8-2",
    "libgdk-pixbuf-2.0-0:amd64": "2.42.12+dfsg-4+deb13u1",
    "libpango-1.0-0:amd64": "1.56.3-1",
    "libpangocairo-1.0-0:amd64": "1.56.3-1",
    "libbz2-1.0:amd64": "1.0.8-6",
    "libdb5.3t64:amd64": "5.3.28+dfsg2-9",
    "libgdbm-compat4t64:amd64": "1.24-2",
    "libgdbm6t64:amd64": "1.24-2",
    "liblzma5:amd64": "5.8.1-1",
    "libncursesw6:amd64": "6.5+20250216-2",
    "libreadline8t64:amd64": "8.2-6",
    "libsqlite3-0:amd64": "3.46.1-7+deb13u1",
    "libssl3t64:amd64": "3.5.6-1~deb13u2",
    "libuuid1:amd64": "2.41-5",
    "zlib1g:amd64": "1:1.3.dfsg+really1.3.1-1+b1",
}
EXPECTED_FONT = "DejaVu Sans|/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _installed_packages() -> dict[str, str]:
    output = subprocess.check_output(
        [
            "dpkg-query",
            "-W",
            "-f=${binary:Package}=${Version}\\n",
            *EXPECTED_PACKAGES,
        ],
        text=True,
    )
    return dict(line.split("=", 1) for line in output.splitlines())


def main() -> None:
    workspace_owner = Path("/workspace").stat().st_uid
    observed = {
        "debian": Path("/etc/debian_version").read_text().strip(),
        "python": platform.python_version(),
        "cairosvg": importlib.metadata.version("cairosvg"),
        "packages": _installed_packages(),
        "font": subprocess.check_output(
            ["fc-match", "--format=%{family}|%{file}", "sans-serif"],
            text=True,
        ),
        "git": subprocess.check_output(["git", "--version"], text=True).strip(),
    }
    expected = {
        "debian": "13.5",
        "python": "3.11.15",
        "cairosvg": "2.9.0",
        "packages": EXPECTED_PACKAGES,
        "font": EXPECTED_FONT,
        "git": "git version 2.47.3",
    }
    if observed != expected:
        raise SystemExit(
            f"visual authority runtime mismatch:\nexpected={expected!r}\nobserved={observed!r}"
        )
    if not visual_runtime_available():
        raise SystemExit("visual authority imports are not usable")
    if os.geteuid() == 0 or os.geteuid() != workspace_owner:
        raise SystemExit(
            "visual authority must run as the non-root checkout owner: "
            f"process={os.geteuid()} checkout={workspace_owner}"
        )
    print("visual authority runtime matches frozen v1")


if __name__ == "__main__":
    main()

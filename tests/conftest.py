"""Repository-wide pytest capabilities."""

from __future__ import annotations

import pytest

from lofbench.renderers.runtime import visual_runtime_available


@pytest.fixture(scope="session")
def visual_runtime() -> None:
    """Require the same optional raster capability used by the marker."""

    if not visual_runtime_available():
        pytest.skip("the visual extra and native cairo are required")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip every raster test through one actual-runtime capability gate."""

    if visual_runtime_available():
        return
    skip = pytest.mark.skip(reason="the visual extra and native cairo are required")
    for item in items:
        if "requires_visual_runtime" in item.keywords:
            item.add_marker(skip)

"""Dataset factory functions for inspect-ai integration."""

from lofbench.datasets.factory import (
    create_composite_dataset,
    create_single_dataset,
    create_suite_dataset,
)

__all__ = [
    "create_single_dataset",
    "create_composite_dataset",
    "create_suite_dataset",
]

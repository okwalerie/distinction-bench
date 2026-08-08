"""inspect-ai task definitions for LoF evaluation."""

from lofbench.tasks.composite import composite_lof_task
from lofbench.tasks.single import adhoc_single_lof_task, single_lof_task

__all__ = [
    "single_lof_task",
    "composite_lof_task",
    "adhoc_single_lof_task",
]

"""lofbench - Laws of Form benchmark toolkit."""

from lofbench.core import (
    DIFFICULTY_CONFIGS,
    canonical_string,
    evaluate,
    form_depth,
    form_to_string,
    generate_composite_test_cases,
    generate_form_string,
    generate_test_cases,
    normal_form_string,
    normal_value,
    simplify_string,
    string_depth,
    string_to_form,
)
from lofbench.metrics import compute_controlled_effects, compute_profiles
from lofbench.protocols import PROTOCOLS, ProtocolSpec, get_protocol
from lofbench.records import CallRecord, RunManifest, TrialRecord
from lofbench.release_bundle import ReleaseBundle
from lofbench.renderers import (
    FormRenderer,
    RenderedForm,
    get_renderer,
    list_renderers,
)
from lofbench.tasks import adhoc_single_lof_task, composite_lof_task, single_lof_task

__all__ = [
    # Core
    "DIFFICULTY_CONFIGS",
    "form_to_string",
    "string_to_form",
    "form_depth",
    "string_depth",
    "simplify_string",
    "normal_form_string",
    "normal_value",
    "canonical_string",
    "evaluate",
    "generate_form_string",
    "generate_test_cases",
    "generate_composite_test_cases",
    # inspect-ai tasks
    "single_lof_task",
    "adhoc_single_lof_task",
    "composite_lof_task",
    # Renderers
    "FormRenderer",
    "RenderedForm",
    "get_renderer",
    "list_renderers",
    # Public release interfaces
    "ProtocolSpec",
    "PROTOCOLS",
    "get_protocol",
    "RunManifest",
    "TrialRecord",
    "CallRecord",
    "ReleaseBundle",
    "compute_profiles",
    "compute_controlled_effects",
]

__version__ = "0.2.0"

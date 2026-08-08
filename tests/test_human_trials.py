from __future__ import annotations

from copy import deepcopy

import pytest

from lofbench.human_trials import validate_human_trial_export
from lofbench.protocols import PROTOCOLS
from lofbench.suites import load_suite


def _context():
    suite = load_suite()
    return suite, PROTOCOLS


def _export() -> dict:
    return {
        "schema_version": 1,
        "release_id": "v1.0.0-sample.1",
        "records": [
            {
                "participant_code": "anon-001",
                "familiarity_band": "some",
                "abstract_form_id": "lof_01546ab19cc43091f8ac",
                "dialect_id": "enclosure.plain-v1",
                "protocol_id": "reduce-infer-v1",
                "answer": "marked",
                "transcription": None,
                "confidence": "medium",
                "elapsed_ms": 1200,
            },
            {
                "participant_code": "anon-001",
                "familiarity_band": "some",
                "abstract_form_id": "lof_0c5a60a280c796489342",
                "dialect_id": "enclosure.plain-v1",
                "protocol_id": "transcribe-infer-v1",
                "answer": None,
                "transcription": "(())",
                "confidence": "high",
                "elapsed_ms": 950,
            },
        ],
    }


def _validate(value: dict) -> None:
    suite, protocols = _context()
    validate_human_trial_export(
        value,
        release_id="v1.0.0-sample.1",
        form_ids={form["abstract_form_id"] for form in suite.forms},
        dialect_ids=set(suite.specs),
        protocol_answer_kinds={key: spec.answer_kind for key, spec in protocols.items()},
    )


def test_human_trial_export_conforms_to_frozen_registry():
    _validate(_export())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["records"][0].pop("participant_code"),
        lambda value: value["records"][0].__setitem__("participant_code", "a person"),
        lambda value: value["records"][0].__setitem__("familiarity_band", "guru"),
        lambda value: value["records"][0].__setitem__("abstract_form_id", "not-frozen"),
        lambda value: value["records"][0].__setitem__("dialect_id", "not-frozen"),
        lambda value: value["records"][0].__setitem__("protocol_id", "not-frozen"),
        lambda value: value["records"][0].__setitem__("answer", ["marked"]),
        lambda value: value["records"][0].__setitem__("transcription", "extra"),
        lambda value: value["records"][0].__setitem__("confidence", "certain"),
        lambda value: value["records"][0].__setitem__("elapsed_ms", -1),
    ],
)
def test_human_trial_export_rejects_schema_and_registry_mutations(mutation):
    value = deepcopy(_export())
    mutation(value)
    with pytest.raises(ValueError):
        _validate(value)

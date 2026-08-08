"""Public schema and validator for anonymous human-pilot exports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HUMAN_TRIAL_SCHEMA_VERSION = 1
_PARTICIPANT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$"

HUMAN_TRIAL_EXPORT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://distinction.valeriekim.ca/schema/human-trial-record-v1.json",
    "title": "Distinction benchmark human trial export",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "release_id", "records"],
    "properties": {
        "schema_version": {"const": HUMAN_TRIAL_SCHEMA_VERSION},
        "release_id": {"type": "string", "minLength": 1},
        "records": {
            "type": "array",
            "items": {"$ref": "#/$defs/HumanTrialRecord"},
        },
    },
    "$defs": {
        "HumanTrialRecord": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "participant_code",
                "familiarity_band",
                "abstract_form_id",
                "dialect_id",
                "protocol_id",
                "answer",
                "transcription",
                "confidence",
                "elapsed_ms",
            ],
            "properties": {
                "participant_code": {
                    "type": "string",
                    "pattern": _PARTICIPANT_PATTERN,
                    "description": "Anonymous or pseudonymous participant code; never a name.",
                },
                "familiarity_band": {"enum": ["none", "some", "expert"]},
                "abstract_form_id": {"type": "string", "minLength": 1},
                "dialect_id": {"type": "string", "minLength": 1},
                "protocol_id": {"type": "string", "minLength": 1},
                "answer": {"type": ["string", "null"]},
                "transcription": {"type": ["string", "null"]},
                "confidence": {"enum": ["low", "medium", "high"]},
                "elapsed_ms": {"type": "integer", "minimum": 0},
            },
            "oneOf": [
                {
                    "properties": {
                        "answer": {"enum": ["marked", "unmarked"]},
                        "transcription": {"type": "null"},
                    }
                },
                {
                    "properties": {
                        "answer": {"type": "null"},
                        "transcription": {"type": "string", "minLength": 1},
                    }
                },
            ],
        }
    },
}


def write_human_trial_schema(path: Path) -> None:
    path.write_text(json.dumps(HUMAN_TRIAL_EXPORT_SCHEMA, indent=2) + "\n")


def validate_human_trial_export(
    value: Mapping[str, Any],
    *,
    release_id: str | None = None,
    form_ids: set[str] | None = None,
    dialect_ids: set[str] | None = None,
    protocol_answer_kinds: Mapping[str, str] | None = None,
) -> None:
    """Validate the exported records without adding a runtime JSON-schema dependency."""
    if set(value) != {"schema_version", "release_id", "records"}:
        raise ValueError("human trial export fields do not match schema")
    if value["schema_version"] != HUMAN_TRIAL_SCHEMA_VERSION:
        raise ValueError("unsupported human trial schema version")
    if not isinstance(value["release_id"], str) or not value["release_id"]:
        raise ValueError("human trial release_id must be a nonempty string")
    if release_id is not None and value["release_id"] != release_id:
        raise ValueError("human trial release_id mismatch")
    records = value["records"]
    if not isinstance(records, list):
        raise ValueError("human trial records must be an array")
    required = set(HUMAN_TRIAL_EXPORT_SCHEMA["$defs"]["HumanTrialRecord"]["required"])
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError(f"human trial record {index} fields do not match schema")
        participant = record["participant_code"]
        if (
            not isinstance(participant, str)
            or re.fullmatch(_PARTICIPANT_PATTERN, participant) is None
        ):
            raise ValueError(f"human trial record {index} has an invalid participant code")
        if record["familiarity_band"] not in {"none", "some", "expert"}:
            raise ValueError(f"human trial record {index} has an invalid familiarity band")
        if record["confidence"] not in {"low", "medium", "high"}:
            raise ValueError(f"human trial record {index} has invalid confidence")
        elapsed = record["elapsed_ms"]
        if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
            raise ValueError(f"human trial record {index} has invalid elapsed time")
        for field, allowed in (
            ("abstract_form_id", form_ids),
            ("dialect_id", dialect_ids),
        ):
            item = record[field]
            if (
                not isinstance(item, str)
                or not item
                or (allowed is not None and item not in allowed)
            ):
                raise ValueError(f"human trial record {index} has invalid {field}")
        protocol_id = record["protocol_id"]
        if not isinstance(protocol_id, str) or not protocol_id:
            raise ValueError(f"human trial record {index} has invalid protocol_id")
        answer = record["answer"]
        transcription = record["transcription"]
        answer_kind = (
            protocol_answer_kinds.get(protocol_id) if protocol_answer_kinds is not None else None
        )
        if protocol_answer_kinds is not None and answer_kind is None:
            raise ValueError(f"human trial record {index} has unknown protocol_id")
        is_answer = (
            isinstance(answer, str)
            and answer in {"marked", "unmarked"}
            and transcription is None
        )
        is_transcription = answer is None and isinstance(transcription, str) and bool(
            transcription.strip()
        )
        if not (is_answer or is_transcription):
            raise ValueError(f"human trial record {index} must contain exactly one response kind")
        if answer_kind == "normal_value" and not is_answer:
            raise ValueError(f"human trial record {index} response kind does not match protocol")
        if answer_kind == "structural_transcription" and not is_transcription:
            raise ValueError(f"human trial record {index} response kind does not match protocol")

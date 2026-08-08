"""Versioned public task protocols.

Protocol specifications are data. Inspect tasks and the static site render the same
stored text and JSON schema rather than maintaining parallel prompt definitions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

AnswerKind = Literal["normal_value", "structural_transcription"]

_SYSTEM = """You are evaluating a ground expression in George Spencer-Brown's Laws of Form.
Only containment and adjacency matter. Use these two laws repeatedly:
- calling: adjacent identical marks have the value of one mark;
- crossing: a mark whose sole content is one empty mark has the unmarked value.
Every ground expression reduces to marked or unmarked. Return only JSON matching the
supplied schema. Do not include an explanation or chain of thought."""

_NORMAL_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": "string", "enum": ["marked", "unmarked"]}},
    "required": ["value"],
    "additionalProperties": False,
}

_TREE_SCHEMA: dict[str, Any] = {
    "$defs": {
        "mark": {
            "type": "array",
            "items": {"$ref": "#/$defs/mark"},
        }
    },
    "type": "object",
    "properties": {
        "tree": {
            "type": "array",
            "items": {"$ref": "#/$defs/mark"},
        }
    },
    "required": ["tree"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ProtocolSpec:
    protocol_id: str
    system_text: str
    user_template: str
    response_json_schema: dict[str, Any]
    scorer_id: str
    scorer_version: str
    maximum_output_tokens: int
    includes_dialect_legend: bool
    answer_kind: AnswerKind

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProtocolSpec:
        return cls(**value)

    def render_user_text(self, *, reading_rule: str | None = None) -> str:
        if self.includes_dialect_legend:
            if not reading_rule:
                raise ValueError(f"{self.protocol_id} requires a dialect reading rule")
            return self.user_template.replace("{reading_rule}", reading_rule)
        return self.user_template

    def target_for(self, form: dict[str, Any]) -> str:
        if self.answer_kind == "normal_value":
            return str(form["normal_value"])
        return json.dumps(form["abstract_form"], separators=(",", ":"))

    def parse_answer(
        self,
        response: str,
        *,
        expected_normal_value: str,
        expected_tree: list[Any],
    ) -> tuple[str, str, bool]:
        try:
            value = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            return "invalid", "", False
        if not isinstance(value, dict):
            return "invalid", "", False
        if self.answer_kind == "normal_value":
            if set(value) != {"value"} or value["value"] not in {"marked", "unmarked"}:
                return "invalid", "", False
            prediction = value["value"]
            return "valid", prediction, prediction == expected_normal_value
        if set(value) != {"tree"} or not isinstance(value["tree"], list):
            return "invalid", "", False
        prediction = json.dumps(value["tree"], separators=(",", ":"))
        return "valid", prediction, value["tree"] == expected_tree


PROTOCOLS: dict[str, ProtocolSpec] = {
    "reduce-infer-v1": ProtocolSpec(
        protocol_id="reduce-infer-v1",
        system_text=_SYSTEM,
        user_template=(
            "The attached stimulus encodes one Laws of Form containment structure. "
            "Reduce it and return its normal value."
        ),
        response_json_schema=_NORMAL_VALUE_SCHEMA,
        scorer_id="normal-value-exact",
        scorer_version="1",
        maximum_output_tokens=512,
        includes_dialect_legend=False,
        answer_kind="normal_value",
    ),
    "reduce-taught-v1": ProtocolSpec(
        protocol_id="reduce-taught-v1",
        system_text=_SYSTEM,
        user_template=(
            "Reading convention: {reading_rule}\n\n"
            "The attached stimulus encodes one Laws of Form containment structure. "
            "Reduce it and return its normal value."
        ),
        response_json_schema=_NORMAL_VALUE_SCHEMA,
        scorer_id="normal-value-exact",
        scorer_version="1",
        maximum_output_tokens=512,
        includes_dialect_legend=True,
        answer_kind="normal_value",
    ),
    "transcribe-infer-v1": ProtocolSpec(
        protocol_id="transcribe-infer-v1",
        system_text=_SYSTEM,
        user_template=(
            "The attached stimulus encodes one containment tree. Transcribe it as nested "
            "JSON arrays: each array item is a mark, and that mark is itself an array of "
            "the marks directly contained by it. Return the top-level list in `tree`."
        ),
        response_json_schema=_TREE_SCHEMA,
        scorer_id="containment-tree-exact",
        scorer_version="1",
        maximum_output_tokens=512,
        includes_dialect_legend=False,
        answer_kind="structural_transcription",
    ),
    "transcribe-taught-v1": ProtocolSpec(
        protocol_id="transcribe-taught-v1",
        system_text=_SYSTEM,
        user_template=(
            "Reading convention: {reading_rule}\n\n"
            "Transcribe the attached containment structure as nested JSON arrays: each "
            "array item is a mark, and that mark is itself an array of the marks directly "
            "contained by it. Return the top-level list in `tree`."
        ),
        response_json_schema=_TREE_SCHEMA,
        scorer_id="containment-tree-exact",
        scorer_version="1",
        maximum_output_tokens=512,
        includes_dialect_legend=True,
        answer_kind="structural_transcription",
    ),
}


def get_protocol(protocol_id: str) -> ProtocolSpec:
    try:
        return PROTOCOLS[protocol_id]
    except KeyError as exc:
        raise ValueError(f"unknown protocol {protocol_id!r}") from exc


def write_protocol_registry(path: Path) -> None:
    payload = {
        "schema_version": 1,
        "protocols": {key: value.to_dict() for key, value in sorted(PROTOCOLS.items())},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def load_protocol_registry(path: Path) -> dict[str, ProtocolSpec]:
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise RuntimeError("unsupported protocol registry schema")
    return {key: ProtocolSpec.from_dict(value) for key, value in payload["protocols"].items()}

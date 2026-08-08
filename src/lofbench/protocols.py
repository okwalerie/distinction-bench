"""Versioned public task protocols loaded from the checked-in registry."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

AnswerKind = Literal["normal_value", "structural_transcription"]
PROTOCOLS_DIR = Path(__file__).with_name("registries")
DEFAULT_PROTOCOL_REGISTRY = PROTOCOLS_DIR / "protocols-v1.json"


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

    def prompt_hash(self, *, reading_rule: str, model_payload_sha256: str) -> str:
        """Hash exact system text, rendered user text, and frozen payload identity."""
        user_text = self.render_user_text(reading_rule=reading_rule)
        material = self.system_text + "\x00" + user_text + "\x00" + model_payload_sha256
        return sha256(material.encode()).hexdigest()


def load_protocol_registry(path: Path = DEFAULT_PROTOCOL_REGISTRY) -> dict[str, ProtocolSpec]:
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise RuntimeError("unsupported protocol registry schema")
    protocols = {
        key: ProtocolSpec.from_dict(value) for key, value in payload["protocols"].items()
    }
    if any(key != value.protocol_id for key, value in protocols.items()):
        raise RuntimeError("protocol registry key does not match protocol_id")
    return protocols


PROTOCOLS = load_protocol_registry()


def get_protocol(protocol_id: str) -> ProtocolSpec:
    try:
        return PROTOCOLS[protocol_id]
    except KeyError as exc:
        raise ValueError(f"unknown protocol {protocol_id!r}") from exc


def write_protocol_registry(path: Path) -> None:
    """Copy the checked-in registry byte-for-byte as non-authoritative evidence."""
    path.write_bytes(DEFAULT_PROTOCOL_REGISTRY.read_bytes())

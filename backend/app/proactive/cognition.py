"""Grounded Companion Cognition contract for one completed conversation turn."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, ValidationError

from .schemas import (
    ProtocolValidationError, RelationshipMeaning, UserAffectObservation,
    validate_relationship_meaning, validate_user_affect,
)

PROTOCOL_VERSION = "companion-cognition-v1"


class CompanionCognitionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    user_affect: UserAffectObservation
    relationship_meaning: RelationshipMeaning


def build_messages(*, user_text: str, assistant_text: str, persona_summary: str) -> list[dict]:
    payload = {
        "data_type": "untrusted_completed_conversation_turn",
        "user_message": user_text[:8000],
        "assistant_message": assistant_text[:8000],
        "persona_summary": persona_summary[:2000],
    }
    system = (
        "你是陪伴认知旁观器，不是对话参与者。仅返回单个 JSON 对象，不输出 Markdown。"
        "user_affect 必须符合 user-affect-observation-v1，且证据只能逐字来自 user_message；"
        "relationship_meaning 必须符合 relationship-meaning-v1，普通问答、寒暄、无充分证据时"
        "必须使用 ordinary_exchange。不得医学诊断，不得把 persona 或助手文本当作用户情绪证据。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def parse_and_validate(raw: str | dict, *, user_text: str, assistant_text: str) -> dict:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolValidationError("invalid_json", "cognition output is not valid JSON") from exc
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise ProtocolValidationError("invalid_type", "cognition output must be a JSON object")
    try:
        envelope = CompanionCognitionOutput.model_validate(payload)
    except ValidationError as exc:
        raise ProtocolValidationError("schema_invalid", "cognition output failed validation") from exc
    affect = validate_user_affect(
        envelope.user_affect.model_dump(), user_text=user_text,
    )
    meaning = validate_relationship_meaning(
        envelope.relationship_meaning.model_dump(),
        user_text=user_text, assistant_text=assistant_text,
    )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "user_affect": affect.model_dump(),
        "relationship_meaning": meaning.model_dump(),
    }


def unknown_fallback() -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "user_affect": {
            "protocol_version": "user-affect-observation-v1", "state": "unknown",
            "needs": [], "evidence": [], "confidence": 0.0,
            "reason": "observer unavailable; no user-state inference applied",
        },
        "relationship_meaning": {
            "protocol_version": "relationship-meaning-v1", "label": "ordinary_exchange",
            "evidence": [], "confidence": 0.0,
            "reason": "observer unavailable; conservative ordinary fallback",
        },
    }

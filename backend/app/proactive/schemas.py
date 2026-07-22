"""Strict model-output contracts introduced by EAP.R1."""
from __future__ import annotations

import json
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .protocols import PROACTIVE_FEEDBACK_V1, USER_AFFECT_OBSERVATION_V1


class ProtocolValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, allow_inf_nan=False
    )


class UserEvidence(_StrictModel):
    quote: str = Field(min_length=1, max_length=160)


class UserAffectObservation(_StrictModel):
    protocol_version: Literal[USER_AFFECT_OBSERVATION_V1]
    state: Literal[
        "positive", "calm", "low", "frustrated", "overwhelmed", "unknown"
    ]
    needs: list[Literal["celebrate", "listen", "comfort", "problem_solving", "space"]] = Field(
        default_factory=list, max_length=3
    )
    evidence: list[UserEvidence] = Field(default_factory=list, max_length=4)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def require_evidence_for_claim(self) -> "UserAffectObservation":
        if self.state != "unknown" and not self.evidence:
            raise ValueError("a non-unknown affect state requires user evidence")
        return self


class ProactiveFeedback(_StrictModel):
    protocol_version: Literal[PROACTIVE_FEEDBACK_V1]
    feedback_kind: Literal[
        "wrong_timing", "too_frequent", "wrong_content", "reject_topic",
        "reject_tone", "allow_more",
    ]
    delivery_id: str = Field(min_length=1, max_length=64)
    evidence: list[UserEvidence] = Field(min_length=1, max_length=3)
    target_topic: str | None = Field(default=None, max_length=160)
    target_kind: str | None = Field(default=None, max_length=64)
    confidence: float = Field(ge=0, le=1)


T = TypeVar("T", bound=BaseModel)


def _parse(raw: str | dict, model: type[T], *, user_text: str) -> T:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolValidationError("invalid_json", "protocol output is not valid JSON") from exc
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise ProtocolValidationError("invalid_type", "protocol output must be a JSON object")
    try:
        result = model.model_validate(payload)
    except ValidationError as exc:
        raise ProtocolValidationError("schema_invalid", "protocol output failed validation") from exc
    for item in result.evidence:
        if item.quote not in user_text:
            raise ProtocolValidationError("evidence_not_found", "evidence is not in the user message")
    return result


def validate_user_affect(raw: str | dict, *, user_text: str) -> UserAffectObservation:
    return _parse(raw, UserAffectObservation, user_text=user_text)


def validate_proactive_feedback(raw: str | dict, *, user_text: str) -> ProactiveFeedback:
    return _parse(raw, ProactiveFeedback, user_text=user_text)

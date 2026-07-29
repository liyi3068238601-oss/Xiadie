"""CIE.1 bounded message accumulation and ephemeral turn envelopes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = "turn-ingress-buffer-v1"
ENVELOPE_VERSION = "turn-envelope-v1"
DEFAULT_WINDOW_MS = 500
MIN_WINDOW_MS = 300
MAX_WINDOW_MS = 800
MAX_MESSAGES = 20
MAX_ATTACHMENTS_PER_MESSAGE = 8


class TurnIngressMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

    client_message_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    window_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    content: str = Field(default="", max_length=12_000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS_PER_MESSAGE)
    authorization_scope: Literal[
        "local_text_only", "local_image", "remote_image_once",
    ] = "local_text_only"
    queued_at_ms: int = Field(ge=0)
    boundary: Literal["idle_timeout", "explicit_send", "voice_end", "stop"] = "idle_timeout"

    @model_validator(mode="after")
    def validate_payload(self) -> "TurnIngressMessage":
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("content and attachment_ids cannot both be empty")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("attachment_ids must be unique per message")
        return self


@dataclass(frozen=True)
class TurnEnvelope:
    protocol_version: str
    session_id: str
    window_id: str
    content: str
    entries: tuple[TurnIngressMessage, ...]
    attachment_ids: tuple[str, ...]
    seal_reason: str

    def public_meta(self, persisted_message_ids: list[str]) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "window_id": self.window_id,
            "message_count": len(self.entries),
            "message_ids": persisted_message_ids,
            "seal_reason": self.seal_reason,
        }


def normalize_window_ms(value: int) -> int:
    return max(MIN_WINDOW_MS, min(MAX_WINDOW_MS, int(value)))


def build_envelope(session_id: str, entries: list[TurnIngressMessage]) -> TurnEnvelope:
    if not entries or len(entries) > MAX_MESSAGES:
        raise ValueError(f"turn ingress requires 1..{MAX_MESSAGES} messages")
    if len({item.client_message_id for item in entries}) != len(entries):
        raise ValueError("client_message_id must be unique within a turn")
    window_ids = {item.window_id for item in entries}
    if len(window_ids) != 1:
        raise ValueError("turn ingress cannot cross windows")
    attachment_ids = [aid for item in entries for aid in item.attachment_ids]
    if len(set(attachment_ids)) != len(attachment_ids):
        raise ValueError("an attachment cannot belong to multiple ingress messages")
    content = "\n\n".join(item.content.strip() for item in entries if item.content.strip())
    return TurnEnvelope(
        protocol_version=ENVELOPE_VERSION,
        session_id=session_id,
        window_id=next(iter(window_ids)),
        content=content,
        entries=tuple(entries),
        attachment_ids=tuple(attachment_ids),
        seal_reason=entries[-1].boundary,
    )

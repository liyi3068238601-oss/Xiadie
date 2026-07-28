"""CIE.2 in-process control plane for bounded chat cancellation and replay."""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Literal

Phase = Literal["retrieval", "generation", "persistence"]
_CANCELLABLE = frozenset({"retrieval", "generation"})
_COMPLETED_TTL_SECONDS = 300.0
_ACTIVE_TTL_SECONDS = 600.0


@dataclass
class ActiveChatRequest:
    chat_nonce: str
    cancel_token: str
    session_id: str
    phase: Phase = "retrieval"
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class CompletedChatRequest:
    session_id: str
    payload: dict
    completed_at: float


_lock = threading.RLock()
_active_by_token: dict[str, ActiveChatRequest] = {}
_active_nonce_to_token: dict[str, str] = {}
_completed: dict[str, CompletedChatRequest] = {}


def _prune(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    cutoff = current - _COMPLETED_TTL_SECONDS
    for nonce in [key for key, value in _completed.items() if value.completed_at < cutoff]:
        _completed.pop(nonce, None)
    active_cutoff = current - _ACTIVE_TTL_SECONDS
    for token, item in list(_active_by_token.items()):
        if item.started_at >= active_cutoff:
            continue
        _active_by_token.pop(token, None)
        _active_nonce_to_token.pop(item.chat_nonce, None)


def begin(*, chat_nonce: str, cancel_token: str, session_id: str) -> tuple[str, dict | None]:
    """Return ``started``, ``completed`` or ``active`` without replacing a request."""
    with _lock:
        _prune()
        completed = _completed.get(chat_nonce)
        if completed:
            if completed.session_id != session_id:
                return "conflict", None
            return "completed", dict(completed.payload)
        if chat_nonce in _active_nonce_to_token or cancel_token in _active_by_token:
            return "active", None
        item = ActiveChatRequest(chat_nonce, cancel_token, session_id)
        _active_by_token[cancel_token] = item
        _active_nonce_to_token[chat_nonce] = cancel_token
        return "started", None


def lookup(chat_nonce: str, session_id: str, cancel_token: str | None = None) -> tuple[str, dict | None]:
    with _lock:
        _prune()
        completed = _completed.get(chat_nonce)
        if completed:
            if completed.session_id != session_id:
                return "conflict", None
            return "completed", dict(completed.payload)
        if chat_nonce in _active_nonce_to_token:
            return "active", None
        if cancel_token and cancel_token in _active_by_token:
            return "conflict", None
        return "missing", None


def phase(cancel_token: str, value: Phase) -> bool:
    with _lock:
        item = _active_by_token.get(cancel_token)
        if not item:
            return False
        item.phase = value
        return True


def is_cancelled(cancel_token: str) -> bool:
    with _lock:
        item = _active_by_token.get(cancel_token)
        return bool(item and item.cancel_requested.is_set())


def cancel(cancel_token: str) -> dict:
    with _lock:
        item = _active_by_token.get(cancel_token)
        if not item:
            return {"found": False, "accepted": False, "phase": None}
        accepted = item.phase in _CANCELLABLE
        if accepted:
            item.cancel_requested.set()
        return {"found": True, "accepted": accepted, "phase": item.phase}


def complete(cancel_token: str, payload: dict) -> None:
    with _lock:
        item = _active_by_token.pop(cancel_token, None)
        if not item:
            return
        _active_nonce_to_token.pop(item.chat_nonce, None)
        _completed[item.chat_nonce] = CompletedChatRequest(
            item.session_id, dict(payload), time.monotonic(),
        )
        _prune()


def update_completed(chat_nonce: str, payload: dict) -> None:
    with _lock:
        item = _completed.get(chat_nonce)
        if item:
            _completed[chat_nonce] = CompletedChatRequest(
                item.session_id, dict(payload), item.completed_at,
            )


def finish(cancel_token: str) -> None:
    with _lock:
        item = _active_by_token.pop(cancel_token, None)
        if item:
            _active_nonce_to_token.pop(item.chat_nonce, None)


def reset_for_tests() -> None:
    with _lock:
        _active_by_token.clear()
        _active_nonce_to_token.clear()
        _completed.clear()

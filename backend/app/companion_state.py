"""情绪与关系系统的兼容服务入口。

旧模块名暂时保留，内部已经切换到 affect_state / relationship_state。
"""
from __future__ import annotations

from . import db
from .affect import engine, repository, tone_grid


def get_state(*, persist_advance: bool = True) -> dict:
    snapshot = (
        repository.get_snapshot()
        if persist_advance
        else repository.get_preview_snapshot()
    )
    return _present(snapshot)


def preview_interaction(user_text: str, current: dict | None = None) -> dict:
    internal = _internal(current or get_state(persist_advance=False))
    preview = engine.apply_fallback_interaction(internal, user_text)
    preview["affect"]["last_user_message_at"] = db.now()
    return _present(preview)


def save_state(
    state: dict,
    *,
    source_session_id: str | None = None,
    source_message_id: str | None = None,
) -> dict:
    saved = repository.save_snapshot(
        _internal(state),
        event_type="interaction",
        source="fallback",
        reason="成功完成一轮对话，应用保守本地状态变化",
        source_session_id=source_session_id,
        source_message_id=source_message_id,
    )
    return _present(saved)


def commit_interaction(
    user_text: str,
    *,
    source_session_id: str | None = None,
    source_message_id: str | None = None,
) -> dict:
    saved = repository.apply_interaction(
        user_text,
        source_session_id=source_session_id,
        source_message_id=source_message_id,
    )
    return _present(saved)


def reset_state() -> dict:
    return _present(repository.reset())


def tick(minutes: float) -> dict:
    return _present(repository.advance_by(minutes))


def list_events(limit: int = 50) -> list[dict]:
    return repository.list_events(limit)


def get_style_guidance(state: dict) -> str:
    derived = state.get("derived") if isinstance(state, dict) else None
    if derived and derived.get("style_guidance"):
        return derived["style_guidance"]
    return tone_grid.style_guidance(_internal(state))


def _present(snapshot: dict) -> dict:
    internal = _internal(snapshot)
    derived = tone_grid.describe(internal)
    affect = dict(internal["affect"])
    affect.pop("id", None)
    affect["guardedness"] = derived["guardedness"]
    relationship = dict(internal["relationship"])
    relationship.pop("id", None)
    return {
        "affect": affect,
        "relationship": relationship,
        "derived": derived,
        "signals": engine.signals(internal),
        "algorithm_version": engine.ALGORITHM_VERSION,
    }


def _internal(snapshot: dict) -> dict:
    if "affect" in snapshot and "relationship" in snapshot:
        affect = dict(snapshot["affect"])
        affect.pop("guardedness", None)
        return {"affect": affect, "relationship": dict(snapshot["relationship"])}
    raise ValueError("invalid affect snapshot")

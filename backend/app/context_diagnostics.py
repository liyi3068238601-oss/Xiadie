"""Body-free CTX.6 diagnostics ledger.

Only numeric budgets, counts, versions and stable identifiers are persisted.
Conversation, summary, memory and knowledge bodies never enter this table.
"""
from __future__ import annotations

import json
from typing import Mapping

from . import db


def record(*, session_id: str, user_message_id: str | None,
           meta: Mapping[str, object]) -> str:
    event_id = db.new_id()
    trimmed_messages = _integer(meta.get("trimmed_messages"))
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO context_package_events("
            "id,session_id,user_message_id,package_protocol_version,budget_protocol_version,"
            "context_window_tokens,output_reserve_tokens,trimmed_messages,trimmed_rounds,"
            "trim_reason,summary_revision,source_type_counts_json,component_tokens_json,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id, session_id, user_message_id,
                str(meta.get("package_protocol_version") or "unknown"),
                str(meta.get("protocol_version") or "unknown"),
                _integer(meta.get("context_window_tokens")),
                _integer(meta.get("output_reserve_tokens")),
                trimmed_messages, _integer(meta.get("trimmed_rounds")),
                "budget" if trimmed_messages else "none",
                _optional_integer(meta.get("summary_revision")),
                _safe_counts(meta.get("source_type_counts")),
                _safe_counts(meta.get("component_tokens")), db.now(),
            ),
        )
        conn.commit()
        return event_id
    finally:
        conn.close()


def list_events(*, session_id: str | None = None, limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM context_package_events WHERE session_id=?"
                " ORDER BY created_at DESC,id DESC LIMIT ?",
                (session_id, _limit(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM context_package_events ORDER BY created_at DESC,id DESC LIMIT ?",
                (_limit(limit),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_type_counts"] = json.loads(item.pop("source_type_counts_json") or "{}")
            item["component_tokens"] = json.loads(item.pop("component_tokens_json") or "{}")
            result.append(item)
        return result
    finally:
        conn.close()


def _safe_counts(value: object) -> str:
    source = value if isinstance(value, Mapping) else {}
    safe = {str(key): max(0, _integer(item)) for key, item in source.items()}
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _integer(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _limit(value: int) -> int:
    return max(1, min(int(value), 200))

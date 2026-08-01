"""Local read-only unified runtime audit feed."""
from __future__ import annotations

from collections import Counter
import json
import sqlite3

from . import db

CATEGORIES = frozenset({"model", "reasoning", "retrieval", "context", "tool", "system"})
STATUS_GROUPS = frozenset({"success", "warning", "error", "pending"})
MIN_LIMIT = 1
MAX_LIMIT = 500
PREVIEW_CHARS = 80


class RuntimeLogError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RuntimeLogNotFound(LookupError):
    code = "runtime_log_not_found"


def list_feed(*, category: str | None = None, status: str | None = None,
              limit: int = 200) -> dict[str, object]:
    if category and category not in CATEGORIES:
        raise RuntimeLogError("runtime_log_category_invalid")
    if status and status not in STATUS_GROUPS:
        raise RuntimeLogError("runtime_log_status_invalid")
    bounded = max(MIN_LIMIT, min(int(limit), MAX_LIMIT))
    conn = db.connect()
    try:
        items = [
            *_chat_events(conn, bounded),
            *_decision_events(conn, bounded),
            *_model_worker_events(conn, bounded),
            *_retrieval_events(conn, bounded),
            *_context_events(conn, bounded),
            *_tool_events(conn, bounded),
            *_transition_events(conn, bounded),
        ]
    finally:
        conn.close()
    items.sort(key=lambda item: (float(item["created_at"]), str(item["id"])), reverse=True)
    window = items[:bounded]
    all_counts = Counter(str(item["category"]) for item in window)
    if category:
        window = [item for item in window if item["category"] == category]
    if status:
        window = [item for item in window if item["status_group"] == status]
    return {
        "items": window,
        "counts": {key: all_counts.get(key, 0) for key in sorted(CATEGORIES)},
        "total": len(window),
        "privacy_notice": (
            "本页会展示本地保存的对话输入和助手最终回复；不展示系统提示词、隐藏思维链、"
            "密钥、知识正文、记忆正文或模型原始内部输出。删除原会话后，聊天详情不可恢复。"
        ),
    }


def get_detail(event_id: str) -> dict[str, object]:
    source, separator, raw_id = str(event_id or "").partition(":")
    if separator != ":" or source != "chat" or not raw_id:
        raise RuntimeLogNotFound()
    conn = db.connect()
    try:
        assistant = conn.execute(
            "SELECT id,session_id,content,model,created_at FROM messages "
            "WHERE id=? AND role='assistant'",
            (raw_id,),
        ).fetchone()
        if assistant is None:
            raise RuntimeLogNotFound()
        previous = conn.execute(
            "SELECT id,created_at FROM messages WHERE session_id=? AND role='assistant' "
            "AND (created_at,id)<(?,?) ORDER BY created_at DESC,id DESC LIMIT 1",
            (assistant["session_id"], assistant["created_at"], assistant["id"]),
        ).fetchone()
        previous_created_at = previous["created_at"] if previous else -1.0e308
        previous_id = previous["id"] if previous else ""
        inputs = conn.execute(
            "SELECT id,content,created_at FROM messages WHERE session_id=? AND role='user' "
            "AND (created_at,id)>(?,?) AND (created_at,id)<(?,?) "
            "ORDER BY created_at,id",
            (
                assistant["session_id"], previous_created_at, previous_id,
                assistant["created_at"], assistant["id"],
            ),
        ).fetchall()
    finally:
        conn.close()
    return {
        "id": f"chat:{assistant['id']}",
        "source": "chat",
        "session_id": assistant["session_id"],
        "assistant": {
            "message_id": assistant["id"],
            "content": assistant["content"],
            "model": assistant["model"] or "mock",
            "created_at": float(assistant["created_at"]),
        },
        "inputs": [
            {
                "message_id": item["id"],
                "content": item["content"],
                "created_at": float(item["created_at"]),
            }
            for item in inputs
        ],
        "representation": "persisted-turn-final-v1",
    }


def _rows(conn, sql: str, limit: int) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, (limit,)).fetchall()]


def _optional_rows(conn, sql: str, limit: int) -> list[dict]:
    try:
        return _rows(conn, sql, limit)
    except sqlite3.OperationalError as exc:
        if "no such table:" in str(exc).casefold():
            return []
        raise


def _event(*, source: str, event_id: object, category: str, title: str,
           summary: str, status: object, created_at: object,
           details: dict[str, object], detail_available: bool = False) -> dict[str, object]:
    raw_status = str(status or "unknown")
    return {
        "id": f"{source}:{event_id}",
        "source": source,
        "category": category,
        "title": title,
        "summary": summary,
        "status": raw_status,
        "status_group": _status_group(raw_status),
        "created_at": float(created_at or 0),
        "details": {key: value for key, value in details.items() if value is not None},
        "detail_available": detail_available,
    }


def _chat_events(conn, limit: int) -> list[dict[str, object]]:
    rows = _rows(conn, """
        WITH targets AS (
            SELECT id,session_id,model,created_at,substr(content,1,241) AS output_preview
            FROM messages WHERE role='assistant'
            ORDER BY created_at DESC,id DESC LIMIT ?
        ), bounds AS (
            SELECT target.*,
                COALESCE((
                    SELECT previous.created_at FROM messages AS previous
                    WHERE previous.session_id=target.session_id AND previous.role='assistant'
                      AND (previous.created_at,previous.id)<(target.created_at,target.id)
                    ORDER BY previous.created_at DESC,previous.id DESC LIMIT 1
                ),-1.0e308) AS previous_created_at,
                COALESCE((
                    SELECT previous.id FROM messages AS previous
                    WHERE previous.session_id=target.session_id AND previous.role='assistant'
                      AND (previous.created_at,previous.id)<(target.created_at,target.id)
                    ORDER BY previous.created_at DESC,previous.id DESC LIMIT 1
                ),'') AS previous_id
            FROM targets AS target
        )
        SELECT bounds.*,
            (SELECT COUNT(*) FROM messages AS input
             WHERE input.session_id=bounds.session_id AND input.role='user'
               AND (input.created_at,input.id)>(bounds.previous_created_at,bounds.previous_id)
               AND (input.created_at,input.id)<(bounds.created_at,bounds.id)) AS input_count,
            (SELECT substr(input.content,1,81) FROM messages AS input
             WHERE input.session_id=bounds.session_id AND input.role='user'
               AND (input.created_at,input.id)>(bounds.previous_created_at,bounds.previous_id)
               AND (input.created_at,input.id)<(bounds.created_at,bounds.id)
             ORDER BY input.created_at,input.id LIMIT 1) AS input_preview_1,
            (SELECT substr(input.content,1,81) FROM messages AS input
             WHERE input.session_id=bounds.session_id AND input.role='user'
               AND (input.created_at,input.id)>(bounds.previous_created_at,bounds.previous_id)
               AND (input.created_at,input.id)<(bounds.created_at,bounds.id)
             ORDER BY input.created_at,input.id LIMIT 1 OFFSET 1) AS input_preview_2,
            (SELECT substr(input.content,1,81) FROM messages AS input
             WHERE input.session_id=bounds.session_id AND input.role='user'
               AND (input.created_at,input.id)>(bounds.previous_created_at,bounds.previous_id)
               AND (input.created_at,input.id)<(bounds.created_at,bounds.id)
             ORDER BY input.created_at,input.id LIMIT 1 OFFSET 2) AS input_preview_3
        FROM bounds
        ORDER BY created_at DESC,id DESC
    """, limit)
    events = []
    for row in rows:
        input_previews = [
            _preview(row.get(key))
            for key in ("input_preview_1", "input_preview_2", "input_preview_3")
            if row.get(key)
        ]
        input_count = int(row.get("input_count") or 0)
        if input_count > len(input_previews):
            input_previews.append(f"另有 {input_count - len(input_previews)} 条")
        input_summary = " / ".join(input_previews) if input_previews else "无前置用户输入"
        events.append(_event(
            source="chat", event_id=row["id"], category="model", title="对话模型回复",
            summary=f"输入：{input_summary} → 输出：{_preview(row.get('output_preview'))}",
            status="completed", created_at=row["created_at"], detail_available=True,
            details={
                "model": row.get("model") or "mock", "session_id": row["session_id"],
                "message_id": row["id"], "input_count": input_count,
            },
        ))
    return events


def _decision_events(conn, limit: int) -> list[dict[str, object]]:
    rows = _optional_rows(conn, "SELECT id,task_kind,protocol_version,status,provider_id,model_id,"
                          "latency_ms,input_tokens,output_tokens,error_code,action,confidence_band,"
                          "reason_codes_json,fallback_used,logical_role,created_at FROM decision_runs "
                          "ORDER BY created_at DESC,id DESC LIMIT ?", limit)
    events = []
    for row in rows:
        reasons = _json_list(row.get("reason_codes_json"))
        summary = " · ".join(part for part in (
            str(row.get("action") or "决策处理中"), ", ".join(reasons[:3]),
            str(row.get("error_code") or ""),
        ) if part)
        events.append(_event(
            source="decision", event_id=row["id"], category="reasoning",
            title=str(row.get("task_kind") or "模型决策"), summary=summary,
            status=row.get("status"), created_at=row["created_at"], details={
                "protocol_version": row.get("protocol_version"),
                "provider_id": row.get("provider_id"), "model": row.get("model_id"),
                "logical_role": row.get("logical_role"), "confidence": row.get("confidence_band"),
                "latency_ms": row.get("latency_ms"), "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "fallback_used": bool(row.get("fallback_used")),
                "error_code": row.get("error_code"), "reason_codes": reasons,
            },
        ))
    return events


def _model_worker_events(conn, limit: int) -> list[dict[str, object]]:
    specs = (
        ("memory_observer_runs", "记忆观察", "source_assistant_message_id", "latency_ms"),
        ("affect_observer_runs", "情绪观察", "source_assistant_message_id", "NULL AS latency_ms"),
        ("conversation_summary_runs", "会话摘要", "source_end_message_id", "latency_ms"),
    )
    events: list[dict[str, object]] = []
    for table, title, source_column, latency_column in specs:
        rows = _optional_rows(
            conn,
            f"SELECT id,status,provider_id,model,error_code,{latency_column},prompt_tokens,"
            f"completion_tokens,protocol_version,created_at,{source_column} FROM {table} "
            f"ORDER BY created_at DESC,id DESC LIMIT ?",
            limit,
        )
        for row in rows:
            events.append(_event(
                source=table, event_id=row["id"], category="model", title=title,
                summary=(f"{row.get('model') or row.get('provider_id') or '模型'} · "
                         f"{row.get('error_code') or row.get('status') or 'unknown'}"),
                status=row.get("status"), created_at=row["created_at"], details={
                    "provider_id": row.get("provider_id"), "model": row.get("model"),
                    "protocol_version": row.get("protocol_version"),
                    "latency_ms": row.get("latency_ms"), "prompt_tokens": row.get("prompt_tokens"),
                    "completion_tokens": row.get("completion_tokens"),
                    "error_code": row.get("error_code"),
                    "source_message_id": row.get(source_column),
                },
            ))
    return events


def _retrieval_events(conn, limit: int) -> list[dict[str, object]]:
    rows = _optional_rows(conn, "SELECT id,action,reason_code,confidence_band,candidate_count,"
                          "eligible_count,injected_count,retrieval_mode,vector_available,vector_error_code,"
                          "provider_id,latency_ms,status,created_at FROM knowledge_recall_decisions "
                          "ORDER BY created_at DESC,id DESC LIMIT ?", limit)
    return [_event(
        source="knowledge_recall", event_id=row["id"], category="retrieval", title="知识召回",
        summary=f"{row.get('action') or 'skip'} · {row.get('reason_code') or 'unknown'}",
        status=row.get("status"), created_at=row["created_at"], details={
            "confidence": row.get("confidence_band"), "candidate_count": row.get("candidate_count"),
            "eligible_count": row.get("eligible_count"), "injected_count": row.get("injected_count"),
            "retrieval_mode": row.get("retrieval_mode"),
            "vector_available": bool(row.get("vector_available")),
            "vector_error_code": row.get("vector_error_code"),
            "provider_id": row.get("provider_id"), "latency_ms": row.get("latency_ms"),
        },
    ) for row in rows]


def _context_events(conn, limit: int) -> list[dict[str, object]]:
    rows = _optional_rows(conn, "SELECT id,package_protocol_version,budget_protocol_version,"
                          "context_window_tokens,output_reserve_tokens,trimmed_messages,trimmed_rounds,"
                          "trim_reason,source_type_counts_json,component_tokens_json,created_at "
                          "FROM context_package_events ORDER BY created_at DESC,id DESC LIMIT ?", limit)
    return [_event(
        source="context", event_id=row["id"], category="context", title="上下文装配",
        summary=f"窗口 {row.get('context_window_tokens') or 0} · 裁剪 {row.get('trimmed_messages') or 0} 条",
        status="warning" if int(row.get("trimmed_messages") or 0) else "completed",
        created_at=row["created_at"], details={
            "package_protocol_version": row.get("package_protocol_version"),
            "budget_protocol_version": row.get("budget_protocol_version"),
            "context_window_tokens": row.get("context_window_tokens"),
            "output_reserve_tokens": row.get("output_reserve_tokens"),
            "trimmed_messages": row.get("trimmed_messages"),
            "trimmed_rounds": row.get("trimmed_rounds"), "trim_reason": row.get("trim_reason"),
            "source_type_counts": _json_dict(row.get("source_type_counts_json")),
            "component_tokens": _json_dict(row.get("component_tokens_json")),
        },
    ) for row in rows]


def _tool_events(conn, limit: int) -> list[dict[str, object]]:
    rows = _rows(conn, "SELECT id,tool,risk_level,status,summary,created_at FROM tool_logs "
                       "ORDER BY created_at DESC,id DESC LIMIT ?", limit)
    return [_event(
        source="tool", event_id=row["id"], category="tool",
        title=str(row.get("tool") or "工具调用"), summary=str(row.get("summary") or ""),
        status=row.get("status"), created_at=row["created_at"],
        details={"risk_level": row.get("risk_level"), "tool_run_id": row["id"]},
    ) for row in rows]


def _transition_events(conn, limit: int) -> list[dict[str, object]]:
    rows = _optional_rows(conn, "SELECT id,run_id,event_type,from_status,to_status,mode,error_code,"
                          "warning_codes_json,created_at FROM decision_run_events "
                          "ORDER BY created_at DESC,id DESC LIMIT ?", limit)
    return [_event(
        source="decision_transition", event_id=row["id"], category="system", title="决策状态变化",
        summary=f"{row.get('from_status') or 'new'} → {row.get('to_status') or 'unknown'}",
        status=row.get("to_status"), created_at=row["created_at"], details={
            "run_id": row.get("run_id"), "event_type": row.get("event_type"),
            "mode": row.get("mode"), "error_code": row.get("error_code"),
            "warning_codes": _json_list(row.get("warning_codes_json")),
        },
    ) for row in rows]


def _preview(value: object) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= PREVIEW_CHARS:
        return normalized
    return normalized[:PREVIEW_CHARS].rstrip() + "…"


def _json_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_dict(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _status_group(status: str) -> str:
    folded = status.casefold()
    if any(marker in folded for marker in ("fail", "error", "reject", "denied", "blocked", "recovery")):
        return "error"
    if any(marker in folded for marker in ("warn", "partial", "degraded", "insufficient")):
        return "warning"
    if any(marker in folded for marker in ("queue", "pending", "running", "await")):
        return "pending"
    if any(marker in folded for marker in ("complete", "applied", "success", "done", "allow", "executed")):
        return "success"
    return "warning"

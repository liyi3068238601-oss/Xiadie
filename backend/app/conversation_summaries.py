"""会话摘要的派生数据账本与生命周期。

CTX.2 只建立可追溯的数据地基，不生成摘要、不调用模型，也不把摘要注入聊天。
所有写操作仅供后端内部 worker 使用；HTTP 层只暴露无正文诊断。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Mapping, Sequence

from . import db

PROTOCOL_VERSION = "conversation-summary-v1"
MAX_ATTEMPTS = 3
LEASE_SECONDS = 60
FIRST_RETRY_DELAY_SECONDS = 60
RUNNING_STATUSES = frozenset({"queued", "running", "recovery_pending"})
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "exhausted", "cancelled"})
REVISION_STATUSES = frozenset({"active", "superseded", "invalid", "failed"})
_EVENT_METADATA_KEYS = frozenset({
    "attempt", "max_attempts", "source_message_count", "revision",
    "lease_seconds", "invalidated_revision_count", "invalidated_run_count",
})


class ConversationSummaryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceSnapshot:
    session_id: str
    start_message_id: str
    end_message_id: str
    message_count: int
    source_hash: str


def _ordered_messages(conn, session_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT id,role,content,model,created_at FROM messages"
        " WHERE session_id=? ORDER BY created_at,id",
        (session_id,),
    ).fetchall()]


def _complete_turn_range(messages: Sequence[Mapping[str, object]],
                         start_message_id: str | None,
                         end_message_id: str | None) -> list[dict]:
    rows = [dict(message) for message in messages]
    if not rows:
        raise ConversationSummaryError("summary_source_empty", "会话还没有可摘要的完整对话")
    positions = {str(row["id"]): index for index, row in enumerate(rows)}
    start = positions.get(start_message_id) if start_message_id else 0
    end = positions.get(end_message_id) if end_message_id else len(rows) - 1
    if start is None or end is None or start > end:
        raise ConversationSummaryError("summary_source_range_invalid", "摘要来源范围不存在或顺序无效")
    selected = rows[start:end + 1]
    if len(selected) < 2 or len(selected) % 2:
        raise ConversationSummaryError("summary_source_turn_incomplete", "摘要来源必须由完整对话轮次组成")
    for index in range(0, len(selected), 2):
        if selected[index]["role"] != "user" or selected[index + 1]["role"] != "assistant":
            raise ConversationSummaryError("summary_source_turn_incomplete", "摘要来源必须保持 user/assistant 配对")
    return selected


def _source_hash(messages: Sequence[Mapping[str, object]]) -> str:
    canonical = [
        {
            "id": str(message["id"]),
            "role": str(message["role"]),
            "content": str(message.get("content") or ""),
            "model": str(message.get("model") or ""),
        }
        for message in messages
    ]
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def source_snapshot(conn, session_id: str, *, start_message_id: str | None = None,
                    end_message_id: str | None = None) -> SourceSnapshot:
    if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
        raise ConversationSummaryError("summary_session_missing", "会话不存在")
    selected = _complete_turn_range(
        _ordered_messages(conn, session_id), start_message_id, end_message_id,
    )
    return SourceSnapshot(
        session_id=session_id,
        start_message_id=str(selected[0]["id"]),
        end_message_id=str(selected[-1]["id"]),
        message_count=len(selected),
        source_hash=_source_hash(selected),
    )


def enqueue(session_id: str, *, start_message_id: str | None = None,
            end_message_id: str | None = None) -> dict:
    """为连续完整来源建立幂等 run；不启动模型或后台生成。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = source_snapshot(
            conn, session_id, start_message_id=start_message_id,
            end_message_id=end_message_id,
        )
        key = ":".join((
            PROTOCOL_VERSION, session_id, snapshot.start_message_id,
            snapshot.end_message_id, snapshot.source_hash,
        ))
        now = db.now()
        run_id = db.new_id()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO conversation_summary_runs("
            "id,idempotency_key,session_id,status,protocol_version,"
            "source_start_message_id,source_end_message_id,source_message_count,source_hash,"
            "max_attempts,next_attempt_at,created_at,updated_at)"
            " VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?,?)",
            (
                run_id, key, session_id, PROTOCOL_VERSION,
                snapshot.start_message_id, snapshot.end_message_id,
                snapshot.message_count, snapshot.source_hash, MAX_ATTEMPTS,
                now, now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM conversation_summary_runs WHERE idempotency_key=?", (key,),
        ).fetchone()
        if cursor.rowcount:
            _event(
                conn, session_id=session_id, run_id=run_id, action="enqueued",
                before_status=None, after_status="queued", reason_code="source_ready",
                metadata={"source_message_count": snapshot.message_count,
                          "max_attempts": MAX_ATTEMPTS}, now=now,
            )
        conn.commit()
        return _run_public(conn, row, include_events=True)
    finally:
        conn.close()


def claim_next(*, lease_seconds: int = LEASE_SECONDS) -> dict | None:
    lease = max(10, min(int(lease_seconds), 10 * 60))
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = db.now()
        row = conn.execute(
            "SELECT * FROM conversation_summary_runs WHERE"
            " (status='queued' OR (status='recovery_pending' AND next_attempt_at<=?))"
            " AND attempt_count<max_attempts ORDER BY created_at,id LIMIT 1",
            (now,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        token = secrets.token_urlsafe(24)
        attempt = row["attempt_count"] + 1
        conn.execute(
            "UPDATE conversation_summary_runs SET status='running',attempt_count=?,"
            "lease_token=?,lease_expires_at=?,heartbeat_at=?,next_attempt_at=NULL,"
            "error_code=NULL,started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",
            (attempt, token, now + lease, now, now, now, row["id"]),
        )
        _event(
            conn, session_id=row["session_id"], run_id=row["id"], action="claimed",
            before_status=row["status"], after_status="running", reason_code="worker_claimed",
            metadata={"attempt": attempt, "lease_seconds": lease}, now=now,
        )
        conn.commit()
        result = dict(row)
        result.update(
            status="running", attempt_count=attempt, lease_token=token,
            lease_expires_at=now + lease, heartbeat_at=now, updated_at=now,
        )
        return result
    finally:
        conn.close()


def heartbeat(run_id: str, lease_token: str, *, lease_seconds: int = LEASE_SECONDS) -> bool:
    lease = max(10, min(int(lease_seconds), 10 * 60))
    conn = db.connect()
    try:
        now = db.now()
        cursor = conn.execute(
            "UPDATE conversation_summary_runs SET heartbeat_at=?,lease_expires_at=?,updated_at=?"
            " WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?",
            (now, now + lease, now, run_id, lease_token, now),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def recover_stale_runs(*, now: float | None = None) -> int:
    timestamp = db.now() if now is None else float(now)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM conversation_summary_runs WHERE status='running'"
            " AND lease_expires_at IS NOT NULL AND lease_expires_at<?",
            (timestamp,),
        ).fetchall()
        for row in rows:
            if row["attempt_count"] >= row["max_attempts"]:
                status, action, finished = "exhausted", "exhausted", timestamp
            else:
                status, action, finished = "recovery_pending", "recovery_scheduled", None
            conn.execute(
                "UPDATE conversation_summary_runs SET status=?,lease_token=NULL,"
                "lease_expires_at=NULL,heartbeat_at=NULL,next_attempt_at=?,error_code=?,"
                "finished_at=?,updated_at=? WHERE id=?",
                (
                    status, timestamp if status == "recovery_pending" else None,
                    "summary_worker_interrupted", finished, timestamp, row["id"],
                ),
            )
            _event(
                conn, session_id=row["session_id"], run_id=row["id"], action=action,
                before_status="running", after_status=status,
                reason_code="summary_worker_interrupted",
                metadata={"attempt": row["attempt_count"],
                          "max_attempts": row["max_attempts"]}, now=timestamp,
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def fail_run(run_id: str, lease_token: str, error_code: str, *, retryable: bool) -> dict:
    error_code = _stable_code(error_code, "summary_failure")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _leased_run(conn, run_id, lease_token)
        now = db.now()
        retry = retryable and row["attempt_count"] < row["max_attempts"]
        if retry:
            status, action, next_attempt, finished = (
                "recovery_pending", "retry_scheduled", now + FIRST_RETRY_DELAY_SECONDS, None,
            )
        else:
            status = "exhausted" if retryable else "failed"
            action, next_attempt, finished = "exhausted" if retryable else "failed", None, now
        failed_revision_id = None
        if not retry:
            failed_revision_id = _insert_failed_revision_locked(conn, row, error_code, now)
        conn.execute(
            "UPDATE conversation_summary_runs SET status=?,lease_token=NULL,lease_expires_at=NULL,"
            "heartbeat_at=NULL,next_attempt_at=?,error_code=?,result_revision_id=?,"
            "finished_at=?,updated_at=? WHERE id=?",
            (status, next_attempt, error_code, failed_revision_id, finished, now, run_id),
        )
        _event(
            conn, session_id=row["session_id"], run_id=run_id, action=action,
            before_status="running", after_status=status, reason_code=error_code,
            metadata={"attempt": row["attempt_count"],
                      "max_attempts": row["max_attempts"]}, now=now,
        )
        conn.commit()
        return get_run(run_id) or {}
    finally:
        conn.close()


def activate_result(run_id: str, lease_token: str, summary: Mapping[str, object], *,
                    provider_id: str | None = None, model: str | None = None,
                    prompt_tokens: int | None = None,
                    completion_tokens: int | None = None) -> dict:
    """原子验证来源并激活结果；调用方只能传已通过 CTX.3 协议校验的数据。"""
    summary_text = str(
        summary.get("summary_text") or summary.get("continuity") or ""
    ).strip()
    if not summary_text:
        raise ConversationSummaryError("summary_result_invalid", "摘要正文为空")
    open_threads = _json_list(summary.get("open_threads", []), "open_threads")
    decisions = _json_list(summary.get("decisions", []), "decisions")
    corrections = _json_list(summary.get("corrections", []), "corrections")
    entity_refs = _json_list(summary.get("entity_refs", []), "entity_refs")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _leased_run(conn, run_id, lease_token)
        now = db.now()
        try:
            current = source_snapshot(
                conn, row["session_id"],
                start_message_id=row["source_start_message_id"],
                end_message_id=row["source_end_message_id"],
            )
        except ConversationSummaryError:
            current = None
        if current is None or current.source_hash != row["source_hash"]:
            conn.execute(
                "UPDATE conversation_summary_runs SET status='failed',lease_token=NULL,"
                "lease_expires_at=NULL,heartbeat_at=NULL,error_code='summary_source_changed',"
                "finished_at=?,updated_at=? WHERE id=?",
                (now, now, run_id),
            )
            _event(
                conn, session_id=row["session_id"], run_id=run_id,
                action="failed", before_status="running", after_status="failed",
                reason_code="summary_source_changed", metadata={}, now=now,
            )
            conn.commit()
            raise ConversationSummaryError(
                "summary_source_changed", "摘要生成期间原始消息发生变化，旧结果已拒绝落库",
            )

        existing = conn.execute(
            "SELECT * FROM conversation_summary_revisions WHERE session_id=?"
            " AND source_hash=? AND protocol_version=? AND status='active'",
            (row["session_id"], row["source_hash"], row["protocol_version"]),
        ).fetchone()
        if existing:
            _complete_run_locked(conn, row, existing["id"], now, "existing_revision_reused")
            conn.commit()
            return _revision_public(existing)

        revision_number = conn.execute(
            "SELECT COALESCE(MAX(revision),0)+1 value FROM conversation_summary_revisions"
            " WHERE session_id=?", (row["session_id"],),
        ).fetchone()["value"]
        previous = conn.execute(
            "SELECT * FROM conversation_summary_revisions WHERE session_id=? AND status='active'",
            (row["session_id"],),
        ).fetchone()
        if previous:
            conn.execute(
                "UPDATE conversation_summary_revisions SET status='superseded',"
                "superseded_at=?,updated_at=?"
                " WHERE id=? AND status='active'", (now, now, previous["id"]),
            )
            _event(
                conn, session_id=row["session_id"], revision_id=previous["id"],
                action="superseded", before_status="active", after_status="superseded",
                reason_code="newer_revision_activated", metadata={}, now=now,
            )
        revision_id = db.new_id()
        conn.execute(
            "INSERT INTO conversation_summary_revisions("
            "id,session_id,run_id,revision,status,protocol_version,"
            "source_start_message_id,source_end_message_id,source_message_count,source_hash,"
            "summary_text,open_threads_json,decisions_json,corrections_json,entity_refs_json,"
            "provider_id,model,prompt_tokens,completion_tokens,created_at,updated_at,activated_at)"
            " VALUES(?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                revision_id, row["session_id"], run_id, revision_number,
                row["protocol_version"], row["source_start_message_id"],
                row["source_end_message_id"], row["source_message_count"], row["source_hash"],
                summary_text, open_threads, decisions, corrections, entity_refs,
                provider_id, model, _nonnegative_optional(prompt_tokens),
                _nonnegative_optional(completion_tokens), now, now, now,
            ),
        )
        _event(
            conn, session_id=row["session_id"], run_id=run_id,
            revision_id=revision_id, action="activated", before_status=None,
            after_status="active", reason_code="source_verified",
            metadata={"revision": revision_number,
                      "source_message_count": row["source_message_count"]}, now=now,
        )
        _complete_run_locked(conn, row, revision_id, now, "revision_activated")
        conn.commit()
        created = conn.execute(
            "SELECT * FROM conversation_summary_revisions WHERE id=?", (revision_id,),
        ).fetchone()
        return _revision_public(created)
    finally:
        conn.close()


def invalidate_for_replaced_message_locked(conn, session_id: str, message_id: str) -> dict:
    """在 regenerate 删除旧回复前失效覆盖它的摘要和未完成任务。"""
    messages = _ordered_messages(conn, session_id)
    positions = {str(row["id"]): index for index, row in enumerate(messages)}
    target = positions.get(message_id)
    if target is None:
        return {"invalidated_revision_count": 0, "invalidated_run_count": 0}
    now = db.now()
    revisions = conn.execute(
        "SELECT * FROM conversation_summary_revisions WHERE session_id=? AND status='active'",
        (session_id,),
    ).fetchall()
    invalidated_revisions = 0
    for row in revisions:
        start = positions.get(row["source_start_message_id"])
        end = positions.get(row["source_end_message_id"])
        if start is None or end is None or not start <= target <= end:
            continue
        conn.execute(
            "UPDATE conversation_summary_revisions SET status='invalid',error_code=?,"
            "invalidated_at=?,updated_at=? WHERE id=? AND status='active'",
            ("source_message_replaced", now, now, row["id"]),
        )
        _event(
            conn, session_id=session_id, revision_id=row["id"], action="invalidated",
            before_status="active", after_status="invalid",
            reason_code="source_message_replaced", metadata={}, now=now,
        )
        invalidated_revisions += 1

    runs = conn.execute(
        "SELECT * FROM conversation_summary_runs WHERE session_id=?"
        " AND status IN ('queued','running','recovery_pending')", (session_id,),
    ).fetchall()
    invalidated_runs = 0
    for row in runs:
        start = positions.get(row["source_start_message_id"])
        end = positions.get(row["source_end_message_id"])
        if start is None or end is None or not start <= target <= end:
            continue
        conn.execute(
            "UPDATE conversation_summary_runs SET status='failed',lease_token=NULL,"
            "lease_expires_at=NULL,heartbeat_at=NULL,next_attempt_at=NULL,error_code=?,"
            "finished_at=?,updated_at=? WHERE id=?",
            ("source_message_replaced", now, now, row["id"]),
        )
        _event(
            conn, session_id=session_id, run_id=row["id"], action="failed",
            before_status=row["status"], after_status="failed",
            reason_code="source_message_replaced", metadata={}, now=now,
        )
        invalidated_runs += 1
    return {
        "invalidated_revision_count": invalidated_revisions,
        "invalidated_run_count": invalidated_runs,
    }


def list_runs(*, session_id: str | None = None, limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM conversation_summary_runs WHERE session_id=?"
                " ORDER BY created_at DESC,id DESC LIMIT ?", (session_id, _limit(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversation_summary_runs ORDER BY created_at DESC,id DESC LIMIT ?",
                (_limit(limit),),
            ).fetchall()
        return [_run_public(conn, row, include_events=False) for row in rows]
    finally:
        conn.close()


def get_run(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM conversation_summary_runs WHERE id=?", (run_id,),
        ).fetchone()
        return _run_public(conn, row, include_events=True) if row else None
    finally:
        conn.close()


def list_revisions(session_id: str, *, limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conversation_summary_revisions WHERE session_id=?"
            " ORDER BY revision DESC LIMIT ?", (session_id, _limit(limit)),
        ).fetchall()
        return [_revision_public(row) for row in rows]
    finally:
        conn.close()


def list_events(session_id: str, *, limit: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conversation_summary_events WHERE session_id=?"
            " ORDER BY created_at DESC,id DESC LIMIT ?", (session_id, _limit(limit)),
        ).fetchall()
        return [_event_public(row) for row in rows]
    finally:
        conn.close()


def _leased_run(conn, run_id: str, lease_token: str):
    row = conn.execute(
        "SELECT * FROM conversation_summary_runs WHERE id=?", (run_id,),
    ).fetchone()
    now = db.now()
    if not row:
        raise ConversationSummaryError("summary_run_missing", "摘要任务不存在")
    if (row["status"] != "running" or row["lease_token"] != lease_token
            or row["lease_expires_at"] is None or row["lease_expires_at"] < now):
        raise ConversationSummaryError("summary_run_lease_invalid", "摘要任务租约无效或已过期")
    return row


def _complete_run_locked(conn, row, revision_id: str, now: float, reason_code: str) -> None:
    conn.execute(
        "UPDATE conversation_summary_runs SET status='completed',lease_token=NULL,"
        "lease_expires_at=NULL,heartbeat_at=NULL,result_revision_id=?,finished_at=?,updated_at=?"
        " WHERE id=?", (revision_id, now, now, row["id"]),
    )


def _insert_failed_revision_locked(conn, row, error_code: str, now: float) -> str | None:
    """只在来源仍可验证时记录失败 revision；失败不会替换现有 active。"""
    try:
        current = source_snapshot(
            conn, row["session_id"],
            start_message_id=row["source_start_message_id"],
            end_message_id=row["source_end_message_id"],
        )
    except ConversationSummaryError:
        return None
    if current.source_hash != row["source_hash"]:
        return None
    revision_number = conn.execute(
        "SELECT COALESCE(MAX(revision),0)+1 value FROM conversation_summary_revisions"
        " WHERE session_id=?", (row["session_id"],),
    ).fetchone()["value"]
    revision_id = db.new_id()
    conn.execute(
        "INSERT INTO conversation_summary_revisions("
        "id,session_id,run_id,revision,status,protocol_version,"
        "source_start_message_id,source_end_message_id,source_message_count,source_hash,"
        "summary_text,error_code,created_at,updated_at)"
        " VALUES(?,?,?,?,'failed',?,?,?,?,?,NULL,?,?,?)",
        (
            revision_id, row["session_id"], row["id"], revision_number,
            row["protocol_version"], row["source_start_message_id"],
            row["source_end_message_id"], row["source_message_count"], row["source_hash"],
            error_code, now, now,
        ),
    )
    _event(
        conn, session_id=row["session_id"], run_id=row["id"], revision_id=revision_id,
        action="revision_failed", before_status=None, after_status="failed",
        reason_code=error_code, metadata={"revision": revision_number}, now=now,
    )
    return revision_id
    _event(
        conn, session_id=row["session_id"], run_id=row["id"], revision_id=revision_id,
        action="completed", before_status="running", after_status="completed",
        reason_code=reason_code, metadata={}, now=now,
    )


def _safe_metadata(metadata: Mapping[str, object]) -> str:
    cleaned: dict[str, int | float | bool | None] = {}
    for key, value in metadata.items():
        if key not in _EVENT_METADATA_KEYS:
            raise ValueError(f"conversation summary event metadata key not allowed: {key}")
        if not isinstance(value, (int, float, bool)) and value is not None:
            raise ValueError("conversation summary event metadata only accepts scalar counters")
        cleaned[key] = value
    return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))


def _json_list(value: object, field: str) -> str:
    if not isinstance(value, list):
        raise ConversationSummaryError("summary_result_invalid", f"{field} 必须是数组")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _nonnegative_optional(value: int | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 0:
        raise ConversationSummaryError("summary_result_invalid", "token usage 不能为负数")
    return parsed


def _stable_code(value: str, fallback: str) -> str:
    candidate = str(value or "").strip()
    return candidate if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate) else fallback


def _event(conn, *, session_id: str, action: str, before_status: str | None,
           after_status: str | None, reason_code: str,
           metadata: Mapping[str, object], now: float,
           run_id: str | None = None, revision_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO conversation_summary_events("
        "id,session_id,run_id,revision_id,action,before_status,after_status,reason_code,"
        "metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            db.new_id(), session_id, run_id, revision_id,
            _stable_code(action, "event_recorded"), before_status,
            after_status, _stable_code(reason_code, "summary_event"),
            _safe_metadata(metadata), now,
        ),
    )


def _run_public(conn, row, *, include_events: bool) -> dict:
    item = dict(row)
    item.pop("lease_token", None)
    if include_events:
        events = conn.execute(
            "SELECT * FROM conversation_summary_events WHERE run_id=?"
            " ORDER BY created_at,id", (item["id"],),
        ).fetchall()
        item["events"] = [_event_public(event) for event in events]
    return item


def _revision_public(row) -> dict:
    item = dict(row)
    item["summary_present"] = bool(item.pop("summary_text", None))
    for field in ("open_threads_json", "decisions_json", "corrections_json", "entity_refs_json"):
        values = json.loads(item.pop(field) or "[]")
        item[field.removesuffix("_json") + "_count"] = len(values)
    return item


def _event_public(row) -> dict:
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


def _limit(value: int) -> int:
    return max(1, min(int(value), 200))

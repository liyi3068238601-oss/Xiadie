"""EAP 公共 run 账本工具：source_hash 计算、状态机常量、idempotency_key 生成。

按 spec 第 6.5 节"复用公共 DecisionRun"要求，本模块提供最小公共抽象：
- compute_source_hash：对输入消息列表做 JSON 规范化后 sha256，返回 64 字符 hex
- RunStatus：统一状态机常量（与 affect_observer_runs 对齐）
- make_idempotency_key：按 protocol + 关键标识生成幂等键

不强制现有 11 个 run 表迁移到此抽象；EAP 新建表时复用本模块。
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from .. import db


# 状态机常量（与 affect_observer_runs.status 对齐，spec 第 5.7 节 ContactEpisode 状态机
# 由 EAP.E 阶段扩展为 10 值）
class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    APPLIED = "applied"
    RECOVERY_PENDING = "recovery_pending"
    EXHAUSTED = "exhausted"
    SKIPPED = "skipped"


ALL_RUN_STATUSES = {
    RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.APPLIED,
    RunStatus.RECOVERY_PENDING, RunStatus.EXHAUSTED, RunStatus.SKIPPED,
}

_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.SKIPPED},
    RunStatus.RUNNING: {
        RunStatus.APPLIED, RunStatus.RECOVERY_PENDING, RunStatus.EXHAUSTED, RunStatus.SKIPPED,
    },
    RunStatus.RECOVERY_PENDING: {RunStatus.RUNNING, RunStatus.EXHAUSTED, RunStatus.SKIPPED},
    RunStatus.APPLIED: set(),
    RunStatus.EXHAUSTED: set(),
    RunStatus.SKIPPED: set(),
}


@dataclass(frozen=True)
class DecisionRun:
    id: str
    task_kind: str
    protocol_version: str
    source_type: str
    source_id: str
    source_revision: str
    source_hash: str
    idempotency_key: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: float | None
    provider_id: str | None
    model_id: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None
    warnings: list[str]
    created_at: float
    updated_at: float
    completed_at: float | None


@dataclass(frozen=True)
class LegacyRunRef:
    """Read-only adapter view; historical run tables remain owned by their subsystems."""
    legacy_table: str
    legacy_id: str
    protocol_version: str
    source_revision: str
    source_hash: str
    status: str


def adapt_legacy_run(
    row: Any, *, legacy_table: str, protocol_version: str,
    revision_field: str = "source_revision", hash_field: str = "source_hash",
) -> LegacyRunRef:
    """Expose an existing run through common identity fields without migrating its row."""
    values = dict(row)
    status = values.get("status", RunStatus.SKIPPED)
    if status not in ALL_RUN_STATUSES:
        # Existing subsystems use additional terminal names; do not mutate or overstate them.
        status = RunStatus.SKIPPED
    return LegacyRunRef(
        legacy_table=legacy_table,
        legacy_id=str(values["id"]),
        protocol_version=protocol_version,
        source_revision=str(values.get(revision_field, "")),
        source_hash=str(values.get(hash_field, "")),
        status=status,
    )


def compute_source_hash(messages: Iterable[dict[str, Any]]) -> str:
    """对消息列表做 JSON 规范化后 sha256，返回 64 字符 hex。

    参考 conversation_summaries._source_hash 的实现，独立定义以避免循环导入。
    输入消息字典的键应包含 id/role/content 等；排序后 JSON 序列化确保确定性。
    """
    normalized = [
        {"id": m.get("id"), "role": m.get("role"), "content": m.get("content")}
        for m in messages
    ]
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_idempotency_key(protocol: str, *parts: str) -> str:
    """按 protocol + 关键标识生成幂等键。

    例：make_idempotency_key(PROACTIVE_DECISION_V2, episode_id, turn_id)
    返回 "proactive-decision-v2:{episode_id}:{turn_id}"
    """
    return ":".join((protocol, *parts))


def _from_row(row) -> DecisionRun:
    return DecisionRun(
        id=row["id"], task_kind=row["task_kind"], protocol_version=row["protocol_version"],
        source_type=row["source_type"], source_id=row["source_id"],
        source_revision=row["source_revision"], source_hash=row["source_hash"],
        idempotency_key=row["idempotency_key"], status=row["status"],
        attempt_count=row["attempt_count"], max_attempts=row["max_attempts"],
        next_attempt_at=row["next_attempt_at"], provider_id=row["provider_id"],
        model_id=row["model_id"], latency_ms=row["latency_ms"],
        input_tokens=row["input_tokens"], output_tokens=row["output_tokens"],
        error_code=row["error_code"], warnings=json.loads(row["warnings_json"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def create_or_get_run(
    *, task_kind: str, protocol_version: str, source_type: str, source_id: str,
    source_revision: str, source_hash: str, idempotency_key: str,
    max_attempts: int = 3, provider_id: str | None = None,
    model_id: str | None = None, now: float | None = None,
) -> tuple[DecisionRun, bool]:
    if not all((task_kind, protocol_version, source_type, source_id, source_hash, idempotency_key)):
        raise ValueError("DecisionRun identity fields must not be empty")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        existing = conn.execute(
            "SELECT * FROM decision_runs WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing:
            return _from_row(existing), False
        run_id = db.new_id()
        try:
            conn.execute(
                "INSERT INTO decision_runs (id,task_kind,protocol_version,source_type,source_id,"
                "source_revision,source_hash,idempotency_key,status,attempt_count,max_attempts,"
                "provider_id,model_id,warnings_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, task_kind, protocol_version, source_type, source_id, source_revision,
                 source_hash, idempotency_key, RunStatus.QUEUED, 0, max_attempts,
                 provider_id, model_id, "[]", now, now),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            raced = conn.execute(
                "SELECT * FROM decision_runs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if raced:
                return _from_row(raced), False
            raise
        conn.commit()
        row = conn.execute("SELECT * FROM decision_runs WHERE id=?", (run_id,)).fetchone()
        return _from_row(row), True
    finally:
        conn.close()


def get_run(run_id: str) -> DecisionRun | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM decision_runs WHERE id=?", (run_id,)).fetchone()
        return _from_row(row) if row else None
    finally:
        conn.close()


def transition_run(
    run_id: str, status: str, *, error_code: str | None = None,
    next_attempt_at: float | None = None, provider_id: str | None = None,
    model_id: str | None = None, latency_ms: int | None = None,
    input_tokens: int | None = None, output_tokens: int | None = None,
    warnings: Iterable[str] = (), now: float | None = None,
) -> DecisionRun:
    if status not in ALL_RUN_STATUSES:
        raise ValueError("invalid DecisionRun status")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM decision_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("DecisionRun not found")
        if status not in _TRANSITIONS[row["status"]]:
            raise ValueError(f"invalid DecisionRun transition: {row['status']} -> {status}")
        attempts = row["attempt_count"] + (1 if status == RunStatus.RUNNING else 0)
        if attempts > row["max_attempts"]:
            raise ValueError("DecisionRun attempt budget exhausted")
        completed_at = now if status in {
            RunStatus.APPLIED, RunStatus.EXHAUSTED, RunStatus.SKIPPED
        } else None
        cursor = conn.execute(
            "UPDATE decision_runs SET status=?,attempt_count=?,next_attempt_at=?,provider_id=?,"
            "model_id=?,latency_ms=?,input_tokens=?,output_tokens=?,error_code=?,warnings_json=?,"
            "updated_at=?,completed_at=? WHERE id=? AND status=?",
            (status, attempts, next_attempt_at,
             provider_id if provider_id is not None else row["provider_id"],
             model_id if model_id is not None else row["model_id"],
             latency_ms if latency_ms is not None else row["latency_ms"],
             input_tokens if input_tokens is not None else row["input_tokens"],
             output_tokens if output_tokens is not None else row["output_tokens"],
             error_code if error_code is not None else row["error_code"],
             json.dumps(list(warnings), ensure_ascii=False), now,
             completed_at, run_id, row["status"]),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise ValueError("DecisionRun changed concurrently")
        conn.commit()
        return _from_row(conn.execute("SELECT * FROM decision_runs WHERE id=?", (run_id,)).fetchone())
    finally:
        conn.close()

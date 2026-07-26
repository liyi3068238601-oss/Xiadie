"""Schema 56/61 shared DecisionRun repository and body-free event ledger.

The original EAP-compatible API remains stable. CDS.1 extends the same rows with
versioned decision metadata, retention and diagnostics rather than creating a
parallel generic run table. This module provides:
- compute_source_hash：对输入消息列表做 JSON 规范化后 sha256，返回 64 字符 hex
- RunStatus：统一状态机常量（与 affect_observer_runs 对齐）
- make_idempotency_key：按 protocol + 关键标识生成幂等键

Historical domain-owned run tables are not migrated; they remain read-only adapter targets.
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
    policy_version: str
    mode: str
    source_type: str
    source_id: str
    source_revision: str
    source_hash: str
    source_snapshot: list[dict[str, Any]]
    snapshot_hash: str
    candidate_snapshot_hash: str
    idempotency_key: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: float | None
    provider_id: str | None
    model_id: str | None
    provider_location: str | None
    provider_location_revision: int | None
    logical_role: str
    certification_level: str
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None
    warnings: list[str]
    candidate_count: int
    selected_count: int
    action: str | None
    confidence_band: str | None
    reason_codes: list[str]
    fallback_used: bool
    prompt_template_hash: str
    input_schema_hash: str
    output_schema_hash: str
    validator_version: str
    fallback_version: str
    model_binding_revision: str
    temperature: float | None
    top_p: float | None
    retention_class: str
    expires_at: float | None
    privacy_scope: str
    aggregate_after_expiry: bool
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
        policy_version=row["policy_version"], mode=row["mode"],
        source_type=row["source_type"], source_id=row["source_id"],
        source_revision=row["source_revision"], source_hash=row["source_hash"],
        source_snapshot=json.loads(row["source_snapshot_json"]),
        snapshot_hash=row["snapshot_hash"],
        candidate_snapshot_hash=row["candidate_snapshot_hash"],
        idempotency_key=row["idempotency_key"], status=row["status"],
        attempt_count=row["attempt_count"], max_attempts=row["max_attempts"],
        next_attempt_at=row["next_attempt_at"], provider_id=row["provider_id"],
        model_id=row["model_id"], provider_location=row["provider_location"],
        provider_location_revision=row["provider_location_revision"],
        logical_role=row["logical_role"], certification_level=row["certification_level"],
        latency_ms=row["latency_ms"],
        input_tokens=row["input_tokens"], output_tokens=row["output_tokens"],
        error_code=row["error_code"], warnings=json.loads(row["warnings_json"]),
        candidate_count=row["candidate_count"], selected_count=row["selected_count"],
        action=row["action"], confidence_band=row["confidence_band"],
        reason_codes=json.loads(row["reason_codes_json"]),
        fallback_used=bool(row["fallback_used"]),
        prompt_template_hash=row["prompt_template_hash"],
        input_schema_hash=row["input_schema_hash"],
        output_schema_hash=row["output_schema_hash"],
        validator_version=row["validator_version"], fallback_version=row["fallback_version"],
        model_binding_revision=row["model_binding_revision"],
        temperature=row["temperature"], top_p=row["top_p"],
        retention_class=row["retention_class"], expires_at=row["expires_at"],
        privacy_scope=row["privacy_scope"],
        aggregate_after_expiry=bool(row["aggregate_after_expiry"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def create_or_get_run(
    *, task_kind: str, protocol_version: str, source_type: str, source_id: str,
    source_revision: str, source_hash: str, idempotency_key: str,
    max_attempts: int = 3, provider_id: str | None = None,
    model_id: str | None = None, now: float | None = None,
    policy_version: str = "", mode: str = "legacy", provider_location: str | None = None,
    provider_location_revision: int | None = None, logical_role: str = "legacy",
    certification_level: str = "unverified",
    source_snapshot: Iterable[dict[str, Any]] = (), snapshot_hash: str = "",
    candidate_snapshot_hash: str = "", candidate_count: int = 0,
    prompt_template_hash: str = "", input_schema_hash: str = "",
    output_schema_hash: str = "", validator_version: str = "",
    fallback_version: str = "", model_binding_revision: str = "",
    temperature: float | None = None, top_p: float | None = None,
    retention_class: str = "operational", expires_at: float | None = None,
    privacy_scope: str = "body_free", aggregate_after_expiry: bool = True,
) -> tuple[DecisionRun, bool]:
    if not all((task_kind, protocol_version, source_type, source_id, source_hash, idempotency_key)):
        raise ValueError("DecisionRun identity fields must not be empty")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if mode not in {"legacy", "shadow", "advisory", "active"}:
        raise ValueError("invalid DecisionRun mode")
    if logical_role not in {"legacy", "fast", "reasoning", "creative"}:
        raise ValueError("invalid DecisionRun logical role")
    if certification_level not in {
        "unverified", "structured_capable", "decision_verified", "local_sensitive_verified",
    }:
        raise ValueError("invalid DecisionRun certification level")
    if candidate_count < 0:
        raise ValueError("candidate_count must not be negative")
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
                "INSERT INTO decision_runs (id,task_kind,protocol_version,policy_version,mode,"
                "source_type,source_id,source_revision,source_hash,source_snapshot_json,"
                "snapshot_hash,candidate_snapshot_hash,idempotency_key,status,attempt_count,"
                "max_attempts,provider_id,model_id,provider_location,warnings_json,"
                "provider_location_revision,logical_role,certification_level,"
                "candidate_count,prompt_template_hash,input_schema_hash,output_schema_hash,"
                "validator_version,fallback_version,model_binding_revision,temperature,top_p,"
                "retention_class,expires_at,privacy_scope,aggregate_after_expiry,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, task_kind, protocol_version, policy_version, mode, source_type, source_id,
                 source_revision, source_hash,
                 json.dumps(list(source_snapshot), ensure_ascii=False, sort_keys=True),
                 snapshot_hash, candidate_snapshot_hash, idempotency_key, RunStatus.QUEUED, 0,
                 max_attempts, provider_id, model_id, provider_location, "[]",
                 provider_location_revision, logical_role, certification_level, candidate_count,
                 prompt_template_hash, input_schema_hash, output_schema_hash, validator_version,
                 fallback_version, model_binding_revision, temperature, top_p, retention_class,
                 expires_at, privacy_scope, int(aggregate_after_expiry), now, now),
            )
            _record_event(
                conn, run_id=run_id, event_type="created", from_status=None,
                to_status=RunStatus.QUEUED, mode=mode, created_at=now,
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
        _record_event(
            conn, run_id=run_id, event_type="transition", from_status=row["status"],
            to_status=status, mode=row["mode"], error_code=error_code,
            warning_codes=list(warnings), created_at=now,
        )
        conn.commit()
        return _from_row(conn.execute("SELECT * FROM decision_runs WHERE id=?", (run_id,)).fetchone())
    finally:
        conn.close()


def _record_event(
    conn, *, run_id: str, event_type: str, from_status: str | None, to_status: str,
    mode: str, created_at: float, error_code: str | None = None,
    warning_codes: Iterable[str] = (),
) -> None:
    conn.execute(
        "INSERT INTO decision_run_events(id,run_id,event_type,from_status,to_status,mode,"
        "error_code,warning_codes_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (db.new_id(), run_id, event_type, from_status, to_status, mode, error_code,
         json.dumps(list(warning_codes), ensure_ascii=False), created_at),
    )


def _record_validated_decision_outcome(
    run_id: str, *, action: str, selected_count: int, confidence_band: str,
    reason_codes: Iterable[str], fallback_used: bool,
    validated_candidate_snapshot_hash: str,
) -> DecisionRun:
    """Internal CDS collaborator; caller must validate IDs against this exact snapshot."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM decision_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("DecisionRun not found")
        if not validated_candidate_snapshot_hash or (
            validated_candidate_snapshot_hash != row["candidate_snapshot_hash"]
        ):
            raise ValueError("validated candidate snapshot mismatch")
        if selected_count < 0 or selected_count > row["candidate_count"]:
            raise ValueError("selected_count exceeds candidate_count")
        conn.execute(
            "UPDATE decision_runs SET action=?,selected_count=?,confidence_band=?,"
            "reason_codes_json=?,fallback_used=?,updated_at=? WHERE id=?",
            (action, selected_count, confidence_band,
             json.dumps(list(reason_codes), ensure_ascii=False), int(fallback_used), db.now(), run_id),
        )
        conn.commit()
        return _from_row(conn.execute("SELECT * FROM decision_runs WHERE id=?", (run_id,)).fetchone())
    finally:
        conn.close()


def list_diagnostics(*, decision_kind: str | None = None, limit: int = 50) -> list[dict]:
    """Return a strict body-free allowlist; expired rows are omitted."""
    limit = max(1, min(int(limit), 200))
    clauses = ["(expires_at IS NULL OR expires_at>?)"]
    params: list[Any] = [db.now()]
    if decision_kind:
        clauses.append("task_kind=?")
        params.append(decision_kind)
    params.append(limit)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id,task_kind,protocol_version,policy_version,mode,status,attempt_count,"
            "provider_id,model_id,provider_location,provider_location_revision,logical_role,"
            "certification_level,latency_ms,input_tokens,output_tokens,"
            "error_code,warnings_json,candidate_count,selected_count,action,confidence_band,"
            "reason_codes_json,fallback_used,validator_version,fallback_version,"
            "model_binding_revision,retention_class,expires_at,privacy_scope,created_at,"
            "updated_at,completed_at FROM decision_runs WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    finally:
        conn.close()
    return [{
        "id": row["id"], "decision_kind": row["task_kind"],
        "protocol_version": row["protocol_version"], "policy_version": row["policy_version"],
        "mode": row["mode"], "status": row["status"],
        "attempt_count": row["attempt_count"], "provider_id": row["provider_id"],
        "model_id": row["model_id"], "provider_location": row["provider_location"],
        "provider_location_revision": row["provider_location_revision"],
        "logical_role": row["logical_role"],
        "certification_level": row["certification_level"],
        "latency_ms": row["latency_ms"], "prompt_tokens": row["input_tokens"],
        "completion_tokens": row["output_tokens"], "error_code": row["error_code"],
        "warning_codes": json.loads(row["warnings_json"]),
        "candidate_count": row["candidate_count"], "selected_count": row["selected_count"],
        "action": row["action"], "confidence_band": row["confidence_band"],
        "reason_codes": json.loads(row["reason_codes_json"]),
        "fallback_used": bool(row["fallback_used"]),
        "validator_version": row["validator_version"],
        "fallback_version": row["fallback_version"],
        "model_binding_revision": row["model_binding_revision"],
        "retention_class": row["retention_class"], "expires_at": row["expires_at"],
        "privacy_scope": row["privacy_scope"], "created_at": row["created_at"],
        "updated_at": row["updated_at"], "finished_at": row["completed_at"],
    } for row in rows]


def list_diagnostic_events(*, decision_kind: str | None = None, limit: int = 100) -> list[dict]:
    """Return body-free transition events for non-expired DecisionRuns."""
    limit = max(1, min(int(limit), 400))
    clauses = ["(r.expires_at IS NULL OR r.expires_at>?)"]
    params: list[Any] = [db.now()]
    if decision_kind:
        clauses.append("r.task_kind=?")
        params.append(decision_kind)
    params.append(limit)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT e.id,e.run_id,e.event_type,e.from_status,e.to_status,e.mode,e.error_code,"
            "e.warning_codes_json,e.created_at FROM decision_run_events e "
            "JOIN decision_runs r ON r.id=e.run_id WHERE " + " AND ".join(clauses)
            + " ORDER BY e.created_at DESC,e.id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    finally:
        conn.close()
    return [{
        "id": row["id"], "run_id": row["run_id"], "event_type": row["event_type"],
        "from_status": row["from_status"], "to_status": row["to_status"],
        "mode": row["mode"], "error_code": row["error_code"],
        "warning_codes": json.loads(row["warning_codes_json"]),
        "created_at": row["created_at"],
    } for row in rows]

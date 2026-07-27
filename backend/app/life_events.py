"""LIFE.2 provenance-aware event ledger and fact-layer state machine."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from . import db

EVENT_KINDS = frozenset({"state_transition", "activity", "agent_action", "observation", "date_marker"})
WORLD_LAYERS = frozenset({"planned", "simulated", "observed", "performed"})
SOURCE_KINDS = frozenset({
    "life_event", "diary_entry", "important_date", "personal_goal", "self_timeline",
    "tool_run", "user_statement", "system_observation",
})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LifeEventError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceRef:
    kind: str
    id: str
    revision: str
    content_hash: str


def make_idempotency_key(*, event_kind: str, source_refs: Iterable[SourceRef], semantic_key: str) -> str:
    payload = {
        "event_kind": event_kind,
        "semantic_key": semantic_key,
        "sources": sorted((item.kind, item.id, item.revision, item.content_hash) for item in source_refs),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "life-event-v1:" + hashlib.sha256(encoded).hexdigest()


def _validate_source(source: SourceRef) -> None:
    if source.kind not in SOURCE_KINDS or not source.id or not source.revision:
        raise LifeEventError("source_invalid", "life event source identity is invalid")
    if not _HEX64.fullmatch(source.content_hash):
        raise LifeEventError("source_hash_invalid", "life event source hash must be sha256")


def _validate_attributes(attributes: dict[str, Any]) -> str:
    if not isinstance(attributes, dict):
        raise LifeEventError("attributes_invalid", "attributes must be an object")
    encoded = json.dumps(attributes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 8_000:
        raise LifeEventError("attributes_too_large", "life event attributes exceed the bound")
    return encoded


def _require_tool_run(conn: sqlite3.Connection, *, event_kind: str, world_layer: str,
                      tool_run_id: str | None) -> None:
    requires_tool = event_kind == "agent_action" and world_layer == "performed"
    if requires_tool and not tool_run_id:
        raise LifeEventError("tool_run_required", "performed agent action requires ToolRun evidence")
    if tool_run_id:
        row = conn.execute("SELECT id,status FROM tool_logs WHERE id=?", (tool_run_id,)).fetchone()
        if not row or row["status"] != "done":
            raise LifeEventError("tool_run_invalid", "ToolRun evidence is missing or not completed")
    if world_layer == "performed" and event_kind != "agent_action":
        raise LifeEventError("performed_layer_invalid", "performed layer is reserved for agent actions")


def create_event(*, event_kind: str, world_layer: str, summary: str,
                 source_refs: tuple[SourceRef, ...], idempotency_key: str,
                 attributes: dict[str, Any] | None = None, tool_run_id: str | None = None,
                 now: float | None = None) -> tuple[dict[str, Any], bool]:
    if event_kind not in EVENT_KINDS or world_layer not in WORLD_LAYERS:
        raise LifeEventError("event_semantics_invalid", "event kind or world layer is invalid")
    if not isinstance(summary, str) or len(summary) > 2_000:
        raise LifeEventError("summary_invalid", "life event summary exceeds the bound")
    if not source_refs or len({(s.kind, s.id, s.revision) for s in source_refs}) != len(source_refs):
        raise LifeEventError("source_set_invalid", "life event sources must be non-empty and unique")
    for source in source_refs:
        _validate_source(source)
    if not idempotency_key:
        raise LifeEventError("idempotency_key_invalid", "idempotency key is required")
    attributes_json = _validate_attributes(attributes or {})
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        existing = conn.execute("SELECT id FROM life_events WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            event = get_event(existing["id"], conn=conn)
            expected = (event_kind, world_layer, summary, attributes_json, tool_run_id)
            actual = (
                event["event_kind"], event["world_layer"], event["summary"],
                json.dumps(event["attributes"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                event["tool_run_id"],
            )
            if actual != expected:
                raise LifeEventError("idempotency_conflict", "idempotency key was reused for different content")
            return event, False
        _require_tool_run(conn, event_kind=event_kind, world_layer=world_layer, tool_run_id=tool_run_id)
        event_id = db.new_id()
        conn.execute(
            "INSERT INTO life_events(id,event_kind,world_layer,lifecycle_status,current_revision,"
            "tool_run_id,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (event_id, event_kind, world_layer, "active", 1, tool_run_id, idempotency_key, now, now),
        )
        conn.execute(
            "INSERT INTO life_event_revisions(id,event_id,revision,event_kind,world_layer,summary,"
            "attributes_json,change_reason_code,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (db.new_id(), event_id, 1, event_kind, world_layer, summary, attributes_json, "created", now),
        )
        for source in source_refs:
            conn.execute(
                "INSERT INTO life_event_sources(id,event_id,source_kind,source_id,source_revision,"
                "source_hash,active,created_at) VALUES(?,?,?,?,?,?,1,?)",
                (db.new_id(), event_id, source.kind, source.id, source.revision, source.content_hash, now),
            )
        conn.execute(
            "INSERT INTO life_event_audit_events(id,event_id,event_type,from_status,to_status,revision,"
            "reason_code,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), event_id, "created", None, "active", 1, "created", now),
        )
        conn.commit()
        return get_event(event_id, conn=conn), True
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise LifeEventError("event_integrity_error", "life event violates ledger integrity") from exc
    finally:
        conn.close()


def get_event(event_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    owned = conn is None
    conn = db.connect() if conn is None else conn
    try:
        row = conn.execute(
            "SELECT e.*,r.summary,r.attributes_json,r.change_reason_code FROM life_events e "
            "JOIN life_event_revisions r ON r.event_id=e.id AND r.revision=e.current_revision "
            "WHERE e.id=?", (event_id,),
        ).fetchone()
        if not row:
            return None
        sources = conn.execute(
            "SELECT source_kind,source_id,source_revision,source_hash,active,removed_at "
            "FROM life_event_sources WHERE event_id=? ORDER BY created_at,id", (event_id,),
        ).fetchall()
        return {
            "id": row["id"], "event_kind": row["event_kind"], "world_layer": row["world_layer"],
            "lifecycle_status": row["lifecycle_status"], "revision": row["current_revision"],
            "tool_run_id": row["tool_run_id"], "summary": row["summary"],
            "attributes": json.loads(row["attributes_json"]),
            "change_reason_code": row["change_reason_code"],
            "sources": [{"kind": s["source_kind"], "id": s["source_id"],
                         "revision": s["source_revision"], "content_hash": s["source_hash"],
                         "active": bool(s["active"]), "removed_at": s["removed_at"]} for s in sources],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
    finally:
        if owned:
            conn.close()


def correct_event(event_id: str, *, expected_revision: int, summary: str,
                  attributes: dict[str, Any] | None, reason_code: str,
                  now: float | None = None) -> dict[str, Any]:
    if not reason_code or not isinstance(summary, str) or len(summary) > 2_000:
        raise LifeEventError("correction_invalid", "correction fields are invalid")
    attributes_json = _validate_attributes(attributes or {})
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM life_events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise LifeEventError("event_not_found", "life event was not found")
        if row["lifecycle_status"] != "active" or row["current_revision"] != expected_revision:
            raise LifeEventError("revision_conflict", "life event changed or is no longer active")
        revision = expected_revision + 1
        conn.execute(
            "INSERT INTO life_event_revisions(id,event_id,revision,event_kind,world_layer,summary,"
            "attributes_json,change_reason_code,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (db.new_id(), event_id, revision, row["event_kind"], row["world_layer"], summary,
             attributes_json, reason_code, now),
        )
        cursor = conn.execute(
            "UPDATE life_events SET current_revision=?,updated_at=? WHERE id=? AND current_revision=? "
            "AND lifecycle_status='active'", (revision, now, event_id, expected_revision),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise LifeEventError("revision_conflict", "life event changed concurrently")
        conn.execute(
            "INSERT INTO life_event_audit_events(id,event_id,event_type,from_status,to_status,revision,"
            "reason_code,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), event_id, "corrected", "active", "active", revision, reason_code, now),
        )
        conn.commit()
        return get_event(event_id, conn=conn)
    finally:
        conn.close()


def revoke_event(event_id: str, *, expected_revision: int, reason_code: str,
                 now: float | None = None) -> dict[str, Any]:
    if not reason_code:
        raise LifeEventError("revoke_reason_required", "revoke reason is required")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        cursor = conn.execute(
            "UPDATE life_events SET lifecycle_status='revoked',updated_at=? WHERE id=? "
            "AND current_revision=? AND lifecycle_status='active'", (now, event_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise LifeEventError("revision_conflict", "life event changed or is no longer active")
        conn.execute(
            "INSERT INTO life_event_audit_events(id,event_id,event_type,from_status,to_status,revision,"
            "reason_code,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), event_id, "revoked", "active", "revoked", expected_revision, reason_code, now),
        )
        conn.commit()
        return get_event(event_id, conn=conn)
    finally:
        conn.close()


def remove_source(*, source_kind: str, source_id: str, reason_code: str,
                  now: float | None = None) -> int:
    if source_kind not in SOURCE_KINDS or not source_id or not reason_code:
        raise LifeEventError("source_removal_invalid", "source removal identity is invalid")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT event_id FROM life_event_sources WHERE source_kind=? AND source_id=? AND active=1",
            (source_kind, source_id),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE life_event_sources SET active=0,removed_at=? WHERE event_id=? "
                "AND source_kind=? AND source_id=? AND active=1", (now, row["event_id"], source_kind, source_id),
            )
            event = conn.execute("SELECT * FROM life_events WHERE id=?", (row["event_id"],)).fetchone()
            if event and event["lifecycle_status"] == "active":
                remaining = conn.execute(
                    "SELECT 1 FROM life_event_sources WHERE event_id=? AND active=1 LIMIT 1", (row["event_id"],)
                ).fetchone()
                next_status = "active" if remaining else "revoked"
                if next_status == "revoked":
                    conn.execute("UPDATE life_events SET lifecycle_status='revoked',updated_at=? WHERE id=?", (now, row["event_id"]))
                conn.execute(
                    "INSERT INTO life_event_audit_events(id,event_id,event_type,from_status,to_status,revision,"
                    "reason_code,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (db.new_id(), row["event_id"], "source_removed", "active", next_status,
                     event["current_revision"], reason_code, now),
                )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def list_events(*, include_revoked: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id FROM life_events " + ("" if include_revoked else "WHERE lifecycle_status='active' ")
            + "ORDER BY created_at DESC,id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [get_event(row["id"], conn=conn) for row in rows]
    finally:
        conn.close()


def diagnostics(*, event_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    clauses, params = [], []
    if event_id:
        clauses.append("event_id=?")
        params.append(event_id)
    params.append(limit)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id,event_id,event_type,from_status,to_status,revision,reason_code,created_at "
            "FROM life_event_audit_events " + ("WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY created_at DESC,id DESC LIMIT ?", tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

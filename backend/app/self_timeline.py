"""LIFE.9 local SelfTimeline projection and epistemic-expression-v1."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from . import db

PROTOCOL_VERSION = "epistemic-expression-v1"
_SELF_QUERY = re.compile(r"(?:你|遐蝶).{0,8}(?:做过|干了|经历|今天|最近|生活|日记|计划)|what\s+did\s+you", re.I)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _upsert(conn, *, source_type: str, source_id: str, source_revision: str,
            world_layer: str, source_status: str, occurred_at: float,
            summary: str, source_locator: str, now: float) -> None:
    identity = f"{source_type}:{source_id}:{source_revision}"
    conn.execute(
        "INSERT INTO self_timeline_entries(id,source_type,source_id,source_revision,world_layer,"
        "source_status,occurred_at,summary,source_locator,content_hash,indexed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_type,source_id,source_revision) DO UPDATE SET "
        "world_layer=excluded.world_layer,source_status=excluded.source_status,occurred_at=excluded.occurred_at,"
        "summary=excluded.summary,source_locator=excluded.source_locator,content_hash=excluded.content_hash,"
        "indexed_at=excluded.indexed_at",
        (_hash(identity)[:16], source_type, source_id, source_revision, world_layer, source_status,
         occurred_at, summary[:500], source_locator, _hash(summary), now),
    )


def refresh(*, now: float | None = None, conn=None) -> int:
    now = db.now() if now is None else now
    owned = conn is None
    conn = db.connect() if conn is None else conn
    try:
        active_keys: set[tuple[str, str, str]] = set()
        for row in conn.execute(
            "SELECT e.id,e.current_revision,e.world_layer,e.lifecycle_status,e.created_at,r.summary "
            "FROM life_events e JOIN life_event_revisions r ON r.event_id=e.id AND r.revision=e.current_revision"
        ).fetchall():
            key = ("life_event", row["id"], str(row["current_revision"]))
            active_keys.add(key)
            _upsert(conn, source_type=key[0], source_id=key[1], source_revision=key[2],
                    world_layer=row["world_layer"], source_status=row["lifecycle_status"],
                    occurred_at=row["created_at"], summary=row["summary"],
                    source_locator=f"/api/life/events?event_id={row['id']}", now=now)
        for row in conn.execute("SELECT * FROM diary_entries").fetchall():
            key = ("diary_entry", row["id"], str(row["revision"]))
            active_keys.add(key)
            _upsert(conn, source_type=key[0], source_id=key[1], source_revision=key[2],
                    world_layer="inferred", source_status=row["status"], occurred_at=row["created_at"],
                    summary=row["title"] if row["sensitivity"] == "normal" else "一条私密日记",
                    source_locator=f"/api/life/diary/{row['id']}", now=now)
        for row in conn.execute(
            "SELECT s.*,d.revision AS schedule_revision,d.status AS schedule_status,d.created_at AS schedule_created "
            "FROM life_schedule_segments s JOIN life_schedules d ON d.id=s.schedule_id"
        ).fetchall():
            revision = f"{row['schedule_revision']}:{row['detail_revision']}"
            key = ("schedule_segment", row["id"], revision)
            active_keys.add(key)
            _upsert(conn, source_type=key[0], source_id=key[1], source_revision=key[2],
                    world_layer="planned", source_status=row["schedule_status"],
                    occurred_at=row["schedule_created"], summary=row["label"],
                    source_locator=f"/api/life/schedules/{row['schedule_id']}", now=now)
        for row in conn.execute("SELECT * FROM tool_logs").fetchall():
            key = ("tool_run", row["id"], "1")
            active_keys.add(key)
            _upsert(conn, source_type=key[0], source_id=key[1], source_revision=key[2],
                    world_layer="performed" if row["status"] == "done" else "observed",
                    source_status=row["status"], occurred_at=row["created_at"],
                    summary=row["summary"] or f"工具 {row['tool']}",
                    source_locator=f"/api/tool-logs/{row['id']}", now=now)
        for row in conn.execute("SELECT id,status,delivered_at,created_at FROM proactive_deliveries").fetchall():
            key = ("proactive_delivery", row["id"], "1")
            active_keys.add(key)
            _upsert(conn, source_type=key[0], source_id=key[1], source_revision=key[2],
                    world_layer="performed" if row["status"] == "delivered" else "observed",
                    source_status=row["status"], occurred_at=row["delivered_at"] or row["created_at"],
                    summary="一次主动陪伴表达", source_locator="/api/proactive/history", now=now)
        for row in conn.execute("SELECT * FROM personal_goals").fetchall():
            key = ("personal_goal", row["id"], str(row["revision"]))
            active_keys.add(key)
            _upsert(conn, source_type=key[0], source_id=key[1], source_revision=key[2],
                    world_layer="planned", source_status=row["status"], occurred_at=row["updated_at"],
                    summary=row["title"], source_locator=f"/api/life/goals/{row['id']}", now=now)
        rows = conn.execute("SELECT source_type,source_id,source_revision FROM self_timeline_entries").fetchall()
        for row in rows:
            if tuple(row) not in active_keys:
                conn.execute(
                    "DELETE FROM self_timeline_entries WHERE source_type=? AND source_id=? AND source_revision=?",
                    tuple(row),
                )
        if owned:
            conn.commit()
        return len(active_keys)
    finally:
        if owned:
            conn.close()


def search(query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 20))
    terms = [term for term in re.split(r"\s+", query.strip()) if len(term) >= 2][:4]
    conn = db.connect()
    try:
        if terms and not _SELF_QUERY.search(query):
            clauses = " OR ".join("summary LIKE ?" for _ in terms)
            rows = conn.execute(
                f"SELECT * FROM self_timeline_entries WHERE source_status NOT IN ('revoked','disabled','private') "
                f"AND ({clauses}) ORDER BY occurred_at DESC LIMIT ?",
                tuple(f"%{term}%" for term in terms) + (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM self_timeline_entries WHERE source_status NOT IN ('revoked','disabled','private') "
                "ORDER BY occurred_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def epistemic_expression(item: dict[str, Any]) -> str:
    summary = item["summary"]
    layer = item["world_layer"]
    if layer == "planned":
        return f"原本打算：{summary}"
    if layer == "simulated":
        return f"在自己的日程里：{summary}"
    if layer == "inferred":
        return f"大概按原来的节奏：{summary}"
    if layer == "observed":
        return f"根据留下的记录：{summary}"
    if layer == "performed" and item["source_type"] in {"tool_run", "proactive_delivery", "life_event"}:
        return f"确实完成：{summary}"
    return f"没有可靠记录能确认：{summary}"


def context_block(query: str, *, max_items: int = 5, max_chars: int = 1_200) -> str:
    if not _SELF_QUERY.search(query):
        return ""
    items = search(query, limit=max_items)
    if not items:
        return "[SelfTimeline] 没有可靠记录；不要编造角色做过的事情。"
    lines = ["[SelfTimeline / epistemic-expression-v1]"]
    for item in items:
        lines.append(f"- {epistemic_expression(item)}（来源：{item['source_locator']}）")
    return "\n".join(lines)[:max_chars]


def delete_projection(*, source_type: str, source_id: str) -> int:
    conn = db.connect()
    try:
        cursor = conn.execute(
            "DELETE FROM self_timeline_entries WHERE source_type=? AND source_id=?",
            (source_type, source_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

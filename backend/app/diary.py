"""LIFE.8 sourced diary, fatigue guard and provider-aware sharing."""
from __future__ import annotations

import re
from typing import Any

from . import db

SOURCE_KINDS = frozenset({"life_event", "schedule_segment", "important_date", "personal_goal"})
SENSITIVE_HINTS = ("密码", "密钥", "身份证", "住址", "病历", "创伤", "银行卡", "api key")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class DiaryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def deterministic_fallback_text(*, entry_date: str, source_summary: str) -> tuple[str, str]:
    """Body-only fallback; caller must still bind and validate the source revision."""
    variants = (
        "今天留下了一点安静的痕迹",
        "把这段生活轻轻收进今天",
        "今天的节奏在这里转了一个小弯",
        "有件普通但值得记住的小事",
        "给今天留下一枚简短的书签",
    )
    index = sum(ord(char) for char in entry_date) % len(variants)
    bounded = source_summary.strip()[:240]
    return f"{entry_date} 的生活书签", f"{variants[index]}：{bounded}"


def classify_sensitivity(title: str, body: str) -> str:
    lowered = f"{title} {body}".lower()
    return "sensitive" if any(hint in lowered for hint in SENSITIVE_HINTS) else "normal"


def create_thread(*, title: str, motif_code: str, now: float | None = None) -> dict[str, Any]:
    if not title or len(title) > 160 or not motif_code or len(motif_code) > 80:
        raise DiaryError("thread_invalid", "continuity thread is invalid")
    now = db.now() if now is None else now
    thread_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO continuity_threads(id,title,motif_code,status,revision,created_at,updated_at) "
            "VALUES(?,?,?,'active',1,?,?)", (thread_id, title, motif_code, now, now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM continuity_threads WHERE id=?", (thread_id,)).fetchone())
    finally:
        conn.close()


def _source_exists(conn, kind: str, source_id: str, revision: str) -> bool:
    if kind == "life_event":
        row = conn.execute(
            "SELECT current_revision,world_layer,lifecycle_status FROM life_events WHERE id=?", (source_id,),
        ).fetchone()
        return bool(row and str(row["current_revision"]) == revision and row["lifecycle_status"] == "active"
                    and row["world_layer"] != "planned")
    if kind == "schedule_segment":
        row = conn.execute("SELECT detail_revision,detail_status FROM life_schedule_segments WHERE id=?", (source_id,)).fetchone()
        return bool(row and str(row["detail_revision"]) == revision and row["detail_status"] == "detailed")
    if kind == "important_date":
        row = conn.execute("SELECT revision,status FROM important_dates WHERE id=?", (source_id,)).fetchone()
        return bool(row and str(row["revision"]) == revision and row["status"] == "active")
    if kind == "personal_goal":
        row = conn.execute("SELECT revision,status FROM personal_goals WHERE id=?", (source_id,)).fetchone()
        return bool(row and str(row["revision"]) == revision and row["status"] in {"active", "completed"})
    return False


def create_entry(*, entry_date: str, title: str, body: str, source_kind: str,
                 source_id: str, source_revision: str, source_hash: str,
                 share_policy: str = "private", thread_id: str | None = None,
                 now: float | None = None) -> dict[str, Any]:
    if not entry_date or not title or len(title) > 160 or not body or len(body) > 8_000:
        raise DiaryError("entry_invalid", "diary entry fields are invalid")
    if source_kind not in SOURCE_KINDS or not _HEX64.fullmatch(source_hash) or not source_revision:
        raise DiaryError("source_invalid", "diary source identity is invalid")
    if share_policy not in {"private", "ask", "natural", "never"}:
        raise DiaryError("share_policy_invalid", "diary share policy is invalid")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        if not _source_exists(conn, source_kind, source_id, source_revision):
            raise DiaryError("source_unavailable", "diary source is stale, planned or unavailable")
        if thread_id:
            thread = conn.execute("SELECT motif_code,status FROM continuity_threads WHERE id=?", (thread_id,)).fetchone()
            if not thread or thread["status"] != "active":
                raise DiaryError("thread_unavailable", "continuity thread is unavailable")
            repeated = conn.execute(
                "SELECT COUNT(*) FROM diary_entries e JOIN continuity_threads t ON t.id=e.thread_id "
                "WHERE t.motif_code=? AND e.status='active' ORDER BY e.created_at DESC LIMIT 7",
                (thread["motif_code"],),
            ).fetchone()[0]
            if repeated >= 3:
                raise DiaryError("motif_fatigue", "continuity motif is over-repeated")
        sensitivity = classify_sensitivity(title, body)
        item_id = db.new_id()
        conn.execute(
            "INSERT INTO diary_entries(id,thread_id,entry_date,status,sensitivity,share_policy,revision,title,body,"
            "created_at,updated_at) VALUES(?,?,?,'active',?,?,1,?,?,?,?)",
            (item_id, thread_id, entry_date, sensitivity, share_policy, title, body, now, now),
        )
        conn.execute(
            "INSERT INTO diary_entry_revisions(id,diary_entry_id,revision,title,body,reason_code,created_at) "
            "VALUES(?,?,1,?,?,?,?)", (db.new_id(), item_id, title, body, "created", now),
        )
        conn.execute(
            "INSERT INTO diary_entry_sources(id,diary_entry_id,source_kind,source_id,source_revision,source_hash,"
            "active,created_at) VALUES(?,?,?,?,?,?,1,?)",
            (db.new_id(), item_id, source_kind, source_id, source_revision, source_hash, now),
        )
        conn.commit()
        return get_entry(item_id, conn=conn)
    finally:
        conn.close()


def get_entry(item_id: str, *, conn=None) -> dict[str, Any] | None:
    owned = conn is None
    conn = db.connect() if conn is None else conn
    try:
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        sources = conn.execute(
            "SELECT source_kind,source_id,source_revision,source_hash,active FROM diary_entry_sources "
            "WHERE diary_entry_id=?", (item_id,),
        ).fetchall()
        return dict(row) | {"sources": [dict(source) for source in sources]}
    finally:
        if owned:
            conn.close()


def revise_entry(item_id: str, *, expected_revision: int, title: str, body: str,
                 reason_code: str, now: float | None = None) -> dict[str, Any]:
    if not title or len(title) > 160 or not body or len(body) > 8_000 or not reason_code:
        raise DiaryError("revision_invalid", "diary revision is invalid")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (item_id,)).fetchone()
        if not row or row["revision"] != expected_revision or row["status"] != "active":
            raise DiaryError("revision_conflict", "diary entry changed or is unavailable")
        revision = expected_revision + 1
        sensitivity = classify_sensitivity(title, body)
        conn.execute(
            "INSERT INTO diary_entry_revisions(id,diary_entry_id,revision,title,body,reason_code,created_at) "
            "VALUES(?,?,?,?,?,?,?)", (db.new_id(), item_id, revision, title, body, reason_code, now),
        )
        cursor = conn.execute(
            "UPDATE diary_entries SET title=?,body=?,sensitivity=?,revision=?,updated_at=? "
            "WHERE id=? AND revision=? AND status='active'",
            (title, body, sensitivity, revision, now, item_id, expected_revision),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise DiaryError("revision_conflict", "diary entry changed concurrently")
        conn.commit()
        return get_entry(item_id, conn=conn)
    finally:
        conn.close()


def can_share(entry: dict[str, Any], *, provider_location: str,
              certification_level: str, explicit_authorization: bool) -> bool:
    if entry["status"] != "active" or entry["share_policy"] in {"private", "never"}:
        return False
    if entry["sensitivity"] == "sensitive":
        locally_verified = (
            provider_location == "local" and certification_level == "local_sensitive_verified"
        )
        return locally_verified or explicit_authorization
    if entry["share_policy"] == "ask":
        return explicit_authorization
    return entry["share_policy"] == "natural"


def rebuild_invalid_sources(*, now: float | None = None) -> int:
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT s.*,e.status FROM diary_entry_sources s JOIN diary_entries e ON e.id=s.diary_entry_id "
            "WHERE s.active=1 AND e.status='active'"
        ).fetchall()
        affected: set[str] = set()
        for row in rows:
            if not _source_exists(conn, row["source_kind"], row["source_id"], row["source_revision"]):
                conn.execute(
                    "UPDATE diary_entry_sources SET active=0,removed_at=? WHERE id=?", (now, row["id"]),
                )
                affected.add(row["diary_entry_id"])
        for item_id in affected:
            remaining = conn.execute(
                "SELECT 1 FROM diary_entry_sources WHERE diary_entry_id=? AND active=1 LIMIT 1", (item_id,),
            ).fetchone()
            if not remaining:
                conn.execute("UPDATE diary_entries SET status='revoked',updated_at=? WHERE id=?", (now, item_id))
        conn.commit()
        return len(affected)
    finally:
        conn.close()

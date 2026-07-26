"""LIFE.5 validated coarse schedules and just-in-time planned candidates."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import db

ALGORITHM_VERSION = "life-schedule-fallback-v1"
DISALLOWED_ACTIVITY_CODES = frozenset({"network_action", "tool_action", "message_delivery", "external_purchase"})
ALLOWED_ACTIVITY_CODES = frozenset({
    "sleep", "morning_routine", "focused_work", "meal", "walk", "reading",
    "creative", "household", "reflection", "leisure", "wind_down",
})


class ScheduleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SegmentProposal:
    start_minute: int
    end_minute: int
    activity_code: str
    label: str


def validate_segments(segments: tuple[SegmentProposal, ...]) -> None:
    if not segments or len(segments) > 24:
        raise ScheduleError("segment_count_invalid", "schedule segment count is invalid")
    previous_end = 0
    for segment in segments:
        if (
            not isinstance(segment.start_minute, int) or not isinstance(segment.end_minute, int)
            or segment.start_minute != previous_end or segment.end_minute <= segment.start_minute
            or segment.end_minute > 1440
        ):
            raise ScheduleError("schedule_time_invalid", "schedule contains overlap, gap or invalid duration")
        if segment.activity_code in DISALLOWED_ACTIVITY_CODES or segment.activity_code not in ALLOWED_ACTIVITY_CODES:
            raise ScheduleError("activity_not_allowed", "schedule activity is not allowed")
        if not isinstance(segment.label, str) or not segment.label or len(segment.label) > 80:
            raise ScheduleError("label_invalid", "schedule label is invalid")
        previous_end = segment.end_minute
    if previous_end != 1440:
        raise ScheduleError("schedule_incomplete", "schedule must cover the complete local day")


def fallback_segments(local_date: str) -> tuple[SegmentProposal, ...]:
    try:
        parsed = date.fromisoformat(local_date)
    except ValueError as exc:
        raise ScheduleError("date_invalid", "schedule date must be ISO format") from exc
    variant = parsed.toordinal() % 3
    creative_code = ("reading", "creative", "walk")[variant]
    creative_label = ("安静阅读", "整理灵感", "散步观察")[variant]
    result = (
        SegmentProposal(0, 420, "sleep", "休息"),
        SegmentProposal(420, 510, "morning_routine", "晨间整理"),
        SegmentProposal(510, 720, "focused_work", "专注时段"),
        SegmentProposal(720, 810, "meal", "午间休息"),
        SegmentProposal(810, 1020, "focused_work", "下午专注"),
        SegmentProposal(1020, 1140, creative_code, creative_label),
        SegmentProposal(1140, 1320, "leisure", "轻松时段"),
        SegmentProposal(1320, 1440, "wind_down", "收尾与休息"),
    )
    validate_segments(result)
    return result


def create_schedule(*, local_date: str, timezone_id: str,
                    segments: tuple[SegmentProposal, ...] | None = None,
                    source_run_id: str | None = None, now: float | None = None) -> tuple[dict[str, Any], bool]:
    proposals = segments or fallback_segments(local_date)
    validate_segments(proposals)
    if not timezone_id:
        raise ScheduleError("timezone_invalid", "schedule timezone is required")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        active = conn.execute(
            "SELECT id FROM life_schedules WHERE local_date=? AND timezone_id=? AND status='active'",
            (local_date, timezone_id),
        ).fetchone()
        if active:
            return get_schedule(active["id"], conn=conn), False
        revision = conn.execute(
            "SELECT COALESCE(MAX(revision),0)+1 FROM life_schedules WHERE local_date=? AND timezone_id=?",
            (local_date, timezone_id),
        ).fetchone()[0]
        schedule_id = db.new_id()
        conn.execute(
            "INSERT INTO life_schedules(id,local_date,timezone_id,revision,status,algorithm_version,"
            "source_run_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (schedule_id, local_date, timezone_id, revision, "active", ALGORITHM_VERSION,
             source_run_id, now, now),
        )
        for ordinal, segment in enumerate(proposals):
            conn.execute(
                "INSERT INTO life_schedule_segments(id,schedule_id,ordinal,start_minute,end_minute,"
                "activity_code,label,detail_status,detail_revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (db.new_id(), schedule_id, ordinal, segment.start_minute, segment.end_minute,
                 segment.activity_code, segment.label, "coarse", 0, now, now),
            )
        conn.commit()
        return get_schedule(schedule_id, conn=conn), True
    finally:
        conn.close()


def get_schedule(schedule_id: str, *, conn=None) -> dict[str, Any] | None:
    owned = conn is None
    conn = db.connect() if conn is None else conn
    try:
        row = conn.execute("SELECT * FROM life_schedules WHERE id=?", (schedule_id,)).fetchone()
        if not row:
            return None
        segments = conn.execute(
            "SELECT id,ordinal,start_minute,end_minute,activity_code,label,detail_status,detail_revision "
            "FROM life_schedule_segments WHERE schedule_id=? ORDER BY ordinal", (schedule_id,),
        ).fetchall()
        return dict(row) | {"segments": [dict(item) for item in segments]}
    finally:
        if owned:
            conn.close()


def get_active_schedule(*, local_date: str, timezone_id: str) -> dict[str, Any] | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM life_schedules WHERE local_date=? AND timezone_id=? AND status='active'",
            (local_date, timezone_id),
        ).fetchone()
        return get_schedule(row["id"], conn=conn) if row else None
    finally:
        conn.close()


def detail_segment(segment_id: str, *, expected_revision: int, summary: str,
                   now: float | None = None) -> tuple[dict[str, Any], bool]:
    if not isinstance(summary, str) or not summary or len(summary) > 240:
        raise ScheduleError("detail_invalid", "segment detail is invalid")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        segment = conn.execute(
            "SELECT s.*,d.revision AS schedule_revision FROM life_schedule_segments s "
            "JOIN life_schedules d ON d.id=s.schedule_id WHERE s.id=?", (segment_id,),
        ).fetchone()
        if not segment or segment["detail_revision"] != expected_revision or segment["detail_status"] == "cancelled":
            raise ScheduleError("revision_conflict", "schedule segment changed or is unavailable")
        next_revision = expected_revision + 1
        identity = f"{segment_id}:{next_revision}:{summary}"
        key = "life-schedule-detail-v1:" + hashlib.sha256(identity.encode()).hexdigest()
        existing = conn.execute("SELECT * FROM life_event_candidates WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return dict(existing), False
        candidate_id = db.new_id()
        conn.execute(
            "INSERT INTO life_event_candidates(id,source_kind,source_id,source_revision,event_kind,"
            "world_layer,summary,status,idempotency_key,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, "schedule_segment", segment_id,
             f"{segment['schedule_revision']}:{next_revision}", "activity", "planned",
             summary, "proposed", key, now, now),
        )
        cursor = conn.execute(
            "UPDATE life_schedule_segments SET detail_status='detailed',detail_revision=?,updated_at=? "
            "WHERE id=? AND detail_revision=?", (next_revision, now, segment_id, expected_revision),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise ScheduleError("revision_conflict", "schedule segment changed concurrently")
        conn.commit()
        return dict(conn.execute("SELECT * FROM life_event_candidates WHERE id=?", (candidate_id,)).fetchone()), True
    finally:
        conn.close()

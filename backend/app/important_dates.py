"""LIFE.7 sourced ImportantDate rules; v1 supports solar dates only."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from . import db
from .life_catchup import DateCrossing

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_KINDS = frozenset({"user_statement", "memory", "manual"})


class ImportantDateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def create_candidate(*, label: str, recurrence: str, date_year: int | None,
                     date_month: int | None, date_day: int | None, timezone_id: str,
                     confidence: float, source_kind: str, source_id: str,
                     source_revision: str, source_hash: str,
                     celebration_policy: str = "natural", now: float | None = None) -> dict[str, Any]:
    if not label or len(label) > 160 or recurrence not in {"once", "yearly_solar"} or not timezone_id:
        raise ImportantDateError("date_invalid", "important date identity is invalid")
    if source_kind not in SOURCE_KINDS or not source_id or not source_revision or not _HEX64.fullmatch(source_hash):
        raise ImportantDateError("source_invalid", "important date source is invalid")
    if celebration_policy not in {"natural", "day_only", "none"} or not 0 <= confidence <= 1:
        raise ImportantDateError("policy_invalid", "important date policy is invalid")
    if recurrence == "once" and date_year is None:
        raise ImportantDateError("date_invalid", "one-time date requires a year")
    if date_month is not None and date_day is not None and not _valid_date(date_year or 2000, date_month, date_day):
        raise ImportantDateError("date_invalid", "calendar date is invalid")
    status = "candidate"
    now = db.now() if now is None else now
    item_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO important_dates(id,label,status,recurrence,date_year,date_month,date_day,timezone_id,"
            "confidence,celebration_policy,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (item_id, label, status, recurrence, date_year, date_month, date_day, timezone_id,
             confidence, celebration_policy, now, now),
        )
        conn.execute(
            "INSERT INTO important_date_sources(id,important_date_id,source_kind,source_id,source_revision,"
            "source_hash,active,created_at) VALUES(?,?,?,?,?,?,1,?)",
            (db.new_id(), item_id, source_kind, source_id, source_revision, source_hash, now),
        )
        conn.execute(
            "INSERT INTO important_date_events(id,important_date_id,event_type,from_status,to_status,revision,"
            "reason_code,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), item_id, "created", None, status, 1, "candidate_created", now),
        )
        conn.commit()
        return get(item_id, conn=conn)
    finally:
        conn.close()


def get(item_id: str, *, conn=None) -> dict[str, Any] | None:
    owned = conn is None
    conn = db.connect() if conn is None else conn
    try:
        row = conn.execute("SELECT * FROM important_dates WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        if owned:
            conn.close()


def confirm(item_id: str, *, expected_revision: int, date_year: int | None,
            date_month: int, date_day: int, now: float | None = None) -> dict[str, Any]:
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM important_dates WHERE id=?", (item_id,)).fetchone()
        if not row or row["revision"] != expected_revision or row["status"] != "candidate":
            raise ImportantDateError("revision_conflict", "important date changed or is unavailable")
        resolved_year = date_year if date_year is not None else row["date_year"]
        validation_year = resolved_year if row["recurrence"] == "once" else 2000
        if validation_year is None or not _valid_date(validation_year, date_month, date_day):
            raise ImportantDateError("date_invalid", "confirmed date is invalid")
        revision = expected_revision + 1
        conn.execute(
            "UPDATE important_dates SET status='active',date_year=?,date_month=?,date_day=?,revision=?,updated_at=? "
            "WHERE id=? AND revision=?", (resolved_year, date_month, date_day, revision, now, item_id, expected_revision),
        )
        conn.execute(
            "INSERT INTO important_date_events(id,important_date_id,event_type,from_status,to_status,revision,"
            "reason_code,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), item_id, "confirmed", "candidate", "active", revision, "user_confirmed", now),
        )
        conn.commit()
        return get(item_id, conn=conn)
    finally:
        conn.close()


def next_occurrence(item: dict[str, Any], *, today: date) -> date | None:
    if item["status"] != "active" or item["date_month"] is None or item["date_day"] is None:
        return None
    if item["recurrence"] == "once":
        candidate = date(item["date_year"], item["date_month"], item["date_day"])
        return candidate if candidate >= today else None
    for year in range(today.year, today.year + 9):
        if not _valid_date(year, item["date_month"], item["date_day"]):
            continue
        candidate = date(year, item["date_month"], item["date_day"])
        if candidate >= today:
            return candidate
    return None


def phase(item: dict[str, Any], *, today: date) -> str:
    if item["status"] == "active" and item["date_month"] is not None and item["date_day"] is not None:
        years = [item["date_year"]] if item["recurrence"] == "once" else [today.year]
        for year in years:
            if year and _valid_date(year, item["date_month"], item["date_day"]):
                previous = date(year, item["date_month"], item["date_day"])
                if 1 <= (today - previous).days <= 3:
                    return "follow_up"
    occurrence = next_occurrence(item, today=today)
    if occurrence is None:
        return "missed"
    delta = (occurrence - today).days
    if delta == 0:
        return "day"
    if 1 <= delta <= 7 and item["celebration_policy"] == "natural":
        return "preparation"
    return "upcoming"


def proactive_allowed(item: dict[str, Any], *, today: date) -> bool:
    return bool(
        item["status"] == "active" and item["celebration_policy"] != "none"
        and phase(item, today=today) in {"preparation", "day"}
    )


def remove_source(*, source_kind: str, source_id: str, now: float | None = None) -> int:
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT important_date_id FROM important_date_sources WHERE source_kind=? AND source_id=? AND active=1",
            (source_kind, source_id),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE important_date_sources SET active=0,removed_at=? WHERE important_date_id=? "
                "AND source_kind=? AND source_id=?", (now, row["important_date_id"], source_kind, source_id),
            )
            remaining = conn.execute(
                "SELECT 1 FROM important_date_sources WHERE important_date_id=? AND active=1 LIMIT 1",
                (row["important_date_id"],),
            ).fetchone()
            if not remaining:
                conn.execute(
                    "UPDATE important_dates SET status='revoked',revision=revision+1,updated_at=? WHERE id=?",
                    (now, row["important_date_id"]),
                )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def crossings(*, interval_start: float, interval_end: float) -> tuple[DateCrossing, ...]:
    if interval_end < interval_start:
        return ()
    start_day = datetime.fromtimestamp(interval_start, timezone.utc).date()
    end_day = datetime.fromtimestamp(interval_end, timezone.utc).date()
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM important_dates WHERE status='active'").fetchall()
    finally:
        conn.close()
    result: list[DateCrossing] = []
    for row in rows:
        item = dict(row)
        cursor = start_day
        while cursor <= end_day:
            occurrence = next_occurrence(item, today=cursor)
            if occurrence is None or occurrence > end_day:
                break
            timestamp = datetime.combine(occurrence, time.min, timezone.utc).timestamp()
            if interval_start < timestamp <= interval_end:
                result.append(DateCrossing(item["id"], str(item["revision"]), timestamp))
            cursor = occurrence + timedelta(days=1)
    return tuple(sorted(result, key=lambda value: (value.occurrence_at, value.id)))

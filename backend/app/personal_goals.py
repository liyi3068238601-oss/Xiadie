"""LIFE.6 explicit-source PersonalGoal lifecycle and bounded schedule influence."""
from __future__ import annotations

import re
from typing import Any

from . import db

ACTIVATION_CONFIDENCE = 0.85
SOURCE_KINDS = frozenset({"persona", "user_explicit", "important_date", "diary_reflection", "life_event"})
TRANSITIONS = {
    "candidate": frozenset({"active", "revoked"}),
    "active": frozenset({"paused", "completed", "revoked"}),
    "paused": frozenset({"active", "revoked"}),
    "completed": frozenset(),
    "revoked": frozenset(),
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class GoalError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def create_candidate(*, title: str, priority: int, confidence: float, source_kind: str,
                     source_id: str, source_revision: str, source_hash: str,
                     explicit_confirmation: bool = False, target_date: str | None = None,
                     now: float | None = None) -> dict[str, Any]:
    if not isinstance(title, str) or not title or len(title) > 160 or priority not in range(1, 6):
        raise GoalError("goal_invalid", "goal title or priority is invalid")
    if not 0 <= confidence <= 1 or source_kind not in SOURCE_KINDS:
        raise GoalError("source_invalid", "goal source or confidence is invalid")
    if not source_id or not source_revision or not _HEX64.fullmatch(source_hash):
        raise GoalError("source_invalid", "goal source identity is invalid")
    now = db.now() if now is None else now
    goal_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO personal_goals(id,title,status,priority,confidence,revision,target_date,created_at,updated_at) "
            "VALUES(?,?,?,?,?,1,?,?,?)",
            (goal_id, title, "candidate", priority, confidence, target_date, now, now),
        )
        conn.execute(
            "INSERT INTO personal_goal_sources(id,goal_id,source_kind,source_id,source_revision,"
            "source_hash,explicit_confirmation,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), goal_id, source_kind, source_id, source_revision, source_hash,
             int(explicit_confirmation), now),
        )
        conn.execute(
            "INSERT INTO personal_goal_events(id,goal_id,event_type,from_status,to_status,revision,reason_code,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), goal_id, "created", None, "candidate", 1, "candidate_created", now),
        )
        conn.commit()
        return get_goal(goal_id, conn=conn)
    finally:
        conn.close()


def get_goal(goal_id: str, *, conn=None) -> dict[str, Any] | None:
    owned = conn is None
    conn = db.connect() if conn is None else conn
    try:
        row = conn.execute("SELECT * FROM personal_goals WHERE id=?", (goal_id,)).fetchone()
        if not row:
            return None
        sources = conn.execute(
            "SELECT source_kind,source_id,source_revision,source_hash,explicit_confirmation "
            "FROM personal_goal_sources WHERE goal_id=? ORDER BY created_at,id", (goal_id,),
        ).fetchall()
        return dict(row) | {"sources": [dict(source) for source in sources]}
    finally:
        if owned:
            conn.close()


def transition(goal_id: str, *, expected_revision: int, to_status: str,
               reason_code: str, now: float | None = None) -> dict[str, Any]:
    if not reason_code:
        raise GoalError("reason_required", "goal transition reason is required")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM personal_goals WHERE id=?", (goal_id,)).fetchone()
        if not row or row["revision"] != expected_revision:
            raise GoalError("revision_conflict", "goal changed or was not found")
        if to_status not in TRANSITIONS[row["status"]]:
            raise GoalError("transition_invalid", "goal transition is invalid")
        if to_status == "active":
            explicit_source = conn.execute(
                "SELECT 1 FROM personal_goal_sources WHERE goal_id=? AND source_kind='user_explicit' "
                "AND explicit_confirmation=1 LIMIT 1", (goal_id,),
            ).fetchone()
            owned_source = conn.execute(
                "SELECT 1 FROM personal_goal_sources WHERE goal_id=? "
                "AND source_kind IN ('persona','diary_reflection') LIMIT 1", (goal_id,),
            ).fetchone()
            if (not explicit_source and not owned_source) or row["confidence"] < ACTIVATION_CONFIDENCE:
                raise GoalError("activation_not_authorized", "goal lacks explicit high-confidence activation")
        revision = expected_revision + 1
        cursor = conn.execute(
            "UPDATE personal_goals SET status=?,revision=?,updated_at=? WHERE id=? AND revision=?",
            (to_status, revision, now, goal_id, expected_revision),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise GoalError("revision_conflict", "goal changed concurrently")
        conn.execute(
            "INSERT INTO personal_goal_events(id,goal_id,event_type,from_status,to_status,revision,reason_code,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), goal_id, "transition", row["status"], to_status, revision, reason_code, now),
        )
        conn.commit()
        return get_goal(goal_id, conn=conn)
    finally:
        conn.close()


def active_for_schedule(*, limit: int = 3) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 3))
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id FROM personal_goals WHERE status='active' ORDER BY priority DESC,updated_at ASC,id LIMIT ?",
            (20,),
        ).fetchall()
        goals = [get_goal(row["id"], conn=conn) for row in rows]
    finally:
        conn.close()
    if limit == 1 or len(goals) <= 1:
        return goals[:limit]
    user = [item for item in goals if any(source["source_kind"] == "user_explicit" for source in item["sources"])]
    independent = [item for item in goals if any(source["source_kind"] != "user_explicit" for source in item["sources"])]
    selected: list[dict[str, Any]] = []
    for pool in (independent, user, goals):
        for item in pool:
            if item not in selected and len(selected) < limit:
                selected.append(item)
    return selected


def record_progress(goal_id: str, *, expected_revision: int, reason_code: str,
                    now: float | None = None) -> dict[str, Any]:
    if not reason_code:
        raise GoalError("reason_required", "goal progress reason is required")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM personal_goals WHERE id=?", (goal_id,)).fetchone()
        if not row or row["revision"] != expected_revision or row["status"] not in {"active", "paused"}:
            raise GoalError("revision_conflict", "goal changed or cannot record progress")
        revision = expected_revision + 1
        cursor = conn.execute(
            "UPDATE personal_goals SET revision=?,updated_at=? WHERE id=? AND revision=?",
            (revision, now, goal_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise GoalError("revision_conflict", "goal changed concurrently")
        conn.execute(
            "INSERT INTO personal_goal_events(id,goal_id,event_type,from_status,to_status,revision,reason_code,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), goal_id, "progress", row["status"], row["status"], revision, reason_code, now),
        )
        conn.commit()
        return get_goal(goal_id, conn=conn)
    finally:
        conn.close()


def future_replan_candidates(*, schedule_id: str, current_minute: int) -> list[dict[str, Any]]:
    """Return at most three future segment/goal bindings; never mutate current or past segments."""
    if not 0 <= current_minute <= 1440:
        raise GoalError("time_invalid", "current minute is invalid")
    goals = active_for_schedule(limit=3)
    if not goals:
        return []
    conn = db.connect()
    try:
        segments = conn.execute(
            "SELECT id,start_minute,end_minute,activity_code FROM life_schedule_segments "
            "WHERE schedule_id=? AND start_minute>=? AND detail_status!='cancelled' "
            "ORDER BY start_minute LIMIT 3", (schedule_id, current_minute),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"goal_id": goal["id"], "goal_revision": goal["revision"],
         "segment_id": segment["id"], "segment_start_minute": segment["start_minute"]}
        for goal, segment in zip(goals, segments)
    ]

from __future__ import annotations

import hashlib

import pytest

from app import db, life_schedule, personal_goals


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def clean_goals():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM personal_goal_events")
        conn.execute("DELETE FROM personal_goal_sources")
        conn.execute("DELETE FROM personal_goals")
        conn.execute("DELETE FROM life_event_candidates")
        conn.execute("DELETE FROM life_schedule_segments")
        conn.execute("DELETE FROM life_schedules")
        conn.commit()
    finally:
        conn.close()


def _goal(*, explicit=True, confidence=0.9, title="练习绘画", priority=3):
    return personal_goals.create_candidate(
        title=title, priority=priority, confidence=confidence,
        source_kind="user_explicit" if explicit else "important_date",
        source_id=title, source_revision="1", source_hash=_hash(title),
        explicit_confirmation=explicit,
    )


def _sourced_goal(*, source_kind: str, title: str, priority: int):
    return personal_goals.create_candidate(
        title=title, priority=priority, confidence=0.9, source_kind=source_kind,
        source_id=title, source_revision="1", source_hash=_hash(title),
        explicit_confirmation=False,
    )


def test_schema_68_adds_goal_fsm_without_tool_authority():
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(personal_goals)")}
    finally:
        conn.close()
    assert version == "76"
    assert {"status", "confidence", "revision", "priority"} <= columns
    assert not ({"tool_run_id", "delivery_id", "execution_status"} & columns)


def test_only_explicit_high_confidence_candidate_can_activate():
    inferred = _goal(explicit=False, confidence=0.99)
    with pytest.raises(personal_goals.GoalError) as no_explicit:
        personal_goals.transition(inferred["id"], expected_revision=1, to_status="active", reason_code="try")
    assert no_explicit.value.code == "activation_not_authorized"
    low = _goal(explicit=True, confidence=0.6, title="低置信目标")
    with pytest.raises(personal_goals.GoalError) as low_confidence:
        personal_goals.transition(low["id"], expected_revision=1, to_status="active", reason_code="try")
    assert low_confidence.value.code == "activation_not_authorized"
    explicit = _goal()
    active = personal_goals.transition(
        explicit["id"], expected_revision=1, to_status="active", reason_code="user_confirmed",
    )
    assert active["status"] == "active" and active["revision"] == 2


def test_fsm_rejects_illegal_and_stale_transitions():
    goal = _goal()
    with pytest.raises(personal_goals.GoalError):
        personal_goals.transition(goal["id"], expected_revision=1, to_status="completed", reason_code="skip")
    active = personal_goals.transition(goal["id"], expected_revision=1, to_status="active", reason_code="confirmed")
    completed = personal_goals.transition(active["id"], expected_revision=2, to_status="completed", reason_code="done")
    assert completed["status"] == "completed"
    with pytest.raises(personal_goals.GoalError):
        personal_goals.transition(goal["id"], expected_revision=2, to_status="active", reason_code="stale")


def test_schedule_consumes_at_most_three_active_goals_by_priority():
    for index in range(5):
        goal = _goal(title=f"目标-{index}", priority=(index % 5) + 1)
        personal_goals.transition(goal["id"], expected_revision=1, to_status="active", reason_code="confirmed")
    selected = personal_goals.active_for_schedule(limit=99)
    assert len(selected) == 3
    assert [item["priority"] for item in selected] == [5, 4, 3]


def test_replanning_only_returns_future_segments_and_never_mutates_schedule():
    schedule, _ = life_schedule.create_schedule(local_date="2026-07-26", timezone_id="Asia/Shanghai")
    for index in range(2):
        goal = _goal(title=f"目标-{index}", priority=5 - index)
        personal_goals.transition(goal["id"], expected_revision=1, to_status="active", reason_code="confirmed")
    before = life_schedule.get_schedule(schedule["id"])
    proposals = personal_goals.future_replan_candidates(schedule_id=schedule["id"], current_minute=800)
    after = life_schedule.get_schedule(schedule["id"])
    assert len(proposals) <= 3 and all(item["segment_start_minute"] >= 800 for item in proposals)
    assert before == after


def test_paused_completed_and_revoked_goals_do_not_shape_schedule():
    statuses = ("paused", "completed", "revoked")
    for status in statuses:
        goal = _goal(title=f"目标-{status}")
        active = personal_goals.transition(goal["id"], expected_revision=1, to_status="active", reason_code="confirmed")
        personal_goals.transition(active["id"], expected_revision=2, to_status=status, reason_code=status)
    assert personal_goals.active_for_schedule() == []


def test_goal_events_are_body_free_transition_audit():
    goal = _goal()
    personal_goals.transition(goal["id"], expected_revision=1, to_status="active", reason_code="confirmed")
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM personal_goal_events WHERE goal_id=?", (goal["id"],)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert "title" not in rows[0].keys() and "content" not in rows[0].keys()


def test_progress_is_revisioned_and_balance_keeps_independent_and_user_lines():
    persona = _sourced_goal(source_kind="persona", title="独立创作", priority=1)
    persona = personal_goals.transition(
        persona["id"], expected_revision=1, to_status="active", reason_code="persona_policy",
    )
    user = _goal(title="用户约定", priority=5)
    user = personal_goals.transition(user["id"], expected_revision=1, to_status="active", reason_code="confirmed")
    progressed = personal_goals.record_progress(
        user["id"], expected_revision=2, reason_code="small_step_completed",
    )
    assert progressed["revision"] == 3
    selected = personal_goals.active_for_schedule(limit=2)
    assert {item["id"] for item in selected} == {persona["id"], user["id"]}

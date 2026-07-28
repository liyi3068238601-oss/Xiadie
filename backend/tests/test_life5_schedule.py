"""LIFE.5 schedule validation, fallback diversity and detail candidates."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, life_schedule
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def clean_schedules():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM life_event_candidates")
        conn.execute("DELETE FROM life_schedule_replacements")
        conn.execute("DELETE FROM life_schedule_segments")
        conn.execute("DELETE FROM life_schedules")
        conn.commit()
    finally:
        conn.close()


def test_schema_67_adds_versioned_schedule_and_candidate_tables():
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert version == "75"
    assert {"life_schedules", "life_schedule_segments", "life_schedule_replacements", "life_event_candidates"} <= tables


def test_fallback_schedule_covers_day_without_overlap_or_gap():
    segments = life_schedule.fallback_segments("2026-07-26")
    life_schedule.validate_segments(segments)
    assert segments[0].start_minute == 0 and segments[-1].end_minute == 1440
    assert all(left.end_minute == right.start_minute for left, right in zip(segments, segments[1:]))


@pytest.mark.parametrize(
    "segments,code",
    [
        ((life_schedule.SegmentProposal(0, 100, "sleep", "a"),
          life_schedule.SegmentProposal(90, 1440, "reading", "b")), "schedule_time_invalid"),
        ((life_schedule.SegmentProposal(0, 100, "sleep", "a"),
          life_schedule.SegmentProposal(200, 1440, "reading", "b")), "schedule_time_invalid"),
        ((life_schedule.SegmentProposal(0, 1440, "tool_action", "a"),), "activity_not_allowed"),
    ],
)
def test_validator_rejects_overlap_gap_and_forbidden_action(segments, code):
    with pytest.raises(life_schedule.ScheduleError) as exc:
        life_schedule.validate_segments(segments)
    assert exc.value.code == code


def test_fallback_varies_across_dates_without_random_state():
    labels = [life_schedule.fallback_segments(day)[5].label for day in (
        "2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29",
    )]
    assert len(set(labels)) == 3
    assert life_schedule.fallback_segments("2026-07-26") == life_schedule.fallback_segments("2026-07-26")


def test_create_is_idempotent_for_active_day_and_api_is_read_only():
    first, created = life_schedule.create_schedule(local_date="2026-07-26", timezone_id="Asia/Shanghai")
    second, created_again = life_schedule.create_schedule(local_date="2026-07-26", timezone_id="Asia/Shanghai")
    assert created is True and created_again is False and first["id"] == second["id"]
    response = client.get("/api/life/schedules/2026-07-26?timezone_id=Asia%2FShanghai")
    assert response.status_code == 200 and response.json()["item"]["id"] == first["id"]
    assert client.post("/api/life/schedules/2026-07-26", json={}).status_code == 405


def test_create_rejects_invalid_timezone_before_persistence():
    with pytest.raises(life_schedule.ScheduleError) as exc:
        life_schedule.create_schedule(local_date="2026-07-26", timezone_id="Mars/Olympus")
    assert exc.value.code == "timezone_invalid"
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM life_schedules").fetchone()[0] == 0
    finally:
        conn.close()


def test_detail_only_creates_planned_candidate_not_diary_or_delivery():
    conn = db.connect()
    try:
        deliveries_before = conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0]
        diary_before = conn.execute("SELECT COUNT(*) FROM diary_entries").fetchone()[0]
    finally:
        conn.close()
    schedule, _ = life_schedule.create_schedule(local_date="2026-07-26", timezone_id="Asia/Shanghai")
    segment = schedule["segments"][1]
    candidate, created = life_schedule.detail_segment(
        segment["id"], expected_revision=0, summary="整理窗边的晨间笔记",
    )
    assert created is True and candidate["world_layer"] == "planned"
    assert candidate["status"] == "proposed" and candidate["source_kind"] == "schedule_segment"
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0] == deliveries_before
        assert conn.execute("SELECT COUNT(*) FROM diary_entries").fetchone()[0] == diary_before
    finally:
        conn.close()


def test_stale_detail_revision_is_rejected_and_no_duplicate_candidate_created():
    schedule, _ = life_schedule.create_schedule(local_date="2026-07-26", timezone_id="Asia/Shanghai")
    segment = schedule["segments"][1]
    life_schedule.detail_segment(segment["id"], expected_revision=0, summary="晨间整理")
    with pytest.raises(life_schedule.ScheduleError) as exc:
        life_schedule.detail_segment(segment["id"], expected_revision=0, summary="过期细化")
    assert exc.value.code == "revision_conflict"
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM life_event_candidates").fetchone()[0] == 1
    finally:
        conn.close()

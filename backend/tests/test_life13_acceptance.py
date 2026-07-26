"""LIFE.13 long-horizon, provenance, timezone, budget and retention gates."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import (
    db, diary, important_dates, life_catchup, life_retention, life_schedule,
    llm, personal_goals, self_timeline,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def clean_acceptance_records():
    def clear() -> None:
        conn = db.connect()
        try:
            for table in (
                "life_catchup_candidates", "life_catchup_requests", "life_exit_snapshots",
                "life_runtime_events", "life_event_candidates", "important_date_events",
                "important_date_sources", "important_dates", "personal_goal_events",
                "personal_goal_sources", "personal_goals",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    clear()
    yield
    clear()


def test_180_day_schedule_timeline_is_deterministic_complete_and_bounded():
    start = date(2028, 1, 1)
    signatures = []
    for offset in range(180):
        local_date = (start + timedelta(days=offset)).isoformat()
        segments = life_schedule.fallback_segments(local_date)
        life_schedule.validate_segments(segments)
        assert segments[0].start_minute == 0 and segments[-1].end_minute == 1440
        assert len(segments) <= 24
        signatures.append(tuple((item.activity_code, item.label) for item in segments))
    assert 2 <= len(set(signatures)) <= 3
    assert signatures[0] == tuple(
        (item.activity_code, item.label) for item in life_schedule.fallback_segments(start.isoformat())
    )


def test_30_day_diary_fallback_has_continuity_without_exact_repetition():
    entries = [
        diary.deterministic_fallback_text(
            entry_date=f"2028-06-{day:02d}", source_summary=f"第 {day} 天的合成生活片段",
        )
        for day in range(1, 31)
    ]
    assert len(entries) == len(set(entries)) == 30
    assert len({body.split("：", 1)[0] for _, body in entries}) == 5
    assert all("合成生活片段" in body for _, body in entries)


def test_100_important_date_timezone_scenarios_resolve_local_midnight():
    zones = (
        "Pacific/Honolulu", "America/Los_Angeles", "America/Denver", "America/Chicago",
        "America/New_York", "America/Sao_Paulo", "Atlantic/Azores", "Europe/London",
        "Europe/Paris", "Europe/Helsinki", "Africa/Cairo", "Africa/Johannesburg",
        "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai", "Asia/Tokyo",
        "Australia/Perth", "Australia/Sydney", "Pacific/Auckland",
    )
    calendar_days = ((1, 15), (2, 29), (4, 30), (7, 26), (12, 31))
    expected = {}
    for zone_index, zone in enumerate(zones):
        for day_index, (month, day) in enumerate(calendar_days):
            source_id = f"date-{zone_index}-{day_index}"
            item = important_dates.create_candidate(
                label=source_id, recurrence="once", date_year=2028, date_month=month,
                date_day=day, timezone_id=zone, confidence=1.0, source_kind="manual",
                source_id=source_id, source_revision="1", source_hash=_hash(source_id),
            )
            active = important_dates.confirm(
                item["id"], expected_revision=1, date_year=2028, date_month=month, date_day=day,
            )
            assert important_dates.next_occurrence(active, today=date(2028, 1, 1)) == date(2028, month, day)
            expected[item["id"]] = datetime(2028, month, day, tzinfo=ZoneInfo(zone)).timestamp()
    crossings = important_dates.crossings(
        interval_start=datetime(2027, 12, 29, tzinfo=timezone.utc).timestamp(),
        interval_end=datetime(2029, 1, 2, tzinfo=timezone.utc).timestamp(),
    )
    assert len(crossings) == 100
    assert {item.id: item.occurrence_at for item in crossings} == expected


def test_invalid_important_date_timezone_is_rejected():
    with pytest.raises(important_dates.ImportantDateError) as exc:
        important_dates.create_candidate(
            label="bad zone", recurrence="once", date_year=2028, date_month=1, date_day=1,
            timezone_id="Mars/Olympus", confidence=1.0, source_kind="manual",
            source_id="bad-zone", source_revision="1", source_hash=_hash("bad-zone"),
        )
    assert exc.value.code == "timezone_invalid"


def test_100_world_layer_source_combinations_never_confuse_plan_with_performed():
    layers = ("planned", "simulated", "inferred", "observed", "performed")
    source_types = (
        "life_event", "diary_entry", "schedule_segment", "tool_run", "proactive_delivery",
        "personal_goal", "memory", "knowledge", "lore", "episode", "saga", "user_message",
        "model_guess", "catchup", "important_date", "calendar", "runtime", "summary",
        "candidate", "unknown",
    )
    confirmed = 0
    for layer in layers:
        for source_type in source_types:
            rendered = self_timeline.epistemic_expression({
                "summary": f"{layer}:{source_type}", "world_layer": layer, "source_type": source_type,
            })
            is_confirmed = rendered.startswith("确实完成：")
            assert is_confirmed == (layer == "performed" and source_type in {
                "life_event", "tool_run", "proactive_delivery",
            })
            if layer == "planned":
                assert rendered.startswith("原本打算：")
            confirmed += int(is_confirmed)
    assert confirmed == 3


def test_default_and_explicit_reasoner_token_budgets_remain_bounded():
    assert llm.JSON_COMPLETION_MAX_TOKENS == 500
    assert llm.JSON_COMPLETION_HARD_MAX_TOKENS == 2_048
    assert life_catchup.MAX_MODEL_CALLS == 2


def test_retention_dry_run_and_apply_preserve_authoritative_records():
    cutoff = 2_000.0
    important_dates.create_candidate(
        label="保留日期", recurrence="once", date_year=2028, date_month=7, date_day=26,
        timezone_id="Asia/Shanghai", confidence=1.0, source_kind="manual", source_id="keep-date",
        source_revision="1", source_hash=_hash("keep-date"), now=100,
    )
    personal_goals.create_candidate(
        title="保留目标", priority=3, confidence=1.0, source_kind="user_explicit",
        source_id="keep-goal", source_revision="1", source_hash=_hash("keep-goal"),
        explicit_confirmation=True, now=100,
    )
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO life_event_candidates(id,source_kind,source_id,source_revision,event_kind,"
            "world_layer,summary,status,idempotency_key,created_at,updated_at) "
            "VALUES('stale-candidate','schedule_segment','s','1','activity','planned','old','rejected','stale',100,100)"
        )
        for index, created_at in enumerate((100.0, 3_000.0)):
            conn.execute(
                "INSERT INTO life_exit_snapshots(id,exited_at,timezone_snapshot,schedule_revision,state_revision,"
                "algorithm_version,created_at) VALUES(?,?,?,?,?,?,?)",
                (f"snap-{index}", created_at, "Asia/Shanghai", "none", 1, "v1", created_at),
            )
        conn.execute(
            "INSERT INTO life_catchup_requests(catchup_id,exit_snapshot_id,interval_start,interval_end,"
            "timezone_snapshot,schedule_revision,state_revision,algorithm_version,deterministic_seed,"
            "materialization_revision,span_strategy,status,candidate_count,model_call_count,idempotency_key,"
            "created_at,completed_at) VALUES('old-catchup','snap-0',100,200,'Asia/Shanghai','none',1,'v1',"
            "'seed',2,'detailed','applied',0,0,'old-catchup-key',100,200)"
        )
        for index in range(40):
            conn.execute(
                "INSERT INTO life_runtime_events(id,from_revision,to_revision,elapsed_seconds,event_type,"
                "algorithm_version,created_at) VALUES(?,?,?,?,?,?,?)",
                (f"runtime-{index}", index, index + 1, 60, "advanced", "v1", 100 + index),
            )
        conn.commit()
    finally:
        conn.close()
    preview = life_retention.compact_derived(cutoff=cutoff, dry_run=True)
    assert preview["stale_event_candidates"] == 1
    assert preview["catchup_requests"] == 1 and preview["orphan_exit_snapshots"] == 1
    assert preview["runtime_events"] == 8
    applied = life_retention.compact_derived(cutoff=cutoff, dry_run=False)
    assert applied["authoritative_before"] == applied["authoritative_after"]
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM life_event_candidates").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM life_catchup_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM life_exit_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM life_runtime_events").fetchone()[0] == 32
        assert conn.execute("SELECT COUNT(*) FROM important_dates").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM personal_goals").fetchone()[0] == 1
    finally:
        conn.close()

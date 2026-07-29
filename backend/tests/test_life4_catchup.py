"""LIFE.4 bounded startup catch-up without background actions."""
from __future__ import annotations

import asyncio

import pytest

from app import db, life_catchup, life_catchup_service, life_runtime

START = 1_700_000_000.0


@pytest.fixture(autouse=True)
def clean_catchup():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM life_catchup_candidates")
        conn.execute("DELETE FROM life_catchup_requests")
        conn.execute("DELETE FROM life_exit_snapshots")
        conn.execute("DELETE FROM life_runtime_events")
        conn.execute("DELETE FROM life_runtime_state")
        conn.execute("DELETE FROM life_runtime_lease")
        conn.execute("DELETE FROM tool_logs")
        conn.execute("DELETE FROM proactive_deliveries")
        conn.execute("DELETE FROM settings WHERE key='life_continuity_mode'")
        conn.commit()
    finally:
        conn.close()


def _prepare_exit(*, start: float = START):
    assert life_runtime.acquire_lease(
        process_instance_id="exit-process", boot_session_id="exit-boot", lease_token="exit-token",
        now=start, ttl_seconds=1,
    )
    life_runtime.materialize(
        lease_token="exit-token", now=start, timezone_id="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    return life_catchup.record_exit_snapshot(
        exited_at=start, timezone_snapshot="Asia/Shanghai", schedule_revision="none",
    )


def _run(hours: float, *, date_crossings=()):
    _prepare_exit()
    end = START + hours * 3600
    assert life_runtime.acquire_lease(
        process_instance_id="startup-process", boot_session_id="startup-boot",
        lease_token="startup-token", now=end, ttl_seconds=60,
    )
    result = life_catchup.run_catchup(
        interval_end=end, lease_token="startup-token", timezone_snapshot="Asia/Shanghai",
        modulation=life_runtime.Modulation(), date_crossings=date_crossings,
    )
    return end, result


def test_schema_66_defaults_existing_users_to_continuous_simulated():
    assert life_catchup.get_mode() == life_catchup.MODE_CONTINUOUS
    migration = next(sql for version, sql in db.MIGRATIONS if version == 66)
    assert "life_continuity_mode','continuous_simulated" in migration
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert version == "81"
    assert {"life_exit_snapshots", "life_catchup_requests", "life_catchup_candidates"} <= tables


@pytest.mark.parametrize(
    ("hours", "strategy", "max_candidates"),
    [(1 / 3, "detailed", 1), (8, "daily", 1), (72, "daily", 3),
     (30 * 24, "weekly", 5), (180 * 24, "regression_transition", 3)],
)
def test_required_offline_spans_start_and_remain_bounded(hours, strategy, max_candidates):
    _, result = _run(hours)
    assert result["status"] == "applied" and result["span_strategy"] == strategy
    assert result["candidate_count"] == max_candidates
    assert result["candidate_count"] <= life_catchup.MAX_CANDIDATES
    assert result["model_call_count"] == 0 <= life_catchup.MAX_MODEL_CALLS
    candidates = life_catchup.list_candidates(result["catchup_id"])
    assert all(item["world_layer"] == "simulated" for item in candidates)


def test_catchup_identity_freezes_all_required_fields_and_is_deterministic():
    end, first = _run(8)
    second = life_catchup.run_catchup(
        interval_end=end, lease_token="startup-token", timezone_snapshot="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    assert second["duplicate"] is True
    assert second["catchup_id"] == first["catchup_id"]
    assert second["deterministic_seed"] == first["deterministic_seed"]
    assert second["interval_start"] == START and second["interval_end"] == end
    assert second["timezone_snapshot"] == "Asia/Shanghai"
    assert second["schedule_revision"] == "none"
    assert second["state_revision"] == 1
    assert second["algorithm_version"] == life_runtime.ALGORITHM_VERSION
    assert second["materialization_revision"] == 2
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM life_catchup_requests").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM life_runtime_events").fetchone()[0] == 2
    finally:
        conn.close()


def test_important_date_crossing_is_reliably_preserved_as_candidate():
    crossing = life_catchup.DateCrossing(
        id="date-1", revision="revision-7", occurrence_at=START + 12 * 3600,
    )
    _, result = _run(24, date_crossings=(crossing,))
    candidates = life_catchup.list_candidates(result["catchup_id"])
    matched = [item for item in candidates if item["candidate_kind"] == "important_date_crossing"]
    assert len(matched) == 1
    assert matched[0]["source_id"] == "date-1" and matched[0]["source_revision"] == "revision-7"


def test_out_of_interval_date_is_not_materialized():
    crossing = life_catchup.DateCrossing(
        id="date-outside", revision="1", occurrence_at=START - 1,
    )
    _, result = _run(24, date_crossings=(crossing,))
    assert not [
        item for item in life_catchup.list_candidates(result["catchup_id"])
        if item["candidate_kind"] == "important_date_crossing"
    ]


@pytest.mark.parametrize("mode", [life_catchup.MODE_PAUSED, life_catchup.MODE_DISABLED])
def test_pause_and_disable_skip_without_materialization(mode: str):
    _prepare_exit()
    life_catchup.set_mode(mode)
    result = life_catchup.run_catchup(
        interval_end=START + 8 * 3600, lease_token="not-needed", timezone_snapshot="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    assert result == {"status": "skipped", "reason_code": f"mode_{mode}", "candidate_count": 0}
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM life_catchup_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM life_runtime_events").fetchone()[0] == 1
    finally:
        conn.close()


def test_offline_catchup_never_creates_tool_network_or_message_delivery_claims():
    _, result = _run(180 * 24)
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM tool_logs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0] == 0
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(life_catchup_candidates)")}
    finally:
        conn.close()
    assert result["status"] == "applied"
    assert not ({"tool_run_id", "delivery_id", "network_request_id"} & columns)


def test_wall_clock_rollback_is_skipped_conservatively():
    _prepare_exit()
    result = life_catchup.run_catchup(
        interval_end=START - 1000, lease_token="none", timezone_snapshot="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    assert result == {"status": "skipped", "reason_code": "wall_clock_rollback", "candidate_count": 0}


def test_application_lifecycle_initializes_state_records_exit_and_releases_lease():
    async def exercise():
        started = await life_catchup_service.start()
        assert started["status"] == "skipped" and started["reason_code"] == "no_exit_snapshot"
        assert life_runtime.get_state() is not None
        await life_catchup_service.stop()

    asyncio.run(exercise())
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM life_exit_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM life_runtime_lease").fetchone()[0] == 0
    finally:
        conn.close()

"""LIFE.3 deterministic continuity, lease and time anomaly tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, life_runtime
from app.affect import repository as affect_repository
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def clean_runtime():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM life_runtime_events")
        conn.execute("DELETE FROM life_runtime_state")
        conn.execute("DELETE FROM life_runtime_lease")
        conn.commit()
    finally:
        conn.close()


def _lease(token: str = "lease-a", *, now: float = 1_000_000.0, ttl: float = 60.0) -> None:
    assert life_runtime.acquire_lease(
        process_instance_id="process-a", boot_session_id="boot-a", lease_token=token,
        now=now, ttl_seconds=ttl,
    )


def test_schema_65_adds_singleton_runtime_and_lease():
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert version == "66"
    assert {"life_runtime_state", "life_runtime_lease", "life_runtime_events"} <= tables


@pytest.mark.parametrize("hours", [1, 8, 24, 72, 168])
def test_reducer_is_deterministic_finite_and_bounded_at_required_horizons(hours: int):
    state = life_runtime.default_state(now=1_700_000_000.0, timezone_id="Asia/Shanghai")
    modulation = life_runtime.Modulation(
        contact_need=0.6, valence=0.1, arousal=0.2, bond=0.4, trust=0.7,
    )
    first = life_runtime.reduce_state(state, elapsed_seconds=hours * 3600, modulation=modulation)
    second = life_runtime.reduce_state(state, elapsed_seconds=hours * 3600, modulation=modulation)
    assert first == second
    assert first.logical_time == state.logical_time + hours * 3600
    for value in (first.energy, first.focus, first.rest_need, first.social_openness):
        assert 0 <= value <= 1
    assert first.current_activity in life_runtime.ACTIVITIES


def test_affect_and_relationship_are_read_only_modulation_inputs():
    before = affect_repository.get_snapshot(advance_time=False)
    modulation = life_runtime.read_affect_modulation()
    state = life_runtime.default_state(now=1_700_000_000.0, timezone_id="Asia/Shanghai")
    life_runtime.reduce_state(state, elapsed_seconds=8 * 3600, modulation=modulation)
    after = affect_repository.get_snapshot(advance_time=False)
    assert after == before


def test_only_one_unexpired_materializer_can_hold_the_database_lease():
    assert life_runtime.acquire_lease(
        process_instance_id="p1", boot_session_id="b1", lease_token="token-1", now=100.0,
    )
    assert not life_runtime.acquire_lease(
        process_instance_id="p2", boot_session_id="b2", lease_token="token-2", now=101.0,
    )
    assert life_runtime.heartbeat_lease(lease_token="token-1", now=102.0)
    assert not life_runtime.heartbeat_lease(lease_token="token-2", now=102.0)
    assert life_runtime.acquire_lease(
        process_instance_id="p2", boot_session_id="b2", lease_token="token-2", now=200.0,
    )


def test_materialize_requires_owned_lease_and_handles_sleep_restart_elapsed_time():
    with pytest.raises(life_runtime.LifeRuntimeError) as unowned:
        life_runtime.materialize(
            lease_token="none", now=1_000_000.0, timezone_id="Asia/Shanghai",
            modulation=life_runtime.Modulation(),
        )
    assert unowned.value.code == "lease_not_owned"
    _lease(now=1_000_000.0, ttl=9 * 3600)
    initial = life_runtime.materialize(
        lease_token="lease-a", now=1_000_000.0, timezone_id="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    resumed = life_runtime.materialize(
        lease_token="lease-a", now=1_000_000.0 + 8 * 3600, timezone_id="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    assert resumed.revision == initial.revision + 1
    assert resumed.logical_time == initial.logical_time + 8 * 3600
    assert resumed.conservative_mode is False


def test_wall_clock_rollback_enters_conservative_mode_without_reverse_advance():
    _lease(now=1_000_000.0, ttl=3600)
    initial = life_runtime.materialize(
        lease_token="lease-a", now=1_000_000.0, timezone_id="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    rolled_back = life_runtime.materialize(
        lease_token="lease-a", now=999_000.0, timezone_id="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    assert rolled_back.logical_time == initial.logical_time
    assert rolled_back.conservative_mode is True
    assert rolled_back.anomaly_code == "wall_clock_rollback"


def test_timezone_change_is_detected_and_held_for_one_materialization():
    _lease(now=1_000_000.0, ttl=3600)
    initial = life_runtime.materialize(
        lease_token="lease-a", now=1_000_000.0, timezone_id="Asia/Shanghai",
        modulation=life_runtime.Modulation(),
    )
    changed = life_runtime.materialize(
        lease_token="lease-a", now=1_000_100.0, timezone_id="UTC",
        modulation=life_runtime.Modulation(),
    )
    assert changed.logical_time == initial.logical_time
    assert changed.timezone_id == "UTC"
    assert changed.anomaly_code == "timezone_changed"


def test_hysteresis_prevents_activity_thrashing_inside_minimum_duration():
    state = life_runtime.default_state(now=1_700_000_000.0, timezone_id="Asia/Shanghai")
    result = life_runtime.reduce_state(
        state, elapsed_seconds=life_runtime.MIN_ACTIVITY_SECONDS - 1,
        modulation=life_runtime.Modulation(arousal=1.0),
    )
    assert result.current_activity == state.current_activity
    assert result.activity_since == state.activity_since


def test_runtime_events_are_body_free_and_revision_ordered():
    _lease(now=1_000_000.0, ttl=3600)
    life_runtime.materialize(
        lease_token="lease-a", now=1_000_000.0, timezone_id="UTC",
        modulation=life_runtime.Modulation(),
    )
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM life_runtime_events").fetchone()
    finally:
        conn.close()
    assert set(row.keys()) == {
        "id", "from_revision", "to_revision", "elapsed_seconds", "event_type",
        "anomaly_code", "algorithm_version", "created_at",
    }
    assert row["from_revision"] == 0 and row["to_revision"] == 1


def test_life_state_api_is_read_only_and_honest_before_initialization():
    response = client.get("/api/life/state")
    assert response.status_code == 200
    assert response.json() == {
        "initialized": False, "algorithm_version": life_runtime.ALGORITHM_VERSION,
    }
    assert client.post("/api/life/state", json={}).status_code == 405

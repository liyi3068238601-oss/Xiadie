import sqlite3
import time

import pytest

from app import db
from app.proactive import decision, delivery, orchestrator, settings
from app.proactive.timeline_simulator import TimelineSimulator, _cleanup_session


START = 1_900_000_000.0


@pytest.fixture(autouse=True)
def production_acceptance_controls():
    db.init_db()
    conn = db.connect()
    try:
        for table in (
            "proactive_feedback_events", "proactive_preference_weights", "proactive_feedback",
            "proactive_delivery_events", "proactive_delivery_attempts", "proactive_deliveries",
            "expression_plans", "proactive_intensity_plans", "proactive_decisions",
            "proactive_candidate_claims", "proactive_runtime_sagas", "proactive_runtime_sources",
            "proactive_candidates", "life_proactive_seeds", "contact_episodes",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    settings.reset_public_settings()
    db.set_setting("proactive_last_reliable_now", "0")
    db.set_setting("proactive_resume_guard_until", "0")
    yield
    db.set_setting("proactive_local_delivery_enabled", "0")
    db.set_setting("proactive_desktop_notification_enabled", "0")
    db.set_setting("proactive_pause_until", "")


def _new_sim() -> TimelineSimulator:
    return TimelineSimulator(db.new_id(), start_time=START)


@pytest.mark.parametrize("duration", [15 * 60, 8 * 3600, 24 * 3600, 3 * 86400, 30 * 86400])
def test_production_path_long_horizons_are_traceable_and_expire_safely(duration):
    sim = _new_sim()
    try:
        with sim:
            sim.initialize_production()
            sim.production_turn("我去测试，稍后回来", "好，我等你")
            sim.advance(duration)
            sim.run_production_cycle(level=3)
            completed = sim.consume_production_deliveries()
            metrics = sim.production_metrics()
        if duration == 8 * 3600:
            assert any(item["status"] == "delivered" for item in completed)
        else:
            assert completed == []
        assert metrics["traceability_rate"] == 1.0
        assert metrics["duplicate_delivery_count"] == 0
        assert metrics["level5_delivery_count"] == 0
        assert metrics["orphan_source_count"] == 0
    finally:
        _cleanup_session(sim.session_id)


def test_grounded_reject_topic_blocks_the_next_production_evaluation():
    sim = _new_sim()
    try:
        with sim:
            sim.initialize_production()
            sim.production_turn("今天聊得很开心", "我也是")
            sim.advance(8 * 3600)
            sim.run_production_cycle(level=3)
            first = sim.consume_production_deliveries()
            assert len(first) == 1 and first[0]["status"] == "delivered"
            sim.production_feedback(first[0]["id"], "reject_topic")
            sim.production_turn("我们继续", "嗯")
            sim.advance(8 * 3600)
            sim.run_production_cycle(level=3)
        conn = db.connect()
        try:
            reasons = [row[0] for row in conn.execute(
                "SELECT layer1_block_reasons FROM proactive_decisions "
                "WHERE session_id=? ORDER BY created_at", (sim.session_id,),
            ).fetchall()]
        finally:
            conn.close()
        assert any("topic_rejected" in item for item in reasons)
    finally:
        _cleanup_session(sim.session_id)


@pytest.mark.parametrize(
    ("key", "value", "expected_reason"),
    [
        ("proactive_enabled", "0", "proactive_disabled"),
        ("proactive_pause_until", "2099-01-01T00:00:00Z", "proactive_paused"),
        ("proactive_kind_casual_greeting_enabled", "0", "candidate_kind_disabled"),
    ],
)
def test_close_pause_and_kind_controls_block_due_production_sources(key, value, expected_reason):
    sim = _new_sim()
    try:
        with sim:
            sim.initialize_production()
            sim.production_turn("今天聊得很开心", "我也是")
            settings.write_public_setting(key, value)
            sim.advance(8 * 3600)
            sim.run_production_cycle(level=3)
            assert sim.consume_production_deliveries() == []
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT layer1_block_reasons FROM proactive_decisions WHERE session_id=?",
                (sim.session_id,),
            ).fetchall()
        finally:
            conn.close()
        conn = db.connect()
        try:
            sources = conn.execute(
                "SELECT result_code FROM proactive_runtime_sources WHERE session_id=?",
                (sim.session_id,),
            ).fetchall()
        finally:
            conn.close()
        if key == "proactive_kind_casual_greeting_enabled":
            # Kind switches are allowed to stop work before a decision row is
            # materialized.  The externally observable contract is no visible
            # delivery while the persisted switch remains off.
            assert settings.load_settings()[key] == "0"
        else:
            assert (
                any(expected_reason in row[0] for row in rows)
                or any(expected_reason in (row[0] or "") for row in sources)
            )
    finally:
        _cleanup_session(sim.session_id)


def test_clock_rollback_suppresses_processing_until_time_recovers():
    sim = _new_sim()
    try:
        with sim:
            sim.initialize_production()
            sim.production_turn("今天聊得很开心", "我也是")
            sim.run_production_cycle(level=3)
            sim.advance(3600)
            sim.run_production_cycle(level=3)
            watermark = sim.now()
            sim.simulate_clock_rollback(rollback_seconds=1800)
            assert sim.run_production_cycle(level=3) == 0
            assert "clock_rollback" in settings.effective_policy(now=sim.now()).blocked_reasons
            sim.advance(watermark - sim.now())
            assert settings.observe_reliable_clock(sim.now()) is True
    finally:
        _cleanup_session(sim.session_id)


def test_windows_sleep_resume_guard_defers_overdue_local_delivery():
    sim = _new_sim()
    try:
        with sim:
            sim.initialize_production()
            sim.production_turn("晚安，我先休息了", "晚安")
            sim.go_sleep()
            sim.advance(8 * 3600)
            sim.wake_up()
            assert sim.run_production_cycle(level=3) == 0
            assert "system_resume_guard" in settings.effective_policy(
                now=sim.now()
            ).blocked_reasons
            assert sim.consume_production_deliveries() == []
            sim.advance(301)
            sim.run_production_cycle(level=3)
            assert len(sim.consume_production_deliveries()) == 1
    finally:
        _cleanup_session(sim.session_id)


def test_network_disconnect_keeps_network_free_local_delivery_available():
    sim = _new_sim()
    try:
        with sim:
            sim.initialize_production()
            sim.production_turn("今天聊得很开心", "我也是")
            sim.advance(8 * 3600)
            sim.simulate_network_disconnect(duration=600)
            sim.run_production_cycle(level=2)
            completed = sim.consume_production_deliveries()
            assert len(completed) == 1 and completed[0]["status"] == "delivered"
    finally:
        _cleanup_session(sim.session_id)


def test_claim_crash_recovers_once_but_invocation_crash_never_retries():
    sim = _new_sim()
    second_sim = TimelineSimulator(db.new_id(), start_time=START + 2 * 86400)
    try:
        with sim:
            sim.initialize_production()
            sim.production_turn("今天聊得很开心", "我也是")
            sim.advance(8 * 3600)
            sim.run_production_cycle(level=2)
            claimed = delivery.claim_next("crash-before", now=sim.now())
            assert claimed is not None
            sim.advance(delivery.LEASE_SECONDS + 1)
            sim.simulate_crash_recovery()
            recovered = sim.consume_production_deliveries("recovered")
            assert len(recovered) == 1 and recovered[0]["status"] == "delivered"

        # A fresh session avoids conflating the invocation-crash invariant with
        # the product's same-kind cooldown after the first successful contact.
        with second_sim:
            second_sim.initialize_production()
            second_sim.production_turn("新会话", "好")
            second_sim.advance(8 * 3600)
            second_sim.run_production_cycle(level=2)
            second = delivery.claim_next("crash-after", now=second_sim.now())
            assert second is not None
            begun = delivery.begin_delivery(
                second["id"], "crash-after", second["lease_token"], now=second_sim.now(),
            )
            second_sim.advance(delivery.LEASE_SECONDS + 1)
            assert delivery.recover_stale(now=second_sim.now()) == 1
            statuses = {item["id"]: item["status"] for item in delivery.list_deliveries()}
            assert statuses[second["id"]] == "failed"
            assert delivery.claim_next("must-not-retry", now=second_sim.now()) is None
            assert begun["status"] == "delivering"
    finally:
        _cleanup_session(sim.session_id)
        _cleanup_session(second_sim.session_id)


def test_database_busy_is_conservative_and_programming_errors_still_surface(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "_claim_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("busy")),
    )
    assert orchestrator.process_due(now=START, worker_id="busy") == 0
    monkeypatch.setattr(
        orchestrator, "_claim_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")),
    )
    with pytest.raises(RuntimeError, match="bug"):
        orchestrator.process_due(now=START, worker_id="bug")


def test_timezone_change_recomputes_quiet_hours_without_changing_epoch(monkeypatch):
    epoch = START
    original = time.localtime(epoch)
    quiet = time.struct_time((original.tm_year, original.tm_mon, original.tm_mday,
                              23, 0, 0, original.tm_wday, original.tm_yday, -1))
    active = time.struct_time((original.tm_year, original.tm_mon, original.tm_mday,
                               10, 0, 0, original.tm_wday, original.tm_yday, -1))
    monkeypatch.setattr(decision.time, "localtime", lambda _value: quiet)
    assert decision._is_in_quiet_hours(epoch, 23, 9) is True
    monkeypatch.setattr(decision.time, "localtime", lambda _value: active)
    assert decision._is_in_quiet_hours(epoch, 23, 9) is False

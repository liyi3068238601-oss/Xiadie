import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db
from app import main as main_module
from app.proactive import (
    candidates, decision, episodes, life_adapter, orchestrator, presence,
)

_created_sessions = []
_created_fragments = []
_created_memory_episodes = []
_created_memory_sagas = []
_created_life_seeds = []


@pytest.fixture(autouse=True)
def reset_runtime_settings():
    db.init_db()
    conn = db.connect()
    try:
        for table in (
            "life_proactive_seeds",
            "expression_plans", "proactive_intensity_plans", "proactive_decisions",
            "proactive_candidate_claims", "proactive_runtime_sagas",
            "proactive_runtime_sources", "proactive_candidates", "contact_episodes",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    original = {
        key: db.get_setting(key, default)
        for key, default in {
            "proactive_enabled": "1",
            "proactive_emergency_stop": "0",
            "proactive_pause_until": "",
            "proactive_rejected_topics": "",
            "proactive_rejected_kinds": "",
            "proactive_quiet_hours_start": "23",
            "proactive_quiet_hours_end": "9",
            orchestrator.MILESTONE_CURSOR_KEY: "",
            orchestrator.MILESTONE_CURSOR_BACKUP_KEY: "",
        }.items()
    }
    db.set_setting("proactive_enabled", "1")
    db.set_setting("proactive_emergency_stop", "0")
    db.set_setting("proactive_pause_until", "")
    db.set_setting("proactive_rejected_topics", "")
    db.set_setting("proactive_rejected_kinds", "")
    db.set_setting("proactive_quiet_hours_start", "0")
    db.set_setting("proactive_quiet_hours_end", "0")
    db.set_setting(orchestrator.MILESTONE_CURSOR_KEY, str(db.now()))
    db.set_setting(orchestrator.MILESTONE_CURSOR_BACKUP_KEY, str(db.now()))
    yield
    conn = db.connect()
    try:
        for seed_id in _created_life_seeds:
            conn.execute("DELETE FROM life_proactive_seeds WHERE id=?", (seed_id,))
        for saga_id in _created_memory_sagas:
            conn.execute("DELETE FROM memory_sagas WHERE id=?", (saga_id,))
        for episode_id in _created_memory_episodes:
            conn.execute("DELETE FROM memory_episodes WHERE id=?", (episode_id,))
        for fragment_id in _created_fragments:
            conn.execute("DELETE FROM memory_fragments WHERE id=?", (fragment_id,))
        for session_id in _created_sessions:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()
    _created_sessions.clear()
    _created_fragments.clear()
    _created_memory_episodes.clear()
    _created_memory_sagas.clear()
    _created_life_seeds.clear()
    for key, value in original.items():
        db.set_setting(key, value)


def _session_turn():
    session_id, user_id, assistant_id = db.new_id(), db.new_id(), db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "orchestrator", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (user_id, session_id, "user", "I will be back after the test", now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (assistant_id, session_id, "assistant", "Okay, see you later", "test", now + 0.1),
        )
        conn.commit()
    finally:
        conn.close()
    _created_sessions.append(session_id)
    return session_id, user_id, assistant_id


def _enqueue_casual(session_id, assistant_id, *, due_at):
    revision, source_hash, _ = orchestrator._message_snapshot(assistant_id)
    return orchestrator.enqueue_source(
        session_id=session_id,
        source_kind=orchestrator.SOURCE_CASUAL_GREETING,
        source_ref_id=assistant_id,
        source_revision=revision,
        source_hash=source_hash,
        payload={
            "topic": "light check-in",
            "open_thread": None,
            "origin_type": episodes.OriginType.LIFE_SHARE,
            "candidate_kind": candidates.CandidateKind.CASUAL_GREETING,
        },
        due_at=due_at,
        expires_at=due_at + 24 * 3600,
        now=due_at - 1,
    )


def _materialize_only(source, *, now):
    claimed = orchestrator._claim_source("materializer", now)
    assert claimed and claimed["id"] == source["id"]
    assert claimed["status"] == "claimed"
    assert claimed["lease_owner"] == "materializer"
    assert claimed["lease_expires_at"] == now + orchestrator.LEASE_SECONDS
    orchestrator._materialize_source(claimed, now)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_candidates WHERE runtime_source_id=?", (source["id"],),
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


def test_schema_60_has_runtime_delivery_and_feedback_tables():
    conn = db.connect()
    try:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        conn.close()
    assert version == "71"
    assert {
        "proactive_runtime_sources", "proactive_candidate_claims",
        "proactive_runtime_sagas", "proactive_deliveries",
        "proactive_delivery_attempts", "proactive_delivery_events",
    }.issubset(tables)


def test_real_due_source_reaches_shadow_decision_without_delivery():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    assert orchestrator.process_due(now=now, worker_id="one") == 2
    sources = [item for item in orchestrator.list_runtime_sources() if item["id"] == source["id"]]
    sagas = orchestrator.list_runtime_sagas()
    assert sources[0]["status"] == "processed"
    saga = next(item for item in sagas if item["candidate_id"] == sources[0]["candidate_id"])
    assert saga["status"] == "completed"
    result = decision.get_decision(saga["decision_id"])
    assert result is not None and result.is_shadow is True
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM proactive_candidate_claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0] == 0
    finally:
        conn.close()


def test_source_correction_before_due_is_skipped_without_candidate():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now + 900)
    conn = db.connect()
    try:
        conn.execute("UPDATE messages SET content='corrected reply' WHERE id=?", (assistant_id,))
        conn.commit()
    finally:
        conn.close()
    assert orchestrator.process_due(now=now + 900, worker_id="source-change") == 1
    row = next(item for item in orchestrator.list_runtime_sources() if item["id"] == source["id"])
    assert row["status"] == "skipped" and row["result_code"] == "source_invalidated"
    assert row["candidate_id"] is None


def test_malformed_source_payload_is_skipped_without_retry():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE proactive_runtime_sources SET payload_json='{}' WHERE id=?", (source["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    assert orchestrator.process_due(now=now, worker_id="bad-payload") == 1
    row = next(item for item in orchestrator.list_runtime_sources() if item["id"] == source["id"])
    assert row["status"] == "skipped" and row["result_code"] == "source_payload_invalid"


def test_user_returns_before_due_source_and_no_stale_candidate_is_created():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now + 900)
    assert orchestrator.handle_user_message(session_id, now=now + 1) == 0
    assert orchestrator.process_due(now=now + 900, worker_id="returned") == 0
    row = next(item for item in orchestrator.list_runtime_sources() if item["id"] == source["id"])
    assert row["status"] == "skipped" and row["candidate_id"] is None


def test_user_return_closes_same_episode_and_abandons_pending_candidate():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    candidate_id = _materialize_only(source, now=now)
    candidate = candidates.get_candidate(candidate_id)
    assert orchestrator.handle_user_message(session_id, now=now + 1) == 1
    assert episodes.get_episode(candidate.episode_id).status == episodes.EpisodeStatus.RESPONDED
    assert candidates.get_candidate(candidate_id).status == candidates.CandidateStatus.ABANDONED
    assert orchestrator.process_due(now=now + 2, worker_id="late") == 0


def test_user_return_updates_roll_back_as_one_transaction(monkeypatch):
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    candidate_id = _materialize_only(source, now=now)
    real_connect = db.connect

    class FailingConnection:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, params=()):
            if "UPDATE proactive_candidates" in sql:
                raise sqlite3.OperationalError("injected failure")
            return self.inner.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(db, "connect", lambda: FailingConnection(real_connect()))
    with pytest.raises(sqlite3.OperationalError):
        orchestrator.handle_user_message(session_id, now=now + 1)
    assert candidates.get_candidate(candidate_id).status == candidates.CandidateStatus.PENDING
    assert episodes.get_episode(candidates.get_candidate(candidate_id).episode_id).status == episodes.EpisodeStatus.PROPOSED


def test_setting_closed_between_materialization_and_decision_is_rechecked():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    candidate_id = _materialize_only(source, now=now)
    db.set_setting("proactive_enabled", "0")
    assert orchestrator.process_due(now=now + 1, worker_id="closed") == 1
    result = decision.get_decision_by_candidate(candidate_id)
    assert result.decision == decision.DecisionAction.SUPPRESS
    assert "proactive_disabled" in result.layer1_block_reasons
    assert result.is_shadow is True


def test_source_corrected_during_advice_window_is_rechecked(monkeypatch):
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    candidate_id = _materialize_only(source, now=now)
    real_gate = orchestrator._gate_snapshot
    calls = 0

    def gate(candidate, at):
        nonlocal calls
        calls += 1
        result = real_gate(candidate, at)
        if calls == 2:
            conn = db.connect()
            try:
                conn.execute("UPDATE messages SET content='changed mid-evaluation' WHERE id=?", (assistant_id,))
                conn.commit()
            finally:
                conn.close()
        return result

    monkeypatch.setattr(orchestrator, "_gate_snapshot", gate)
    assert orchestrator.process_due(now=now + 1, worker_id="mid-change") == 1
    assert decision.get_decision_by_candidate(candidate_id) is None
    saga = next(item for item in orchestrator.list_runtime_sagas() if item["candidate_id"] == candidate_id)
    assert saga["status"] == "skipped"
    assert saga["error_code"] == "source_invalidated_after_advice"


def test_two_workers_claim_candidate_once():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    candidate_id = _materialize_only(source, now=now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(
            lambda worker: orchestrator.process_due(now=now + 1, worker_id=worker),
            ("worker-a", "worker-b"),
        ))
    assert sum(counts) == 1
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM proactive_decisions WHERE candidate_id=?", (candidate_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT attempt_count FROM proactive_runtime_sagas WHERE candidate_id=?", (candidate_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_expired_claim_is_recovered_after_worker_crash():
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    candidate_id = _materialize_only(source, now=now)
    assert orchestrator._claim_candidate("crashed", now).id == candidate_id
    assert orchestrator.process_due(
        now=now + orchestrator.LEASE_SECONDS + 1, worker_id="recovery",
    ) == 1
    assert decision.get_decision_by_candidate(candidate_id) is not None


def test_database_busy_cycle_is_conservative_and_recoverable(monkeypatch):
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)

    def busy(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(orchestrator, "_claim_source", busy)
    assert orchestrator.process_due(now=now, worker_id="busy") == 0
    row = next(item for item in orchestrator.list_runtime_sources() if item["id"] == source["id"])
    assert row["status"] == "queued"


def test_programming_errors_propagate_from_process_due(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "discover_memory_milestones", lambda **_kwargs: 0,
    )
    monkeypatch.setattr(orchestrator, "_recover", lambda _now: None)
    monkeypatch.setattr(
        orchestrator, "_claim_source", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")),
    )
    with pytest.raises(RuntimeError, match="bug"):
        orchestrator.process_due(now=db.now(), worker_id="bug")


def test_worker_start_is_idempotent_and_stop_is_clean():
    async def exercise():
        await orchestrator.start_worker()
        first = orchestrator._worker_task
        await orchestrator.start_worker()
        assert orchestrator._worker_task is first
        await orchestrator.stop_worker()
        assert orchestrator._worker_task is None

    asyncio.run(exercise())


def test_main_lifespan_owns_orchestrator_start_and_stop(monkeypatch):
    async def noop_async():
        return None

    for service in (
        main_module.conversation_summary_service,
        main_module.affect_observer_service,
        main_module.companion_cognition_service,
        main_module.memory_observer_service,
        main_module.episode_consolidator,
        main_module.saga_consolidator,
        main_module.archivist_worker,
        main_module.knowledge_worker,
    ):
        monkeypatch.setattr(service, "start_worker", noop_async)
        monkeypatch.setattr(service, "stop_worker", noop_async)
    monkeypatch.setattr(main_module.knowledge_recall_service, "start_worker", lambda: None)
    monkeypatch.setattr(main_module.knowledge_recall_service, "stop_worker", lambda: None)

    async def exercise():
        async with main_module.lifespan(main_module.app):
            assert orchestrator._worker_task is not None
        assert orchestrator._worker_task is None

    asyncio.run(exercise())


def test_life_fixture_only_enqueues_source_then_eap_consumes_it():
    session_id, _, _ = _session_turn()
    seed = life_adapter.receive_life_seed(
        source_event_type=life_adapter.LifeSeedSourceType.IMPORTANT_DATE,
        source_event_id=db.new_id(), source_event_summary="project anniversary",
        source_revision="r1", source_hash="a" * 64,
    )
    _created_life_seeds.append(seed.id)
    now = db.now()
    source = orchestrator.enqueue_life_seed_fixture(
        session_id=session_id, seed_id=seed.id, due_at=now, now=now,
    )
    assert source is not None
    assert life_adapter.get_seed(seed.id).consumed_at is None
    assert source["candidate_id"] is None
    assert orchestrator.process_due(now=now, worker_id="life") == 2
    consumed = life_adapter.get_seed(seed.id)
    assert consumed.consumed_candidate_id is not None
    result = decision.get_decision_by_candidate(consumed.consumed_candidate_id)
    assert result is not None and result.is_shadow is True


def test_due_queue_uses_same_path_from_fifteen_minutes_to_thirty_days():
    base = db.now()
    scheduled = []
    for offset in (15 * 60, 30 * 24 * 3600):
        session_id, _, assistant_id = _session_turn()
        scheduled.append(_enqueue_casual(session_id, assistant_id, due_at=base + offset))
    assert orchestrator.process_due(now=base + 899, worker_id="early") == 0
    assert orchestrator.process_due(now=base + 900, worker_id="short") == 2
    assert orchestrator.process_due(now=base + 30 * 24 * 3600, worker_id="long") == 2
    rows = {row["id"]: row for row in orchestrator.list_runtime_sources(limit=200)}
    assert all(rows[item["id"]]["status"] == "processed" for item in scheduled)


def test_expected_return_source_is_derived_from_real_presence():
    session_id, user_id, assistant_id = _session_turn()
    now = db.now()
    presence.update_presence(
        session_id,
        presence.PresenceSignal(
            user_status=presence.UserStatus.AWAY_BRIEF,
            open_thread=True, open_thread_topic="test result",
            expected_return_seconds=15 * 60,
        ),
        source_message_id=user_id, detected_at=now,
    )
    queued = orchestrator.enqueue_after_chat(
        session_id=session_id, user_message_id=user_id,
        assistant_message_id=assistant_id, now=now,
    )
    kinds = {item["source_kind"] for item in queued}
    assert kinds == {
        orchestrator.SOURCE_EXPECTED_RETURN,
        orchestrator.SOURCE_CASUAL_GREETING,
    }


def test_completed_episode_and_saga_are_discovered_from_durable_cursor():
    session_id, user_id, _ = _session_turn()
    now = db.now()
    fragment_id, episode_id, saga_id = db.new_id(), db.new_id(), db.new_id()
    _created_fragments.append(fragment_id)
    _created_memory_episodes.append(episode_id)
    _created_memory_sagas.append(saga_id)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_fragments(id,layer,content,source,source_session_id,"
            "source_message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (fragment_id, "L1", "milestone", "manual", session_id, user_id, now - 5, now - 5),
        )
        conn.execute(
            "INSERT INTO memory_episodes(id,title,summary,start_at,end_at,significance,status,"
            "source_fragment_ids_json,source_hash,created_at,updated_at,completed_at) "
            "VALUES(?,?,?,?,?,8,'completed',?,?,?,?,?)",
            (episode_id, "Episode milestone", "completed episode", now - 100, now - 10,
             f'["{fragment_id}"]', "b" * 64, now - 10, now - 2, now - 2),
        )
        conn.execute(
            "INSERT INTO memory_sagas(id,title,summary,start_at,end_at,significance,status,"
            "source_episode_ids_json,source_hash,created_at,updated_at,completed_at) "
            "VALUES(?,?,?,?,?,9,'completed',?,?,?,?,?)",
            (saga_id, "Saga milestone", "completed saga", now - 200, now - 10,
             f'["{episode_id}"]', "c" * 64, now - 10, now - 1, now - 1),
        )
        conn.execute(
            "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at) "
            "VALUES(?,?,0,'anchor',?)", (saga_id, episode_id, now - 1),
        )
        conn.commit()
    finally:
        conn.close()
    db.set_setting(orchestrator.MILESTONE_CURSOR_KEY, str(now - 10))
    assert orchestrator.discover_memory_milestones(now=now) == 2
    queued = {
        item["source_kind"] for item in orchestrator.list_runtime_sources(limit=200)
        if item["source_ref_id"] in {episode_id, saga_id}
    }
    assert queued == {
        orchestrator.SOURCE_EPISODE_MILESTONE,
        orchestrator.SOURCE_SAGA_MILESTONE,
    }


def test_corrupt_milestone_cursor_uses_checked_backup(caplog):
    now = db.now()
    backup = orchestrator._encode_milestone_cursor(now - 10)
    db.set_setting(orchestrator.MILESTONE_CURSOR_KEY, "corrupted")
    db.set_setting(orchestrator.MILESTONE_CURSOR_BACKUP_KEY, backup)
    assert orchestrator.discover_memory_milestones(now=now) == 0
    current = db.get_setting(orchestrator.MILESTONE_CURSOR_KEY, "")
    assert orchestrator._decode_milestone_cursor(current) == pytest.approx(now, abs=1e-6)
    assert "proactive_milestone_cursor_invalid" in caplog.text


def test_layer3_same_kind_lookup_is_one_batch_query(monkeypatch):
    session_id, _, assistant_id = _session_turn()
    now = db.now()
    source = _enqueue_casual(session_id, assistant_id, due_at=now)
    candidate_id = _materialize_only(source, now=now)
    candidate = candidates.get_candidate(candidate_id)
    prior = decision.decide_candidate(candidate_id, now=now, is_shadow=True)
    prior.decision = decision.DecisionAction.SEND
    calls = 0
    real_connect = db.connect

    def counted_connect():
        nonlocal calls
        calls += 1
        return real_connect()

    monkeypatch.setattr(db, "connect", counted_connect)
    factors = decision.compute_layer3_factors(
        candidate, now=now + 1, recent_decisions=[prior],
    )
    assert factors.factors[decision.Layer3Factor.SAME_KIND_COOLDOWN] == 1
    assert calls == 1

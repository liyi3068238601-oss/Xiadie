from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db, main
from app.proactive import candidates, delivery, episodes, intensity, orchestrator, settings


@pytest.fixture(autouse=True)
def isolated_delivery_runtime():
    db.init_db()
    conn = db.connect()
    try:
        for table in (
            "proactive_delivery_events", "proactive_delivery_attempts", "proactive_deliveries",
            "expression_plans", "proactive_intensity_plans", "proactive_decisions",
            "proactive_candidate_claims", "proactive_runtime_sagas", "proactive_runtime_sources",
            "proactive_candidates", "contact_episodes",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    for key, value in {
        "proactive_enabled": "1", "proactive_local_delivery_enabled": "0",
        "proactive_desktop_notification_enabled": "0", "proactive_emergency_stop": "0",
        "proactive_pause_until": "", "proactive_quiet_hours_start": "0",
        "proactive_quiet_hours_end": "0", "proactive_settings_revision": "0",
    }.items():
        db.set_setting(key, value)
    db.set_setting(orchestrator.MILESTONE_CURSOR_KEY, str(db.now()))
    db.set_setting(orchestrator.MILESTONE_CURSOR_BACKUP_KEY, str(db.now()))
    yield
    db.set_setting("proactive_local_delivery_enabled", "0")
    db.set_setting("proactive_desktop_notification_enabled", "0")


def _turn_and_source(now):
    session_id, user_id, assistant_id = db.new_id(), db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                     (session_id, "delivery", now, now))
        conn.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                     (user_id, session_id, "user", "稍后回来", now - 2))
        conn.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                     (assistant_id, session_id, "assistant", "好，我等你", now - 1))
        conn.commit()
    finally:
        conn.close()
    revision, source_hash, _ = orchestrator._message_snapshot(assistant_id)
    orchestrator.enqueue_source(
        session_id=session_id, source_kind=orchestrator.SOURCE_CASUAL_GREETING,
        source_ref_id=assistant_id, source_revision=revision, source_hash=source_hash,
        payload={"topic": "回来时和我说一声", "open_thread": None,
                 "origin_type": episodes.OriginType.LIFE_SHARE,
                 "candidate_kind": candidates.CandidateKind.CASUAL_GREETING},
        due_at=now, expires_at=now + 3600, now=now - 1,
    )
    return session_id


def _build_delivery(monkeypatch, level):
    now = db.now()
    session_id = _turn_and_source(now)
    settings.write_public_setting("proactive_local_delivery_enabled", "1")
    if level == 4:
        settings.write_public_setting("proactive_desktop_notification_enabled", "1")
    monkeypatch.setattr(intensity, "select_minimum_sufficient_level", lambda **_: level)
    assert orchestrator.process_due(now=now, worker_id=f"level-{level}") == 2
    rows = delivery.list_deliveries()
    assert len(rows) == 1
    return now, session_id, rows[0]


def test_default_rollout_remains_shadow_without_delivery():
    now = db.now()
    _turn_and_source(now)
    assert orchestrator.process_due(now=now, worker_id="shadow") == 2
    assert delivery.list_deliveries() == []


def test_level0_is_audited_without_visible_attempt(monkeypatch):
    _, _, row = _build_delivery(monkeypatch, 0)
    assert row["status"] == "suppressed" and row["channel"] == "silent"
    assert delivery.claim_next("electron") is None


@pytest.mark.parametrize("level", [1, 2, 4])
def test_local_channels_have_one_confirmed_attempt(monkeypatch, level):
    now, _, queued = _build_delivery(monkeypatch, level)
    assert queued["status"] == "queued" and queued["channel"] == delivery.CHANNELS[level]
    claimed = delivery.claim_next("electron", now=now + 1)
    assert claimed and claimed["attempt_count"] == 0
    begun = delivery.begin_delivery(claimed["id"], "electron", claimed["lease_token"], now=now + 2)
    assert begun["status"] == "delivering" and begun["attempt_count"] == 1
    done = delivery.acknowledge_delivery(
        begun["id"], "electron", begun["lease_token"], success=True, now=now + 3
    )
    assert done["status"] == "delivered"
    # Duplicate confirmation is idempotent and cannot create another attempt.
    assert delivery.acknowledge_delivery(
        begun["id"], "electron", begun["lease_token"], success=True, now=now + 4
    )["status"] == "delivered"
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM proactive_delivery_attempts").fetchone()[0] == 1
    finally:
        conn.close()


def test_level3_message_commit_is_atomic_and_unique(monkeypatch):
    now, session_id, queued = _build_delivery(monkeypatch, 3)
    claimed = delivery.claim_next("electron", now=now + 1)
    done = delivery.begin_delivery(claimed["id"], "electron", claimed["lease_token"], now=now + 2)
    assert done["status"] == "delivered" and done["message_id"]
    assert delivery.begin_delivery(
        claimed["id"], "electron", claimed["lease_token"], now=now + 3
    )["status"] == "delivered"
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT role,content FROM messages WHERE session_id=? AND proactive_delivery_id=?",
            (session_id, queued["id"]),
        ).fetchall()
        assert len(row) == 1 and row[0]["role"] == "assistant"
        assert conn.execute("SELECT COUNT(*) FROM proactive_delivery_attempts").fetchone()[0] == 1
    finally:
        conn.close()


def test_settings_revision_change_cancels_at_final_gate(monkeypatch):
    now, _, _ = _build_delivery(monkeypatch, 2)
    claimed = delivery.claim_next("electron", now=now + 1)
    settings.write_public_setting("proactive_enabled", "0")
    result = delivery.begin_delivery(
        claimed["id"], "electron", claimed["lease_token"], now=now + 2
    )
    assert result["status"] == "cancelled"
    assert result["error_code"] == "authorization_changed"
    assert result["attempt_count"] == 0


def test_source_change_cancels_at_final_gate(monkeypatch):
    now, _, _ = _build_delivery(monkeypatch, 2)
    claimed = delivery.claim_next("electron", now=now + 1)
    conn = db.connect()
    try:
        source_message_id = conn.execute(
            "SELECT source_ref_id FROM proactive_runtime_sources LIMIT 1"
        ).fetchone()[0]
        conn.execute("UPDATE messages SET content='来源已修正' WHERE id=?", (source_message_id,))
        conn.commit()
    finally:
        conn.close()
    result = delivery.begin_delivery(
        claimed["id"], "electron", claimed["lease_token"], now=now + 2
    )
    assert result["status"] == "cancelled" and result["error_code"] == "source_invalidated"


def test_two_consumers_cannot_claim_the_same_delivery(monkeypatch):
    now, _, _ = _build_delivery(monkeypatch, 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: delivery.claim_next(name, now=now + 1), ("a", "b")))
    assert sum(item is not None for item in results) == 1


def test_level3_database_failure_rolls_back_before_invocation(monkeypatch):
    now, _, _ = _build_delivery(monkeypatch, 3)
    claimed = delivery.claim_next("electron", now=now + 1)
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TRIGGER fail_proactive_message BEFORE INSERT ON messages "
            "WHEN NEW.proactive_delivery_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(Exception, match="injected"):
        delivery.begin_delivery(
            claimed["id"], "electron", claimed["lease_token"], now=now + 2
        )
    conn = db.connect()
    try:
        row = conn.execute("SELECT status,attempt_count FROM proactive_deliveries").fetchone()
        assert dict(row) == {"status": "claimed", "attempt_count": 0}
        assert conn.execute("SELECT COUNT(*) FROM proactive_delivery_attempts").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_proactive_message")
        conn.commit()
    finally:
        conn.close()


def test_api_claim_begin_and_failed_ack_are_terminal(monkeypatch):
    now, _, _ = _build_delivery(monkeypatch, 2)
    claimed = main.claim_proactive_delivery(main.ProactiveDeliveryClaimIn(consumer_id="electron"))[
        "delivery"
    ]
    begun = main.begin_proactive_delivery(
        claimed["id"], main.ProactiveDeliveryBeginIn(
            consumer_id="electron", lease_token=claimed["lease_token"]
        ),
    )
    failed = main.acknowledge_proactive_delivery(
        begun["id"], main.ProactiveDeliveryAckIn(
            consumer_id="electron", lease_token=begun["lease_token"],
            success=False, error_code="render_failed",
        ),
    )
    assert failed["status"] == "failed" and failed["attempt_count"] == 1
    assert delivery.claim_next("retry", now=now + 60) is None


def test_claim_crash_retries_but_invocation_crash_never_retries(monkeypatch):
    now, _, _ = _build_delivery(monkeypatch, 1)
    first = delivery.claim_next("first", now=now + 1)
    assert delivery.recover_stale(now=first["lease_expires_at"] + 1) == 1
    second = delivery.claim_next("second", now=first["lease_expires_at"] + 2)
    begun = delivery.begin_delivery(
        second["id"], "second", second["lease_token"], now=first["lease_expires_at"] + 3
    )
    assert delivery.recover_stale(now=begun["lease_expires_at"] + 1) == 1
    assert delivery.list_deliveries()[0]["status"] == "failed"
    assert delivery.claim_next("third", now=begun["lease_expires_at"] + 2) is None
    conn = db.connect()
    try:
        attempt = conn.execute("SELECT * FROM proactive_delivery_attempts").fetchone()
        assert attempt["status"] == "uncertain" and attempt["attempt_no"] == 1
    finally:
        conn.close()


def test_level5_cannot_enter_delivery_ledger():
    assert 5 not in delivery.CHANNELS
    conn = db.connect()
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='proactive_deliveries'"
        ).fetchone()[0]
        assert "level BETWEEN 0 AND 4" in sql and "external" not in sql
    finally:
        conn.close()

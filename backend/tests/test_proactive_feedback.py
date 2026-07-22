from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.proactive import candidates, delivery, episodes, feedback, intensity, orchestrator, settings

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def isolated_feedback_runtime():
    db.init_db()
    conn = db.connect()
    try:
        for table in (
            "proactive_feedback_events", "proactive_preference_weights", "proactive_feedback",
            "proactive_delivery_events", "proactive_delivery_attempts", "proactive_deliveries",
            "expression_plans", "proactive_intensity_plans", "proactive_decisions",
            "proactive_candidate_claims", "proactive_runtime_sagas", "proactive_runtime_sources",
            "proactive_candidates", "contact_episodes", "messages", "sessions",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    for key, value in {
        "proactive_enabled": "1", "proactive_local_delivery_enabled": "1",
        "proactive_desktop_notification_enabled": "0", "proactive_emergency_stop": "0",
        "proactive_pause_until": "", "proactive_quiet_hours_start": "0",
        "proactive_quiet_hours_end": "0", "proactive_settings_revision": "0",
    }.items():
        db.set_setting(key, value)


def _delivered(monkeypatch, *, level=3):
    now = db.now()
    session_id, user_id, assistant_id = db.new_id(), db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                     (session_id, "feedback", now, now))
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
    monkeypatch.setattr(intensity, "select_minimum_sufficient_level", lambda **_: level)
    assert orchestrator.process_due(now=now, worker_id="feedback") == 2
    queued = delivery.list_deliveries()[0]
    claimed = delivery.claim_next("electron", now=now + 1)
    if level == 3:
        done = delivery.begin_delivery(claimed["id"], "electron", claimed["lease_token"], now=now + 2)
    else:
        begun = delivery.begin_delivery(claimed["id"], "electron", claimed["lease_token"], now=now + 2)
        done = delivery.acknowledge_delivery(
            begun["id"], "electron", begun["lease_token"], success=True, now=now + 3,
        )
    assert done["status"] == "delivered"
    return session_id, queued["id"], done["episode_id"]


@pytest.mark.parametrize("kind", sorted(feedback.FEEDBACK_KINDS))
def test_explicit_feedback_is_grounded_applied_and_audited(monkeypatch, kind):
    _, delivery_id, _ = _delivered(monkeypatch)
    item = feedback.create_feedback(delivery_id, kind, request_nonce=kind)
    assert item["delivery_id"] == delivery_id
    assert item["status"] == "applied"
    assert item["policy_effect"]["settings_revision"] == 1
    conn = db.connect()
    try:
        event = conn.execute(
            "SELECT * FROM proactive_feedback_events WHERE feedback_id=?", (item["id"],)
        ).fetchone()
        assert event["to_status"] == "applied"
    finally:
        conn.close()


def test_feedback_idempotency_and_concurrency(monkeypatch):
    _, delivery_id, _ = _delivered(monkeypatch)
    def submit():
        return feedback.create_feedback(delivery_id, "too_frequent", request_nonce="same")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: submit(), range(4)))
    assert len({row["id"] for row in rows}) == 1
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM proactive_feedback").fetchone()[0] == 1
    finally:
        conn.close()


def test_allow_more_reduces_episode_pressure_without_relationship_changes(monkeypatch):
    _, delivery_id, episode_id = _delivered(monkeypatch)
    conn = db.connect()
    try:
        before_pressure = conn.execute(
            "SELECT unanswered_pressure FROM contact_episodes WHERE id=?", (episode_id,)
        ).fetchone()[0]
        row = conn.execute("SELECT bond,trust,interaction_count FROM relationship_state WHERE id=1").fetchone()
        before_relationship = tuple(row) if row else None
    finally:
        conn.close()
    feedback.create_feedback(delivery_id, "allow_more", request_nonce="more")
    conn = db.connect()
    try:
        after_pressure = conn.execute(
            "SELECT unanswered_pressure FROM contact_episodes WHERE id=?", (episode_id,)
        ).fetchone()[0]
        row = conn.execute("SELECT bond,trust,interaction_count FROM relationship_state WHERE id=1").fetchone()
        after_relationship = tuple(row) if row else None
    finally:
        conn.close()
    assert after_pressure < before_pressure
    assert after_relationship == before_relationship


def test_vague_natural_feedback_waits_for_confirmation(monkeypatch):
    session_id, delivery_id, _ = _delivered(monkeypatch)
    message_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                     (message_id, session_id, "user", "我有点烦", db.now()))
        conn.commit()
    finally:
        conn.close()
    item = feedback.capture_natural_feedback(session_id, message_id, "我有点烦")
    assert item["delivery_id"] == delivery_id and item["status"] == "pending"
    assert feedback.list_pending()[0]["evidence_quote"] == "有点烦"
    resolved = feedback.resolve_feedback(item["id"], accept=True)
    assert resolved["status"] == "applied"


def test_rejected_topic_becomes_a_hard_boundary(monkeypatch):
    _, delivery_id, _ = _delivered(monkeypatch)
    feedback.create_feedback(delivery_id, "reject_topic", request_nonce="topic")
    conn = db.connect()
    try:
        candidate_id = conn.execute(
            "SELECT candidate_id FROM proactive_deliveries WHERE id=?", (delivery_id,)
        ).fetchone()[0]
        row = conn.execute("SELECT * FROM proactive_candidates WHERE id=?", (candidate_id,)).fetchone()
    finally:
        conn.close()
    boundary = __import__("app.proactive.decision", fromlist=["check_layer1_hard_boundary"])
    assert "topic_rejected" in boundary.check_layer1_hard_boundary(
        candidates._row_to_candidate(row)
    ).reasons


def test_history_and_diagnostics_do_not_expose_payload_scores_or_hashes(monkeypatch):
    _delivered(monkeypatch)
    history_json = json.dumps(feedback.list_history(), ensure_ascii=False)
    diagnostic_json = json.dumps(feedback.diagnostics(), ensure_ascii=False)
    for forbidden in ("payload", "source_hash", "authorization_hash", "shadow_score", "contact_cost"):
        assert forbidden not in history_json
        assert forbidden not in diagnostic_json
    assert "reason_codes" in diagnostic_json
    assert "confidence" not in history_json
    assert "policy_effect" not in history_json


def test_atomic_reset_and_selective_clear_preserve_user_data(monkeypatch):
    session_id, delivery_id, _ = _delivered(monkeypatch)
    feedback.create_feedback(delivery_id, "too_frequent", request_nonce="clear")
    _, revision = settings.reset_public_settings()
    assert revision == 2
    assert settings.load_settings()["proactive_local_delivery_enabled"] == "0"
    result = feedback.clear_pending_and_history()
    assert result["chat_preserved"] and result["memory_preserved"] and result["life_preserved"]
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM proactive_candidates").fetchone()[0] == 0
    finally:
        conn.close()


def test_feedback_history_diagnostics_and_reset_api_contracts(monkeypatch):
    _, delivery_id, _ = _delivered(monkeypatch)
    created = client.post(
        f"/api/proactive/deliveries/{delivery_id}/feedback",
        json={"feedback_kind": "wrong_timing", "request_nonce": "api"},
    )
    assert created.status_code == 200 and created.json()["status"] == "applied"
    history = client.get("/api/proactive/history")
    assert history.status_code == 200 and history.json()[0]["feedback"][0]["feedback_kind"] == "wrong_timing"
    diagnostics = client.get("/api/proactive/diagnostics")
    assert diagnostics.status_code == 200 and "decisions" in diagnostics.json()
    reset = client.post("/api/proactive/settings/reset")
    assert reset.status_code == 200 and reset.json()["settings"]["proactive_local_delivery_enabled"] == "0"

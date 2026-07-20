"""CTX.6 independent controls, derived-data lifecycle and body-free diagnostics."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import context_controls, context_diagnostics, conversation_summaries, db
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


@pytest.fixture(autouse=True)
def clean_context_controls():
    db.init_db()
    db.set_setting("memory_enabled", db.DEFAULT_MEMORY_ENABLED)
    db.set_setting("conversation_history_recall_mode", "explicit_only")
    db.set_setting("conversation_summary_injection_enabled", "1")
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM context_package_events")
        conn.commit()
    finally:
        conn.close()
    yield


def _session() -> tuple[str, str]:
    conn = db.connect()
    try:
        sid, uid, aid = db.new_id(), db.new_id(), db.new_id()
        now = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (sid, "CTX.6", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (uid, sid, "user", "PRIVATE-RAW-BODY", now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
            (aid, sid, "assistant", "PRIVATE-ASSISTANT-BODY", "xiadie-mock", now + 1),
        )
        conn.commit()
        return sid, uid
    finally:
        conn.close()


def test_history_summary_and_long_term_memory_controls_are_independent():
    db.set_setting("memory_enabled", "0")
    result = context_controls.update(
        reference_chat_history=True, summary_injection_enabled=False,
    )
    assert result["reference_chat_history"] is True
    assert result["summary_injection_enabled"] is False
    assert result["memory_enabled"] is False
    assert db.get_setting("conversation_history_recall_mode") == "explicit_only"

    result = context_controls.update(reference_chat_history=False)
    assert result["reference_chat_history"] is False
    assert result["summary_injection_enabled"] is False
    assert db.get_setting("memory_enabled") == "0"


def test_context_diagnostics_persist_counts_but_never_bodies():
    sid, uid = _session()
    context_diagnostics.record(
        session_id=sid,
        user_message_id=uid,
        meta={
            "package_protocol_version": "context-package-v1",
            "protocol_version": "context-budget-v1",
            "context_window_tokens": 8192,
            "output_reserve_tokens": 1024,
            "trimmed_messages": 4,
            "trimmed_rounds": 2,
            "summary_revision": 3,
            "source_type_counts": {"current_session": 2, "rolling_summary": 1},
            "component_tokens": {"rolling_summary": 120},
            "body": "PRIVATE-RAW-BODY",
        },
    )
    events = context_diagnostics.list_events(session_id=sid)
    assert events[0]["trim_reason"] == "budget"
    assert events[0]["summary_revision"] == 3
    assert events[0]["source_type_counts"]["current_session"] == 2
    assert "PRIVATE" not in json.dumps(events, ensure_ascii=False)


def test_delete_derived_summary_preserves_raw_messages_and_refreshes_fts():
    sid, _uid = _session()
    run = conversation_summaries.enqueue(sid)
    assert run["status"] == "queued"

    response = client.delete(f"/api/sessions/{sid}/conversation-summary-derived")
    assert response.status_code == 200
    assert response.json()["raw_messages_preserved"] == 2
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT summary_text FROM conversation_history_sessions_fts WHERE session_id=?", (sid,),
        ).fetchone()[0] == ""
    finally:
        conn.close()


def test_generic_setting_endpoint_cannot_bypass_context_contract():
    response = client.put(
        "/api/settings/conversation_history_recall_mode", json={"value": "on"},
    )
    assert response.status_code == 400

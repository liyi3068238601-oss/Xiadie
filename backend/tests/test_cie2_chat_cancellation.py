"""CIE.2 active generation cancellation, phase and idempotency gates."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import chat_request_control, cie_settings, main as main_module
from app.main import app

TOKEN = "test-token-with-at-least-thirty-two-bytes"
client = TestClient(app, headers={"X-Xiadie-Token": TOKEN})


@pytest.fixture(autouse=True)
def reset_cie2_control_plane():
    cie_settings.set_enabled(True)
    chat_request_control.reset_for_tests()
    yield
    chat_request_control.reset_for_tests()
    cie_settings.set_enabled(False)


def _session() -> str:
    return client.post("/api/sessions", json={}).json()["id"]


def _request(session_id: str, *, nonce: str = "chat_nonce_00000001",
             token: str = "cancel_token_000001", regenerate: bool = False) -> dict:
    return {
        "session_id": session_id,
        "content": "请给我一个可以被安全取消的回复",
        "regenerate": regenerate,
        "chat_nonce": nonce,
        "cancel_token": token,
    }


def test_control_plane_cancels_only_before_persistence():
    state, _ = chat_request_control.begin(
        chat_nonce="chat_nonce_control_1", cancel_token="cancel_token_control_1",
        session_id="session-control",
    )
    assert state == "started"
    assert chat_request_control.cancel("cancel_token_control_1") == {
        "found": True, "accepted": True, "phase": "retrieval",
    }
    chat_request_control.phase("cancel_token_control_1", "persistence")
    assert chat_request_control.cancel("cancel_token_control_1") == {
        "found": True, "accepted": False, "phase": "persistence",
    }


def test_reused_active_cancel_token_is_rejected_before_message_persistence():
    first_session = _session()
    second_session = _session()
    state, _ = chat_request_control.begin(
        chat_nonce="chat_nonce_owner_001", cancel_token="cancel_token_shared_1",
        session_id=first_session,
    )
    assert state == "started"
    response = client.post("/api/chat", json=_request(
        second_session, nonce="chat_nonce_other_001", token="cancel_token_shared_1",
    ))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "chat_nonce_conflict"
    assert client.get(f"/api/sessions/{second_session}/messages").json() == []


def test_cancelled_generation_discards_partial_reply_but_keeps_user_message(monkeypatch):
    session_id = _session()
    original_begin = chat_request_control.begin

    def begin_cancelled(**kwargs):
        result = original_begin(**kwargs)
        chat_request_control.cancel(kwargs["cancel_token"])
        return result

    monkeypatch.setattr(chat_request_control, "begin", begin_cancelled)
    with client.stream("POST", "/api/chat", json=_request(session_id)) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: cancelled" in body and '"persisted": false' in body
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [item["role"] for item in messages] == ["user"]


def test_completed_nonce_replays_without_duplicate_persistence():
    session_id = _session()
    payload = _request(session_id)
    bodies = []
    for _ in range(2):
        with client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200
            bodies.append("".join(response.iter_text()))
    assert "event: phase" in bodies[0] and '"phase": "persistence"' in bodies[0]
    assert '"replayed": true' in bodies[1]
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [item["role"] for item in messages] == ["user", "assistant"]


def test_post_commit_side_effect_failure_still_replays_the_persisted_reply(monkeypatch):
    session_id = _session()
    payload = _request(
        session_id, nonce="chat_nonce_post_commit", token="cancel_token_post_commit",
    )
    monkeypatch.setattr(
        main_module.companion_state,
        "commit_interaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("post-commit failure")),
    )
    tolerant = TestClient(
        app, headers={"X-Xiadie-Token": TOKEN}, raise_server_exceptions=False,
    )
    with tolerant.stream("POST", "/api/chat", json=payload) as response:
        "".join(response.iter_text())
    with tolerant.stream("POST", "/api/chat", json=payload) as response:
        replay = "".join(response.iter_text())
    assert response.status_code == 200 and '"replayed": true' in replay
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [item["role"] for item in messages] == ["user", "assistant"]


def test_cancelled_regeneration_preserves_the_previous_reply(monkeypatch):
    session_id = _session()
    with client.stream("POST", "/api/chat", json={
        "session_id": session_id, "content": "先生成一个需要保留的回复",
    }) as response:
        "".join(response.iter_text())
    before = client.get(f"/api/sessions/{session_id}/messages").json()
    original_begin = chat_request_control.begin

    def begin_cancelled(**kwargs):
        result = original_begin(**kwargs)
        chat_request_control.cancel(kwargs["cancel_token"])
        return result

    monkeypatch.setattr(chat_request_control, "begin", begin_cancelled)
    with client.stream("POST", "/api/chat", json=_request(
        session_id, nonce="chat_nonce_regen_001", token="cancel_token_regen_1", regenerate=True,
    )) as response:
        body = "".join(response.iter_text())
    after = client.get(f"/api/sessions/{session_id}/messages").json()
    assert "event: cancelled" in body
    assert [(item["id"], item["content"]) for item in after] == [
        (item["id"], item["content"]) for item in before
    ]


def test_cancel_endpoint_is_feature_gated_and_nonce_pair_is_atomic():
    session_id = _session()
    response = client.post("/api/chat", json={
        "session_id": session_id,
        "content": "缺少取消 token",
        "chat_nonce": "chat_nonce_missing_1",
    })
    assert response.status_code == 422
    assert client.get(f"/api/sessions/{session_id}/messages").json() == []
    cie_settings.set_enabled(False)
    response = client.post("/api/chat", json=_request(
        session_id, nonce="chat_nonce_disabled_1", token="cancel_token_disabled_1",
    ))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "cie_disabled"
    assert client.get(f"/api/sessions/{session_id}/messages").json() == []
    response = client.post("/api/chat/cancel", json={"cancel_token": "cancel_token_disabled"})
    assert response.status_code == 409

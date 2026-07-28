"""CIE.1 bounded turn ingress, persistence, attachment and isolation gates."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import cie_settings, db, main as main_module, turn_ingress
from app.main import app

TOKEN = "test-token-with-at-least-thirty-two-bytes"
client = TestClient(app, headers={"X-Xiadie-Token": TOKEN})


@pytest.fixture(autouse=True)
def disable_cie_after_test():
    cie_settings.set_enabled(False)
    yield
    cie_settings.set_enabled(False)


def _entry(index: int, *, window: str = "window_123", content: str | None = None,
           attachments: list[str] | None = None, boundary: str = "idle_timeout") -> dict:
    return {
        "client_message_id": f"client_message_{index:02d}",
        "window_id": window,
        "content": content if content is not None else f"第 {index} 条合成消息",
        "attachment_ids": attachments or [],
        "authorization_scope": "local_text_only",
        "queued_at_ms": 1_000 + index,
        "boundary": boundary,
    }


def _sse_event(body: str, event: str) -> dict:
    for block in body.split("\n\n"):
        if f"event: {event}" in block:
            data = next(line for line in block.splitlines() if line.startswith("data:"))
            return json.loads(data.removeprefix("data:").strip())
    raise AssertionError(f"missing SSE event {event}")


def _session() -> str:
    return client.post("/api/sessions", json={}).json()["id"]


def test_turn_ingress_protocol_clamps_window_and_rejects_cross_window_or_duplicates():
    assert turn_ingress.normalize_window_ms(1) == 300
    assert turn_ingress.normalize_window_ms(500) == 500
    assert turn_ingress.normalize_window_ms(9_999) == 800
    one = turn_ingress.TurnIngressMessage.model_validate(_entry(1))
    two = turn_ingress.TurnIngressMessage.model_validate(_entry(2, window="window_456"))
    with pytest.raises(ValueError, match="cross windows"):
        turn_ingress.build_envelope("session", [one, two])
    with pytest.raises(ValueError, match="client_message_id"):
        turn_ingress.build_envelope("session", [one, one])
    with pytest.raises(ValidationError):
        turn_ingress.TurnIngressMessage.model_validate(_entry(3, content="", attachments=[]))


def test_cie_disabled_rejects_batch_without_persisting_messages():
    session_id = _session()
    response = client.post("/api/chat", json={
        "session_id": session_id,
        "content": "第 1 条合成消息",
        "ingress_messages": [_entry(1)],
    })
    assert response.status_code == 409
    assert client.get(f"/api/sessions/{session_id}/messages").json() == []


def test_cie_disabled_preserves_the_legacy_single_message_fallback():
    session_id = _session()
    with client.stream("POST", "/api/chat", json={
        "session_id": session_id, "content": "旧路径保持可用",
    }) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200 and "event: done" in body
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [(item["role"], item["content"]) for item in messages[:1]] == [
        ("user", "旧路径保持可用"),
    ]


def test_enabled_batch_persists_originals_in_order_and_emits_ephemeral_envelope_meta():
    cie_settings.set_enabled(True)
    session_id = _session()
    entries = [_entry(1), _entry(2), _entry(3, boundary="explicit_send")]
    content = "\n\n".join(item["content"] for item in entries)
    with client.stream("POST", "/api/chat", json={
        "session_id": session_id, "content": content, "ingress_messages": entries,
    }) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200 and "event: done" in body
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [(item["role"], item["content"]) for item in messages[:3]] == [
        ("user", item["content"]) for item in entries
    ]
    assert messages[3]["role"] == "assistant"
    meta = _sse_event(body, "meta")["turn_ingress"]
    assert meta["protocol_version"] == turn_ingress.ENVELOPE_VERSION
    assert meta["message_count"] == 3 and meta["seal_reason"] == "explicit_send"
    assert meta["message_ids"] == [item["id"] for item in messages[:3]]


def test_server_rebuilds_envelope_and_rejects_client_content_mismatch_atomically():
    cie_settings.set_enabled(True)
    session_id = _session()
    response = client.post("/api/chat", json={
        "session_id": session_id,
        "content": "被篡改的合并正文",
        "ingress_messages": [_entry(1), _entry(2)],
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "turn_envelope_mismatch"
    assert client.get(f"/api/sessions/{session_id}/messages").json() == []


def test_attachments_remain_bound_to_their_original_messages():
    cie_settings.set_enabled(True)
    session_id = _session()
    first = client.post(
        "/api/chat/attachments", content=b"first synthetic attachment",
        headers={"Content-Type": "text/plain", "X-Xiadie-Filename": "first.txt"},
    ).json()
    second = client.post(
        "/api/chat/attachments", content=b"second synthetic attachment",
        headers={"Content-Type": "text/plain", "X-Xiadie-Filename": "second.txt"},
    ).json()
    entries = [
        _entry(1, attachments=[first["id"]]),
        _entry(2, attachments=[second["id"]]),
    ]
    content = "\n\n".join(item["content"] for item in entries)
    with client.stream("POST", "/api/chat", json={
        "session_id": session_id, "content": content, "ingress_messages": entries,
    }) as response:
        assert "event: done" in "".join(response.iter_text())
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [item["attachments"][0]["id"] for item in messages[:2]] == [first["id"], second["id"]]


def test_attachment_only_tail_keeps_state_evidence_on_last_text_message(monkeypatch):
    cie_settings.set_enabled(True)
    session_id = _session()
    attachment = client.post(
        "/api/chat/attachments", content=b"attachment-only tail",
        headers={"Content-Type": "text/plain", "X-Xiadie-Filename": "tail.txt"},
    ).json()
    captured: dict[str, str] = {}

    def capture_feedback(captured_session_id, message_id, content):
        captured.update(session_id=captured_session_id, message_id=message_id, content=content)

    monkeypatch.setattr(
        main_module.proactive_feedback, "capture_natural_feedback", capture_feedback,
    )
    entries = [
        _entry(1, content="晚安，明天见"),
        _entry(2, content="", attachments=[attachment["id"]], boundary="explicit_send"),
    ]
    with client.stream("POST", "/api/chat", json={
        "session_id": session_id,
        "content": "晚安，明天见",
        "ingress_messages": entries,
    }) as response:
        assert "event: done" in "".join(response.iter_text())
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert captured == {
        "session_id": session_id,
        "message_id": messages[0]["id"],
        "content": "晚安，明天见",
    }
    assert messages[1]["attachments"][0]["id"] == attachment["id"]


def test_same_window_identifier_remains_isolated_by_session_request():
    cie_settings.set_enabled(True)
    sessions = [_session(), _session()]
    for index, session_id in enumerate(sessions, start=1):
        entry = _entry(index, window="shared_window")
        with client.stream("POST", "/api/chat", json={
            "session_id": session_id, "content": entry["content"], "ingress_messages": [entry],
        }) as response:
            assert response.status_code == 200
            "".join(response.iter_text())
    for index, session_id in enumerate(sessions, start=1):
        messages = client.get(f"/api/sessions/{session_id}/messages").json()
        assert messages[0]["content"] == _entry(index)["content"]

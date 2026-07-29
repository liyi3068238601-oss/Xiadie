from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cie_settings, db, image_attachments, llm, vision_capabilities
from app import main as main_module
from app.main import app


TOKEN = "test-token-with-at-least-thirty-two-bytes"
CLIENT = TestClient(app, headers={"X-Xiadie-Token": TOKEN})
RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def restore_cie_image_state():
    previous = cie_settings.is_enabled()
    yield
    cie_settings.set_enabled(previous)
    conn = db.connect()
    try:
        conn.execute(
            "DELETE FROM model_capability_evidence"
            " WHERE (provider_id='mock' AND model='xiadie-mock')"
            " OR (provider_id='deepseek' AND model='cie3-test-vision')",
        )
        conn.execute("DELETE FROM message_attachments WHERE id='expiredcie3image'")
        conn.commit()
    finally:
        conn.close()


def _provider(location: str = "remote") -> dict:
    return {
        "id": "mock",
        "name": "CIE image test",
        "base_url": "https://example.invalid/v1",
        "api_key": "",
        "execution_location": location,
        "location_revision": 1,
    }


def _record_supported(location: str = "remote") -> None:
    conn = db.connect()
    try:
        conn.execute(
            "DELETE FROM model_capability_evidence"
            " WHERE provider_id='mock' AND model='xiadie-mock' AND capability='vision'",
        )
        conn.execute(
            "INSERT INTO model_capability_evidence(provider_id,model,capability,status,"
            "provider_location,provider_location_revision,probe_protocol_version,"
            "evidence_sha256,error_code,checked_at) VALUES(?,?,'vision','supported',?,?,?,?,?,?)",
            ("mock", "xiadie-mock", location, 1, "vision-probe-v1", "a" * 64, None, db.now()),
        )
        conn.commit()
    finally:
        conn.close()


def test_image_parser_rejects_mime_mismatch_and_limits():
    metadata = image_attachments.inspect_image(RED_PNG, "image/png")
    assert metadata["pixel_width"] == metadata["pixel_height"] == 1
    assert metadata["content_sha256"]
    with pytest.raises(image_attachments.ImageAttachmentError) as mismatch:
        image_attachments.inspect_image(RED_PNG, "image/jpeg")
    assert mismatch.value.code == "image_mime_mismatch"
    with pytest.raises(image_attachments.ImageAttachmentError) as too_large:
        image_attachments.inspect_image(b"x" * (image_attachments.MAX_IMAGE_BYTES + 1), "image/png")
    assert too_large.value.code == "image_size_invalid"
    with pytest.raises(image_attachments.ImageAttachmentError) as traversal:
        image_attachments.load_data_url("../outside.bin", "image/png")
    assert traversal.value.code == "image_storage_path_invalid"


def test_real_protocol_probe_persists_supported_evidence(monkeypatch):
    class Response:
        status_code = 200
        content = b'{"choices":[{"message":{"content":"RED"}}]}'

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "RED"}}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr(vision_capabilities.httpx, "AsyncClient", Client)
    provider = _provider("remote") | {
        "id": "deepseek", "base_url": "https://example.invalid/v1", "api_key": "secret",
    }
    import asyncio
    result = asyncio.run(vision_capabilities.probe(provider, "cie3-test-vision"))
    assert result["status"] == "supported"
    assert len(result["evidence_sha256"]) == 64
    assert vision_capabilities.status(provider, "cie3-test-vision")["status"] == "supported"


def test_unverified_model_cannot_upload_image(monkeypatch):
    cie_settings.set_enabled(True)
    provider = _provider()
    monkeypatch.setattr(main_module, "_current_model", lambda: (provider, "xiadie-mock"))
    conn = db.connect()
    try:
        conn.execute("DELETE FROM model_capability_evidence WHERE provider_id='mock'")
        conn.commit()
    finally:
        conn.close()
    response = CLIENT.post(
        "/api/chat/attachments",
        content=RED_PNG,
        headers={"Content-Type": "image/png", "X-Xiadie-Filename": "red.png"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "vision_capability_unavailable"


def test_image_upload_sweeps_expired_runtime_files(monkeypatch):
    cie_settings.set_enabled(True)
    provider = _provider("remote")
    monkeypatch.setattr(main_module, "_current_model", lambda: (provider, "xiadie-mock"))
    _record_supported("remote")
    expired_id = "expiredcie3image"
    storage_name = image_attachments.save(expired_id, RED_PNG)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO message_attachments(id,message_id,filename,mime_type,content_text,"
            "content_sha256,char_count,created_at,attachment_kind,storage_path,byte_count,"
            "pixel_width,pixel_height,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                expired_id, None, "expired.png", "image/png", "", "b" * 64, 0,
                db.now() - 100, "image", storage_name, len(RED_PNG), 1, 1, db.now() - 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    response = CLIENT.post(
        "/api/chat/attachments",
        content=RED_PNG,
        headers={"Content-Type": "image/png", "X-Xiadie-Filename": "new.png"},
    )
    assert response.status_code == 200
    assert not (image_attachments.storage_dir() / storage_name).exists()
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT storage_path FROM message_attachments WHERE id=?", (expired_id,),
        ).fetchone()["storage_path"] is None
    finally:
        conn.close()
    assert CLIENT.delete(f"/api/chat/attachments/{response.json()['id']}").status_code == 200


def test_remote_image_requires_once_consent_then_destroys_raw_bytes(monkeypatch):
    cie_settings.set_enabled(True)
    provider = _provider("remote")
    monkeypatch.setattr(main_module, "_current_model", lambda: (provider, "xiadie-mock"))
    _record_supported("remote")
    upload = CLIENT.post(
        "/api/chat/attachments",
        content=RED_PNG,
        headers={"Content-Type": "image/png", "X-Xiadie-Filename": "red.png"},
    )
    assert upload.status_code == 200
    attachment = upload.json()
    assert attachment["attachment_kind"] == "image"
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT storage_path FROM message_attachments WHERE id=?", (attachment["id"],),
        ).fetchone()
        raw_path = image_attachments.storage_dir() / row["storage_path"]
    finally:
        conn.close()
    assert raw_path.exists()

    session = CLIENT.post("/api/sessions", json={"temporary": True}).json()
    request = {
        "session_id": session["id"],
        "content": "",
        "attachment_ids": [attachment["id"]],
        "image_provider_id": "mock",
        "image_model": "xiadie-mock",
        "image_location_revision": 1,
    }
    wrong_scope = CLIENT.post("/api/chat", json=request | {
        "image_transmission_consent": True,
        "ingress_messages": [{
            "client_message_id": "message_image_scope_001",
            "window_id": "window_image_001",
            "content": "",
            "attachment_ids": [attachment["id"]],
            "authorization_scope": "local_image",
            "queued_at_ms": 1,
            "boundary": "explicit_send",
        }],
    })
    assert wrong_scope.status_code == 409
    assert wrong_scope.json()["detail"]["code"] == "turn_authorization_scope_mismatch"
    refused = CLIENT.post("/api/chat", json=request)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "image_transmission_consent_required"
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE session_id=?", (session["id"],),
        ).fetchone()["c"] == 0
    finally:
        conn.close()
    assert raw_path.exists()

    captured: list[dict] = []

    async def fake_stream(_provider, _model, messages, **_kwargs):
        captured.extend(messages)
        yield "我看到了红色图片"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    with CLIENT.stream(
        "POST", "/api/chat", json=request | {"image_transmission_consent": True},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: done" in body
    image_message = next(item for item in reversed(captured) if item["role"] == "user")
    assert isinstance(image_message["content"], list)
    assert image_message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    conn = db.connect()
    try:
        stored = conn.execute(
            "SELECT storage_path,content_text FROM message_attachments WHERE id=?", (attachment["id"],),
        ).fetchone()
        assert stored["storage_path"] is None
        assert stored["content_text"] == ""
    finally:
        conn.close()
    assert not Path(raw_path).exists()

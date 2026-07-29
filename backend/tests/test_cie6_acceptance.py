from __future__ import annotations

import base64
import json
import runpy
from pathlib import Path

from fastapi.testclient import TestClient

from app import cie_settings, db, llm, vision_capabilities
from app import main as main_module
from app.main import app


PROJECT_DIR = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_DIR / "backend" / "scripts" / "run_cie6_acceptance.py"
REPORT = PROJECT_DIR / "docs" / "reports" / "cie-6-final-acceptance.json"
CLIENT = TestClient(app, headers={
    "X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes",
})
RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


def test_cie6_report_is_reproducible_and_covers_every_required_matrix():
    generated = runpy.run_path(str(SCRIPT))["build_report"]()
    recorded = json.loads(REPORT.read_text(encoding="utf-8"))
    assert recorded == generated
    assert recorded["round_counts"] == [5, 20, 100, 500]
    assert recorded["evaluated_messages"] == 625
    assert all(recorded["matrices"].values())
    assert len(recorded["matrices"]["runtime_environment"]) == 8
    assert len(recorded["matrices"]["native_images"]) == 8
    assert len(recorded["matrices"]["context_contributions"]) == 8


def test_cie6_zero_tolerance_metrics_and_schema_freeze_are_exact():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["schema_version"] == 81
    assert report["uses_schema_82"] is False
    assert report["independent_review"] == "passed"
    assert all(value == 0 for value in report["metrics"].values())


def test_cie6_evidence_files_exist_and_no_matrix_is_claimed_without_an_owner():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    expected = {
        "continuous", "cancellation", "images", "presentation",
        "contributions", "runtime", "electron",
    }
    assert set(report["evidence"]) == expected
    assert (PROJECT_DIR / "backend" / "tests" / "test_cie3_images.py").is_file()
    assert (PROJECT_DIR / "frontend" / "tests" / "replyPresentation.test.mjs").is_file()
    assert (PROJECT_DIR / "desktop" / "tests" / "lifecycle-contract.test.mjs").is_file()
    assert (PROJECT_DIR / "scripts" / "test-cie6-electron-smoke.ps1").is_file()


def test_cie6_image_revoke_expiry_destination_change_and_local_matrix(monkeypatch):
    previous = cie_settings.is_enabled()
    cie_settings.set_enabled(True)
    provider = {
        "id": "cie6-provider", "name": "CIE6", "base_url": "https://example.invalid/v1",
        "api_key": "", "execution_location": "remote", "location_revision": 1,
    }
    monkeypatch.setattr(main_module, "_current_model", lambda: (provider, "cie6-model"))
    capability = {
        "protocol_version": "vision-probe-v1", "provider_id": "cie6-provider",
        "model": "cie6-model", "status": "supported", "provider_location": "remote",
        "provider_location_revision": 1, "checked_at": db.now(), "error_code": None,
    }
    monkeypatch.setattr(vision_capabilities, "status", lambda _p, _m: dict(capability))

    def upload():
        response = CLIENT.post(
            "/api/chat/attachments", content=RED_PNG,
            headers={"Content-Type": "image/png", "X-Xiadie-Filename": "matrix.png"},
        )
        assert response.status_code == 200
        return response.json()

    session = CLIENT.post("/api/sessions", json={"temporary": True}).json()
    request = lambda attachment: {
        "session_id": session["id"], "content": "", "attachment_ids": [attachment["id"]],
        "image_transmission_consent": True,
        "image_provider_id": attachment["vision_capability"]["provider_id"],
        "image_model": attachment["vision_capability"]["model"],
        "image_location_revision": attachment["vision_capability"]["provider_location_revision"],
    }
    try:
        revoked = upload()
        assert CLIENT.delete(f"/api/chat/attachments/{revoked['id']}").status_code == 200
        missing = CLIENT.post("/api/chat", json=request(revoked))
        assert missing.status_code == 409
        assert missing.json()["detail"]["code"] == "turn_attachment_unavailable"

        expired = upload()
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE message_attachments SET expires_at=? WHERE id=?",
                (db.now() - 1, expired["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        expired_response = CLIENT.post("/api/chat", json=request(expired))
        assert expired_response.status_code == 410
        assert expired_response.json()["detail"]["code"] == "image_attachment_expired"
        CLIENT.delete(f"/api/chat/attachments/{expired['id']}")

        changed = upload()
        capability.update(provider_location_revision=2)
        destination_changed = CLIENT.post("/api/chat", json=request(changed))
        assert destination_changed.status_code == 409
        assert destination_changed.json()["detail"]["code"] == "image_authorization_snapshot_changed"
        capability.update(model="cie6-model-v2", provider_location_revision=3)
        model_changed = CLIENT.post("/api/chat", json=request(changed))
        assert model_changed.status_code == 409
        assert model_changed.json()["detail"]["code"] == "image_authorization_snapshot_changed"
        CLIENT.delete(f"/api/chat/attachments/{changed['id']}")

        capability.update(
            model="cie6-model", provider_location="local", provider_location_revision=4,
        )
        local = upload()

        async def fake_stream(_provider, _model, _messages, **_kwargs):
            yield "本地图片路径正常"

        monkeypatch.setattr(llm, "stream_chat", fake_stream)
        local_request = request(local)
        local_request["image_transmission_consent"] = False
        response = CLIENT.post("/api/chat", json=local_request)
        assert response.status_code == 200
        assert "event: done" in response.text
    finally:
        cie_settings.set_enabled(previous)

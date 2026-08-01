from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import db, inner_state_projection, llm, persona_v2
from app.main import app

TEST_API_TOKEN = "test-token-with-at-least-thirty-two-bytes"


def _use_isolated_db(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "DB_PATH", str(data_dir / "xiadie.db"))
    db.init_db()


def test_released_v23_is_fresh_install_default_and_has_matching_certificate():
    assert persona_v2.ACTIVE_PROFILE_VERSION == "persona-profile-v2.3"
    certificate_path = (
        persona_v2.PROFILE_DIRS[persona_v2.ACTIVE_PROFILE_VERSION]
        / "certifications.json"
    )
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))[
        "certifications"
    ][0]
    assert certificate["profile_version"] == persona_v2.ACTIVE_PROFILE_VERSION
    assert certificate["evaluation_protocol"] == "persona-evaluation-v2.0"
    assert certificate["model_fingerprint"] == (
        "b2bcda1f94e8d4c89a84f7e80a99ec5bf8271246496ca10bb34fe2edde2c2040"
    )
    assert certificate["fixture_sha256"] == (
        "22ce05dee3ee425783f30346645fb160aaeff4216fefa6b49a5f019dad7d8dcd"
    )
    for mode in persona_v2.MODES:
        prompt, _, _ = persona_v2.compile_candidate(
            mode=mode, profile_version=persona_v2.ACTIVE_PROFILE_VERSION,
        )
        assert hashlib.sha256(prompt.encode()).hexdigest() == (
            certificate["compiled_hashes"][mode]
        )
    artifact = (
        Path(__file__).resolve().parents[2]
        / "docs" / "reports"
        / "life2-persona-v2.3-candidate-deepseek-v4-flash.json"
    )
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == (
        certificate["evaluation_artifact_sha256"]
    )


def test_chat_captures_profile_selector_once_before_projection_build(
    monkeypatch, tmp_path,
):
    _use_isolated_db(monkeypatch, tmp_path)
    assert persona_v2.selected_profile_version() == persona_v2.ACTIVE_PROFILE_VERSION
    captured_profiles: list[str] = []
    original_compile = persona_v2.compile_for_request

    def build_then_switch(**_kwargs):
        persona_v2.set_profile_version(persona_v2.DEFAULT_PROFILE_VERSION)
        return None

    def capture_compile(**kwargs):
        captured_profiles.append(str(kwargs.get("profile_version")))
        return original_compile(**kwargs)

    async def fake_stream(_provider, _model, _messages, **_kwargs):
        yield "好的。"

    monkeypatch.setattr(inner_state_projection, "build", build_then_switch)
    monkeypatch.setattr(persona_v2, "compile_for_request", capture_compile)
    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    client = TestClient(app, headers={"X-Xiadie-Token": TEST_API_TOKEN})
    session = client.post("/api/sessions", json={}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"], "content": "聊聊今天的安排。",
    }) as response:
        assert response.status_code == 200
        assert "event: done" in "".join(response.iter_text())

    assert captured_profiles == [persona_v2.ACTIVE_PROFILE_VERSION]
    assert persona_v2.selected_profile_version() == persona_v2.DEFAULT_PROFILE_VERSION

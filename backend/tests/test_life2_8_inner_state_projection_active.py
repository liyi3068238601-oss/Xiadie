from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, inner_state_projection as projection, llm, persona_v2
from app.main import app

TEST_API_TOKEN = "test-token-with-at-least-thirty-two-bytes"


def _use_isolated_db(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "DB_PATH", str(data_dir / "xiadie.db"))
    db.init_db()


def _client() -> TestClient:
    return TestClient(app, headers={"X-Xiadie-Token": TEST_API_TOKEN})


def test_fresh_schema_82_defaults_projection_active_without_new_database_objects(
    monkeypatch, tmp_path,
):
    _use_isolated_db(monkeypatch, tmp_path)
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "82"
        assert projection.rollout_mode(conn) == "active"
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name LIKE '%projection%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_missing_setting_defaults_active_and_invalid_value_fails_closed(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM settings WHERE key=?", (projection.ROLLOUT_KEY,))
        conn.commit()
        assert projection.rollout_mode(conn) == "active"
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?)",
            (projection.ROLLOUT_KEY, "invalid"),
        )
        conn.commit()
        assert projection.rollout_mode(conn) == "off"
    finally:
        conn.close()


def test_internal_setter_validates_is_idempotent_and_has_independent_rollback(
    monkeypatch, tmp_path,
):
    _use_isolated_db(monkeypatch, tmp_path)
    db.set_setting("life.short_memo.rollout_mode", "active")
    assert projection.set_rollout_mode("active") == "active"
    assert projection.set_rollout_mode("active") == "active"
    assert projection.set_rollout_mode("shadow") == "shadow"
    assert db.get_setting("life.short_memo.rollout_mode") == "active"
    assert projection.set_rollout_mode("off") == "off"
    assert db.get_setting("life.short_memo.rollout_mode") == "active"
    with pytest.raises(projection.ProjectionRolloutError) as error:
        projection.set_rollout_mode("invalid")
    assert error.value.code == "inner_state_projection_rollout_invalid"
    assert projection.rollout_mode() == "off"


def test_request_uses_projection_rollout_captured_before_projection_build(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    projection.set_rollout_mode("active")
    captured_modes: list[str] = []
    original_build = projection.build
    original_compile = persona_v2.compile_for_request

    def build_then_switch(**kwargs):
        value = original_build(**kwargs)
        projection.set_rollout_mode("shadow")
        return value

    def capture_compile(**kwargs):
        captured_modes.append(str(kwargs.get("projection_rollout_mode")))
        return original_compile(**kwargs)

    async def fake_stream(_provider, _model, _messages, **_kwargs):
        yield "好的。"

    monkeypatch.setattr(projection, "build", build_then_switch)
    monkeypatch.setattr(persona_v2, "compile_for_request", capture_compile)
    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    client = _client()
    session = client.post("/api/sessions", json={}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"], "content": "今天看到一件很有趣的事。",
    }) as response:
        assert response.status_code == 200
        assert "event: done" in "".join(response.iter_text())
    assert captured_modes == ["active"]
    assert projection.rollout_mode() == "shadow"


def test_projection_failure_degrades_to_plain_persona_without_blocking_chat(
    monkeypatch, tmp_path,
):
    _use_isolated_db(monkeypatch, tmp_path)
    projection.set_rollout_mode("active")
    captured_projection: list[object] = []
    original_compile = persona_v2.compile_for_request

    def fail_build(**_kwargs):
        raise RuntimeError("projection fixture failure")

    def capture_compile(**kwargs):
        captured_projection.append(kwargs.get("projection"))
        return original_compile(**kwargs)

    async def fake_stream(_provider, _model, _messages, **_kwargs):
        yield "我在听。"

    monkeypatch.setattr(projection, "build", fail_build)
    monkeypatch.setattr(persona_v2, "compile_for_request", capture_compile)
    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    client = _client()
    session = client.post("/api/sessions", json={}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"], "content": "今天有点累。",
    }) as response:
        assert response.status_code == 200
        assert "event: done" in "".join(response.iter_text())
    assert captured_projection == [None]

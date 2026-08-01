from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import db, llm, short_memo
from app.main import app

TEST_API_TOKEN = "test-token-with-at-least-thirty-two-bytes"


def _use_isolated_db(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "DB_PATH", str(data_dir / "xiadie.db"))
    db.init_db()


def _source(text: str) -> tuple[str, str]:
    session_id, message_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        now = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "LIFE2.7 rollout", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, session_id, "user", text, now),
        )
        conn.commit()
    finally:
        conn.close()
    return session_id, message_id


def test_fresh_schema_82_database_defaults_short_memo_to_active(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    snapshot = short_memo.rollout_snapshot()
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "82"
        assert snapshot.rollout_mode == "active"
        assert snapshot.rollout_epoch == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('short_memos','short_memo_events')"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_missing_setting_uses_active_but_invalid_setting_fails_closed(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM settings WHERE key='life.short_memo.rollout_mode'")
        conn.commit()
        assert short_memo.rollout_snapshot(conn).rollout_mode == "active"
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('life.short_memo.rollout_mode','invalid')"
        )
        conn.commit()
        assert short_memo.rollout_snapshot(conn).rollout_mode == "off"
    finally:
        conn.close()


def test_internal_rollout_setter_is_idempotent_and_epoch_tracks_real_changes(
    monkeypatch, tmp_path,
):
    _use_isolated_db(monkeypatch, tmp_path)
    assert short_memo.set_rollout_mode("active").rollout_epoch == 0
    shadow = short_memo.set_rollout_mode("shadow")
    assert (shadow.rollout_mode, shadow.rollout_epoch) == ("shadow", 1)
    active = short_memo.set_rollout_mode("active")
    assert (active.rollout_mode, active.rollout_epoch) == ("active", 2)
    repeated = short_memo.set_rollout_mode("active")
    assert (repeated.rollout_mode, repeated.rollout_epoch) == ("active", 2)
    off = short_memo.set_rollout_mode("off")
    assert (off.rollout_mode, off.rollout_epoch) == ("off", 3)


def test_request_snapshot_prevents_mid_turn_rollout_mixing(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    short_memo.set_rollout_mode("shadow")
    captured_shadow = short_memo.rollout_snapshot()
    text = "明天我要去图书馆还书"
    session_id, message_id = _source(text)

    current_active = short_memo.set_rollout_mode("active")
    shadow_result = short_memo.process_user_message(
        session_id=session_id,
        message_id=message_id,
        text=text,
        snapshot=captured_shadow,
    )
    assert shadow_result == {"status": "shadow_candidate"}
    assert short_memo.list_active() == []

    active_result = short_memo.process_user_message(
        session_id=session_id,
        message_id=message_id,
        text=text,
        snapshot=current_active,
    )
    assert active_result["status"] == "created"

    captured_active = current_active
    short_memo.set_rollout_mode("off")
    second_text = "后天我要去取修好的相机"
    second_session, second_message = _source(second_text)
    assert short_memo.process_user_message(
        session_id=second_session,
        message_id=second_message,
        text=second_text,
        snapshot=captured_active,
    )["status"] == "created"
    assert short_memo.process_user_message(
        session_id=second_session,
        message_id=second_message,
        text=second_text,
        snapshot=short_memo.rollout_snapshot(),
    ) == {"status": "disabled"}


def test_product_switch_does_not_change_internal_rollout(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    before = short_memo.rollout_snapshot()
    disabled = short_memo.update_product_settings(enabled=False)
    assert disabled.enabled is False
    assert disabled.rollout_mode == before.rollout_mode == "active"
    assert disabled.rollout_epoch == before.rollout_epoch == 0

    text = "明天我要去取洗好的外套"
    session_id, message_id = _source(text)
    assert short_memo.process_user_message(
        session_id=session_id,
        message_id=message_id,
        text=text,
        snapshot=disabled,
    ) == {"status": "disabled"}
    assert short_memo.recall(text, snapshot=disabled) == []

    enabled = short_memo.update_product_settings(enabled=True)
    assert enabled.enabled is True
    assert enabled.rollout_mode == "active"
    assert enabled.rollout_epoch == 0
    assert short_memo.process_user_message(
        session_id=session_id,
        message_id=message_id,
        text=text,
        snapshot=enabled,
    )["status"] == "created"


def test_active_chat_silently_creates_and_reuses_source_backed_memo(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    captured_messages: list[list[dict[str, str]]] = []

    async def fake_stream(_provider, _model, messages, **_kwargs):
        captured_messages.append(messages)
        yield "好的。"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    client = TestClient(app, headers={"X-Xiadie-Token": TEST_API_TOKEN})
    session = client.post("/api/sessions", json={}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"],
        "content": "明天我要去图书馆还书",
    }) as response:
        assert response.status_code == 200
        assert "event: done" in "".join(response.iter_text())

    items = client.get("/api/life/short-memos").json()["items"]
    assert len(items) == 1
    assert items[0]["content"] == "明天我要去图书馆还书"
    assert items[0]["source_session_id"] == session["id"]

    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"],
        "content": "图书馆的书什么时候还？",
    }) as response:
        assert response.status_code == 200
        assert "event: done" in "".join(response.iter_text())
    second_prompt = "\n".join(
        str(message.get("content") or "") for message in captured_messages[-1]
    )
    assert "明天我要去图书馆还书" in second_prompt

    temporary = client.post("/api/sessions", json={"temporary": True}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": temporary["id"],
        "content": "后天我要去取修好的相机",
        "temporary_chat": True,
    }) as response:
        assert response.status_code == 200
        assert "event: done" in "".join(response.iter_text())
    assert len(client.get("/api/life/short-memos").json()["items"]) == 1

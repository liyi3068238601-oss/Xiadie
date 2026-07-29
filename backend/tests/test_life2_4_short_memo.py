from __future__ import annotations

import asyncio
import json

import pytest

from app import db, short_memo


@pytest.fixture(autouse=True)
def clean_short_memo_state():
    db.init_db()
    short_memo.clear(clear_events=True)
    db.set_setting("life.short_memo.enabled", "1")
    db.set_setting("life.short_memo.rollout_mode", "shadow")
    db.set_setting("life.short_memo.rollout_epoch", "0")
    db.set_setting("life.short_memo.remote_extraction_enabled", "0")
    db.set_setting("life.short_memo.default_ttl_seconds", "259200")
    db.set_setting("life.short_memo.max_active", "10")
    db.set_setting("life.short_memo.max_recall", "3")
    yield
    short_memo.clear(clear_events=True)
    db.set_setting("life.short_memo.rollout_mode", "shadow")


def _source(text: str) -> tuple[str, str]:
    session_id, message_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        now = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "ShortMemo synthetic", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, session_id, "user", text, now),
        )
        conn.commit()
    finally:
        conn.close()
    return session_id, message_id


def test_schema_82_is_additive_and_defaults_to_shadow():
    conn = db.connect()
    try:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "82"
        memo_columns = {row["name"] for row in conn.execute("PRAGMA table_info(short_memos)")}
        event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(short_memo_events)")}
        assert {"source_snapshot_hash", "dedupe_key", "expires_at"} <= memo_columns
        assert event_columns == {"id", "memo_id", "action", "reason_code", "metadata_json", "created_at"}
        assert short_memo.rollout_snapshot().rollout_mode == "shadow"
    finally:
        conn.close()


def test_200_synthetic_candidates_cover_valid_sensitive_secret_and_non_memo():
    rows: list[tuple[str, bool, str]] = []
    rows += [(f"明天我要去图书馆还第{i}本书", True, "normal") for i in range(50)]
    rows += [(f"后天我要去医院做第{i}次复查", True, "sensitive_minimized") for i in range(30)]
    rows += [(f"明天记得提醒我验证码是 {100000 + i}", False, "secret") for i in range(50)]
    rows += [(f"这是普通闲聊第{i}条，没有近期安排", False, "ordinary") for i in range(70)]
    assert len(rows) == 200
    for text, expected, kind in rows:
        candidate, reason = short_memo.analyze_user_text(text)
        assert (candidate is not None) is expected, (text, reason)
        if candidate:
            assert candidate.sensitivity == kind
            assert 1 <= len(candidate.content) <= 240
            assert "验证码" not in candidate.content
        elif kind == "secret":
            assert reason == "secret_rejected"


def test_shadow_never_writes_memo_or_event():
    text = "明天我要去图书馆还书"
    session_id, message_id = _source(text)
    result = short_memo.process_user_message(
        session_id=session_id, message_id=message_id, text=text,
        snapshot=short_memo.rollout_snapshot(),
    )
    conn = db.connect()
    try:
        assert result["status"] == "shadow_candidate"
        assert conn.execute("SELECT COUNT(*) FROM short_memos").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM short_memo_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_active_create_recall_dedupe_and_expiry_are_source_guarded(monkeypatch):
    text = "明天我要去图书馆还书"
    session_id, message_id = _source(text)
    snap = short_memo.set_rollout_mode("active")
    started = db.now()
    created = short_memo.process_user_message(
        session_id=session_id, message_id=message_id, text=text, snapshot=snap, now=started,
    )
    repeated = short_memo.process_user_message(
        session_id=session_id, message_id=message_id, text=text, snapshot=snap, now=started + 10,
    )
    assert created["status"] == "created"
    assert repeated == {"status": "deduplicated", "id": created["id"]}
    assert len(short_memo.list_active()) == 1
    recalled = short_memo.recall("图书馆的书什么时候还", snapshot=snap)
    assert [item["id"] for item in recalled] == [created["id"]]
    conn = db.connect()
    try:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM short_memo_events ORDER BY created_at,id"
        )]
        assert actions == ["created", "deduplicated"]
        conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM short_memos").fetchone()[0] == 0
    finally:
        conn.close()


def test_capacity_rejection_has_no_fake_memo_or_event():
    db.set_setting("life.short_memo.max_active", "1")
    snap = short_memo.set_rollout_mode("active")
    first_text, second_text = "明天我要归还图书馆的书", "后天我要领取修好的相机"
    first_session, first_message = _source(first_text)
    second_session, second_message = _source(second_text)
    assert short_memo.process_user_message(
        session_id=first_session, message_id=first_message, text=first_text, snapshot=snap,
    )["status"] == "created"
    rejected = short_memo.process_user_message(
        session_id=second_session, message_id=second_message, text=second_text, snapshot=snap,
    )
    assert rejected == {"status": "rejected", "reason": "capacity_rejected"}
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM short_memos").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM short_memo_events").fetchone()[0] == 1
    finally:
        conn.close()


def test_event_metadata_is_bounded_and_body_free():
    assert json.loads(short_memo.validate_event_metadata({
        "protocol_version": "short-memo-v1", "revision": 1,
        "ttl_seconds": 3600, "rollout_epoch": 1,
    }))["revision"] == 1
    for invalid in (
        {"content": "secret body"}, {"revision": True}, {"ttl_seconds": 10},
        {"protocol_version": "wrong"}, {"revision": {"nested": 1}},
    ):
        with pytest.raises(short_memo.ShortMemoError):
            short_memo.validate_event_metadata(invalid)


def test_expiry_update_delete_clear_and_product_switch_are_user_governed():
    text = "明天我要去取洗好的外套"
    session_id, message_id = _source(text)
    snap = short_memo.set_rollout_mode("active")
    result = short_memo.process_user_message(
        session_id=session_id, message_id=message_id, text=text, snapshot=snap,
    )
    item = short_memo.list_active()[0]
    updated = short_memo.update_expiry(
        result["id"], expected_revision=item["revision"],
        expires_at=item["created_at"] + short_memo.MIN_TTL + 60,
    )
    assert updated["revision"] == 2
    with pytest.raises(short_memo.ShortMemoError, match="revision_conflict"):
        short_memo.update_expiry(
            result["id"], expected_revision=1, expires_at=updated["expires_at"],
        )
    assert short_memo.update_product_settings(enabled=False).enabled is False
    assert short_memo.recall("外套", snapshot=short_memo.rollout_snapshot()) == []
    assert short_memo.delete(result["id"])
    assert not short_memo.delete(result["id"])
    assert short_memo.clear(clear_events=True) == 0
    assert short_memo.export_data()["events"] == []


def test_remote_validator_can_only_veto_and_failure_is_fail_closed(monkeypatch):
    text = "明天我要去图书馆还书"
    session_id, message_id = _source(text)
    db.set_setting("life.short_memo.remote_extraction_enabled", "1")
    snap = short_memo.set_rollout_mode("active")

    async def reject(*_args, **_kwargs):
        return {"text": '{"accept":false}'}

    monkeypatch.setattr(short_memo.llm, "complete_json", reject)
    rejected = asyncio.run(short_memo.validate_and_process_user_message(
        session_id=session_id, message_id=message_id, text=text,
        provider={"id": "remote", "base_url": "https://example.invalid"},
        model="validator", snapshot=snap,
    ))
    assert rejected["reason"] == "remote_validation_rejected"
    assert short_memo.list_active() == []

    async def malformed(*_args, **_kwargs):
        return {"text": '{"accept":true,"content":"model-authored"}'}

    monkeypatch.setattr(short_memo.llm, "complete_json", malformed)
    failed = asyncio.run(short_memo.validate_and_process_user_message(
        session_id=session_id, message_id=message_id, text=text,
        provider={"id": "remote", "base_url": "https://example.invalid"},
        model="validator", snapshot=snap,
    ))
    assert failed["reason"] == "remote_validation_failed"
    assert short_memo.list_active() == []


def test_remote_validator_accepts_only_the_deterministic_candidate(monkeypatch):
    text = "明天我要去图书馆还书"
    session_id, message_id = _source(text)
    db.set_setting("life.short_memo.remote_extraction_enabled", "1")
    snap = short_memo.set_rollout_mode("active")
    observed = {}

    async def accept(_provider, _model, messages, **_kwargs):
        observed["content"] = messages[-1]["content"]
        return {"text": '{"accept":true}'}

    monkeypatch.setattr(short_memo.llm, "complete_json", accept)
    result = asyncio.run(short_memo.validate_and_process_user_message(
        session_id=session_id, message_id=message_id, text=text,
        provider={"id": "remote", "base_url": "https://example.invalid"},
        model="validator", snapshot=snap,
    ))
    assert result["status"] == "created"
    assert observed["content"] == text
    stored = short_memo.list_active()[0]
    assert stored["content"] == text
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT extraction_method FROM short_memos WHERE id=?", (stored["id"],),
        ).fetchone()[0] == "model_validated"
    finally:
        conn.close()


def test_rollout_off_never_calls_remote_validator(monkeypatch):
    text = "明天我要去图书馆还书"
    session_id, message_id = _source(text)
    db.set_setting("life.short_memo.remote_extraction_enabled", "1")
    snap = short_memo.set_rollout_mode("off")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("remote validator must not run while rollout is off")

    monkeypatch.setattr(short_memo.llm, "complete_json", forbidden)
    result = asyncio.run(short_memo.validate_and_process_user_message(
        session_id=session_id, message_id=message_id, text=text,
        provider={"id": "remote", "base_url": "https://example.invalid"},
        model="validator", snapshot=snap,
    ))
    assert result == {"status": "disabled"}

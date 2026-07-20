"""会话摘要派生数据、状态机、租约及 CTX.4 聊天消费边界。"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import conversation_summaries, conversation_summary_service, db, llm
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


@pytest.fixture(autouse=True)
def clean_summary_data():
    db.init_db()
    db.set_setting("current_model", '{"provider_id":"mock","model":"xiadie-mock"}')
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.commit()
    finally:
        conn.close()
    yield


def _session_with_turns(count: int = 1) -> tuple[str, list[str]]:
    conn = db.connect()
    try:
        sid = db.new_id()
        now = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (sid, "摘要测试", now, now),
        )
        ids = []
        for index in range(count):
            uid, aid = db.new_id(), db.new_id()
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (uid, sid, "user", f"用户原文-{index}", now + index * 2 + 0.1),
            )
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,model,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (aid, sid, "assistant", f"助手原文-{index}", "xiadie-mock",
                 now + index * 2 + 0.2),
            )
            ids.extend([uid, aid])
        conn.commit()
        return sid, ids
    finally:
        conn.close()


def _activate(run: dict, summary: dict | None = None) -> dict:
    claimed = conversation_summaries.claim_next()
    assert claimed and claimed["id"] == run["id"]
    return conversation_summaries.activate_result(
        run["id"], claimed["lease_token"], summary or {"continuity": "已完成一轮对话"},
    )


def test_schema_42_upgrades_old_database_without_losing_messages():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES('schema_version','41');
        CREATE TABLE sessions(
            id TEXT PRIMARY KEY,title TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,updated_at REAL NOT NULL
        );
        CREATE TABLE messages(
            id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,favorite INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        INSERT INTO sessions VALUES('s1','旧会话',0,1,1);
        INSERT INTO messages VALUES('m1','s1','user','迁移前原文',NULL,0,1);
        """
    )
    migration = next(sql for version, sql in db.MIGRATIONS if version == 42)
    conn.executescript(migration)

    assert conn.execute("SELECT content FROM messages WHERE id='m1'").fetchone()[0] == "迁移前原文"
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'conversation_summary_%'"
    )}
    assert tables == {
        "conversation_summary_runs", "conversation_summary_revisions",
        "conversation_summary_events",
    }
    revision_columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(conversation_summary_revisions)"
    )}
    assert {
        "summary_text", "open_threads_json", "decisions_json", "corrections_json",
        "entity_refs_json", "provider_id", "model", "prompt_tokens",
        "completion_tokens", "superseded_at",
    } <= revision_columns


def test_source_range_requires_contiguous_complete_turns():
    sid, ids = _session_with_turns(2)
    conn = db.connect()
    try:
        snapshot = conversation_summaries.source_snapshot(
            conn, sid, start_message_id=ids[0], end_message_id=ids[-1],
        )
        assert snapshot.message_count == 4
        assert len(snapshot.source_hash) == 64
        with pytest.raises(conversation_summaries.ConversationSummaryError) as caught:
            conversation_summaries.source_snapshot(
                conn, sid, start_message_id=ids[1], end_message_id=ids[-1],
            )
        assert caught.value.code == "summary_source_turn_incomplete"
    finally:
        conn.close()


def test_same_source_is_idempotent_and_cannot_activate_twice():
    sid, _ = _session_with_turns(1)
    first = conversation_summaries.enqueue(sid)
    repeated = conversation_summaries.enqueue(sid)

    assert first["id"] == repeated["id"]
    assert [event["action"] for event in repeated["events"]] == ["enqueued"]
    revision = _activate(first)
    assert revision["status"] == "active"
    assert conversation_summaries.enqueue(sid)["id"] == first["id"]
    assert len(conversation_summaries.list_revisions(sid)) == 1


def test_new_revision_supersedes_only_the_previous_active_revision():
    sid, _ = _session_with_turns(1)
    first = _activate(conversation_summaries.enqueue(sid), {"continuity": "第一版"})
    conn = db.connect()
    try:
        now = db.now() + 10
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (db.new_id(), sid, "user", "第二轮问题", now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
            (db.new_id(), sid, "assistant", "第二轮回答", "xiadie-mock", now + 1),
        )
        conn.commit()
    finally:
        conn.close()
    second = _activate(conversation_summaries.enqueue(sid), {"continuity": "第二版"})
    revisions = conversation_summaries.list_revisions(sid)

    assert second["revision"] == 2 and second["status"] == "active"
    assert next(row for row in revisions if row["id"] == first["id"])["status"] == "superseded"
    assert sum(row["status"] == "active" for row in revisions) == 1
    conn = db.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE conversation_summary_revisions SET status='forged' WHERE id=?",
                (second["id"],),
            )
    finally:
        conn.close()


def test_source_change_during_generation_rejects_result_without_revision():
    sid, ids = _session_with_turns(1)
    run = conversation_summaries.enqueue(sid)
    claimed = conversation_summaries.claim_next()
    conn = db.connect()
    try:
        conn.execute("UPDATE messages SET content='来源已变化' WHERE id=?", (ids[0],))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(conversation_summaries.ConversationSummaryError) as caught:
        conversation_summaries.activate_result(
            run["id"], claimed["lease_token"], {"continuity": "不得落库"},
        )

    assert caught.value.code == "summary_source_changed"
    assert conversation_summaries.get_run(run["id"])["status"] == "failed"
    assert conversation_summaries.list_revisions(sid) == []


def test_lease_heartbeat_and_stale_recovery_are_bounded():
    sid, _ = _session_with_turns(1)
    run = conversation_summaries.enqueue(sid)
    claimed = conversation_summaries.claim_next(lease_seconds=10)
    assert conversation_summaries.heartbeat(run["id"], claimed["lease_token"], lease_seconds=20)
    assert not conversation_summaries.heartbeat(run["id"], "wrong-token")

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE conversation_summary_runs SET lease_expires_at=0 WHERE id=?", (run["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    assert conversation_summaries.recover_stale_runs() == 1
    recovered = conversation_summaries.get_run(run["id"])
    assert recovered["status"] == "recovery_pending"
    assert recovered["events"][-1]["action"] == "recovery_scheduled"

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE conversation_summary_runs SET next_attempt_at=0,attempt_count=max_attempts-1"
            " WHERE id=?", (run["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    final_claim = conversation_summaries.claim_next()
    assert final_claim["attempt_count"] == final_claim["max_attempts"]
    assert conversation_summaries.fail_run(
        run["id"], final_claim["lease_token"], "synthetic_failure", retryable=True,
    )["status"] == "exhausted"
    failed_revision = conversation_summaries.list_revisions(sid)[0]
    assert failed_revision["status"] == "failed"
    assert failed_revision["summary_present"] is False


def test_events_and_read_only_diagnostics_never_expose_bodies_or_raw_summary():
    marker = "绝不能进入审计事件的原始正文-CTX2"
    sid, _ = _session_with_turns(1)
    conn = db.connect()
    try:
        conn.execute("UPDATE messages SET content=? WHERE session_id=?", (marker, sid))
        conn.commit()
    finally:
        conn.close()
    run = conversation_summaries.enqueue(sid)
    revision = _activate(run, {"continuity": marker})
    diagnostics = {
        "run": client.get(f"/api/conversation-summaries/runs/{run['id']}").json(),
        "revisions": client.get(
            f"/api/sessions/{sid}/conversation-summary-revisions"
        ).json(),
        "events": client.get(f"/api/sessions/{sid}/conversation-summary-events").json(),
    }
    encoded = json.dumps(diagnostics, ensure_ascii=False)

    assert revision["summary_present"] is True
    assert marker not in encoded
    assert "summary_text" not in encoded
    assert client.post("/api/conversation-summaries/runs", json={}).status_code == 405
    assert client.post(
        f"/api/sessions/{sid}/conversation-summary-revisions", json={"status": "active"},
    ).status_code == 405


def test_session_delete_cascades_all_summary_records():
    sid, _ = _session_with_turns(1)
    _activate(conversation_summaries.enqueue(sid))
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    conn = db.connect()
    try:
        for table in (
            "conversation_summary_runs", "conversation_summary_revisions",
            "conversation_summary_events",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        conn.close()


def test_regenerate_excludes_covered_summary_and_invalidates_it_after_success(monkeypatch):
    calls = []

    async def fake_stream(_provider, _model, messages, **_kwargs):
        calls.append(list(messages))
        yield "第一条回复" if len(calls) == 1 else "第二条回复"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat", json={"session_id": session["id"], "content": "第一轮问题"},
    ) as response:
        "".join(response.iter_text())
    runs = conversation_summaries.list_runs(session_id=session["id"])
    if not runs:
        diagnostic = conversation_summary_service.enqueue_after_chat(
            session_id=session["id"], chat_provider=None, chat_model="xiadie-mock",
        )
        pytest.fail(f"chat summary enqueue missing: {diagnostic}")
    run = runs[0]
    _activate(run, {"continuity": "SUMMARY-MUST-NOT-ENTER-CHAT"})

    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "第一轮问题", "regenerate": True},
    ) as response:
        "".join(response.iter_text())

    assert all(
        "SUMMARY-MUST-NOT-ENTER-CHAT" not in message["content"]
        for call in calls for message in call
    )
    revision = conversation_summaries.list_revisions(session["id"])[0]
    assert revision["status"] == "invalid"
    assert revision["error_code"] == "source_message_replaced"


def test_chat_consumes_active_summary_but_not_its_covered_raw_messages(monkeypatch):
    sid, _ = _session_with_turns(4)
    _activate(
        conversation_summaries.enqueue(sid),
        {"continuity": "我们此前决定继续以陪伴和自然聊天为核心。"},
    )
    # helper 为轮次生成了递增的未来时间；将旧消息移回当前请求之前，模拟真实顺序。
    conn = db.connect()
    try:
        conn.execute("UPDATE messages SET created_at=created_at-100 WHERE session_id=?", (sid,))
        conn.commit()
    finally:
        conn.close()
    captured = {}

    async def fake_stream(_provider, _model, messages, **_kwargs):
        captured["messages"] = list(messages)
        yield "我记得，我们就沿着这个方向慢慢走。"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    with client.stream(
        "POST", "/api/chat", json={"session_id": sid, "content": "那就继续吧"},
    ) as response:
        body = "".join(response.iter_text())

    assert captured, body
    encoded = "\n".join(message["content"] for message in captured["messages"])
    assert response.status_code == 200 and "event: done" in body
    assert "以陪伴和自然聊天为核心" in captured["messages"][0]["content"]
    assert "用户原文-0" not in encoded and "助手原文-3" not in encoded
    assert captured["messages"][-1]["content"] == "那就继续吧"
    assert '"summary_used": true' in body
    assert '"summary_covered_messages": 8' in body


def test_failed_regenerate_keeps_active_summary_revision(monkeypatch):
    sid, _ = _session_with_turns(1)
    _activate(
        conversation_summaries.enqueue(sid),
        {"continuity": "这一轮已经成为共同经历。"},
    )

    async def failing_stream(*_args, **_kwargs):
        raise llm.LLMError("暂时无法回复", "稍后重试")
        yield  # pragma: no cover - 保持 async generator 形状

    monkeypatch.setattr(llm, "stream_chat", failing_stream)
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": sid, "content": "用户原文-0", "regenerate": True},
    ) as response:
        body = "".join(response.iter_text())

    revision = conversation_summaries.list_revisions(sid)[0]
    assert "event: error" in body
    assert revision["status"] == "active"
    assert revision["error_code"] is None

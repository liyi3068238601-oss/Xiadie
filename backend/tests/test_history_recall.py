"""CTX.5 跨会话两阶段历史回忆、生命周期、预算与隐私测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import context_assembler, context_budget, db, history_recall, llm, memory as memory_store
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


@pytest.fixture(autouse=True)
def clean_history_data():
    db.init_db()
    db.set_setting("current_model", '{"provider_id":"mock","model":"xiadie-mock"}')
    db.set_setting("conversation_history_recall_mode", "explicit_only")
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM conversation_history_recall_events")
        conn.commit()
    finally:
        conn.close()
    yield


def _session(title: str, turns: list[tuple[str, str]], *, archived: bool = False,
             age: float = 0) -> str:
    conn = db.connect()
    try:
        sid = db.new_id()
        base = db.now() - age - len(turns) * 10
        conn.execute(
            "INSERT INTO sessions(id,title,archived,created_at,updated_at) VALUES(?,?,?,?,?)",
            (sid, title, 1 if archived else 0, base, base + len(turns) * 2),
        )
        for index, (user, assistant) in enumerate(turns):
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (f"{sid}u{index}", sid, "user", user, base + index * 2),
            )
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,model,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (f"{sid}a{index}", sid, "assistant", assistant, "xiadie-mock",
                 base + index * 2 + 1),
            )
        conn.commit()
        return sid
    finally:
        conn.close()


def _prepare(query: str, current_session_id: str) -> dict:
    conn = db.connect()
    try:
        result = history_recall.prepare_locked(
            conn, query, current_session_id=current_session_id,
        )
        conn.commit()
        return result
    finally:
        conn.close()


def _capability(window: int = 16_384):
    return context_budget.resolve_model_context_capability(
        {"id": "custom"}, "history-model",
        configured_profiles={
            "custom/history-model": {
                "context_window": window,
                "max_output_tokens": 2_048,
                "default_output_tokens": 2_048,
            },
        },
    )


def test_schema_45_keeps_rebuildable_local_history_indexes():
    sid = _session("Blender 材质", [("把材质改成玻璃", "已经记下玻璃材质方案")])
    conn = db.connect()
    try:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        session_count = conn.execute(
            "SELECT COUNT(*) FROM conversation_history_sessions_fts WHERE session_id=?", (sid,)
        ).fetchone()[0]
        message_count = conn.execute(
            "SELECT COUNT(*) FROM conversation_history_messages_fts WHERE session_id=?", (sid,)
        ).fetchone()[0]
    finally:
        conn.close()

    rebuilt = history_recall.rebuild_index()
    assert version == "60"
    assert session_count == 1 and message_count == 2
    assert rebuilt == {"sessions": 1, "messages": 2}


def test_explicit_math_recall_finds_real_complete_turn_in_another_session():
    old = _session("随手计算", [("12×8 是多少？", "12×8 等于 96。")])
    current = _session("今天", [])
    result = _prepare("我以前问过你什么算术题？", current)

    assert result["status"] == "injected" and len(result["turns"]) == 1
    turn = result["turns"][0]
    assert turn["source_type"] == "cross_session_history"
    assert turn["session_id"] == old
    assert turn["user_text"] == "12×8 是多少？"
    assert turn["assistant_text"] == "12×8 等于 96。"
    assert turn["locator"].startswith(f"session:{old}/messages:")


def test_two_stage_selection_avoids_unrelated_newer_session():
    relevant = _session(
        "Blender 创作", [("玻璃材质的粗糙度设为多少？", "我们决定设为 0.12。")], age=10_000,
    )
    _session("今天的午饭", [("中午吃什么？", "可以吃清淡一点。")], age=0)
    current = _session("新话题", [])
    result = _prepare("我们之前讨论的 Blender 材质是什么？", current)

    assert result["turns"]
    assert {item["session_id"] for item in result["turns"]} == {relevant}
    assert all("午饭" not in item["session_title"] for item in result["turns"])


def test_ordinary_query_is_shadow_only_until_fixed_evaluation_allows_it():
    _session("单窗口决定", [("界面只保留单主窗口", "好，我们保持单主窗口。")])
    current = _session("新话题", [])
    result = _prepare("单主窗口要怎么布局？", current)

    assert result["intent"] == "ordinary"
    assert result["status"] == "shadow"
    assert result["candidate_turn_count"] > 0
    assert result["turns"] == []
    event = history_recall.list_events(session_id=current)[0]
    assert event["diagnostic"]["reason"] == "ordinary_query_shadow_only"
    assert "weights" in event["diagnostic"]


def test_on_mode_still_keeps_ordinary_recall_in_shadow_until_calibrated():
    _session("窗口决定", [("只保留单主窗口", "好的。")])
    current = _session("当前", [])
    db.set_setting("conversation_history_recall_mode", "on")

    result = _prepare("单主窗口要怎么布局", current)

    assert history_recall.AUTOMATIC_RECALL_CALIBRATED is False
    assert result["status"] == "shadow"
    assert result["turns"] == []


def test_off_mode_does_not_search_candidates():
    _session("隐私边界", [("过去说过的内容", "过去回答")])
    current = _session("当前", [])
    db.set_setting("conversation_history_recall_mode", "off")

    result = _prepare("你还记得过去说过的内容吗？", current)

    assert result["status"] == "off"
    assert result["candidate_session_count"] == 0
    assert result["candidate_turn_count"] == 0
    assert history_recall.list_events(session_id=current)[0]["diagnostic"]["reason"] == "disabled"


def test_archived_sessions_remain_recallable_and_permanent_delete_removes_index():
    archived = _session(
        "旧项目决定", [("项目名字确定为遐蝶", "以后就叫遐蝶。")], archived=True,
    )
    current = _session("新会话", [])
    recalled = _prepare("你还记得我们之前决定的项目名字吗？", current)
    assert recalled["turns"] and recalled["turns"][0]["session_archived"] is True

    assert client.delete(f"/api/sessions/{archived}").status_code == 200
    after = _prepare("你还记得我们之前决定的项目名字吗？", current)
    assert after["turns"] == []
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM conversation_history_sessions_fts WHERE session_id=?",
            (archived,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM conversation_history_messages_fts WHERE session_id=?",
            (archived,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_new_session_does_not_clear_old_history_or_long_term_memory():
    old = _session("窗口偏好", [("我不喜欢多窗口", "我会尊重这个选择。")])
    memory = client.post(
        "/api/memories",
        json={"layer": "L2", "content": "用户偏好单主窗口", "tags": "界面"},
    ).json()
    new_session = client.post("/api/sessions", json={}).json()["id"]
    result = _prepare("你还记得我之前对窗口有什么偏好吗？", new_session)

    assert result["turns"] and result["turns"][0]["session_id"] == old
    assert memory_store.get_memory(memory["id"])["content"] == "用户偏好单主窗口"


def test_history_recall_is_independent_from_long_term_memory_toggle():
    old = _session("陪伴方向", [("核心要贴近陪伴和聊天", "我会保持伴侣感。")])
    current = _session("新话题", [])

    db.set_setting("memory_enabled", "0")
    disabled = _prepare("你还记得我们以前定下的陪伴方向吗？", current)
    db.set_setting("memory_enabled", "1")
    enabled = _prepare("你还记得我们以前定下的陪伴方向吗？", current)

    assert disabled["turns"][0]["session_id"] == old
    assert enabled["turns"][0]["session_id"] == old


def test_session_title_and_active_summary_index_follow_source_lifecycle():
    sid = _session("旧标题", [("早期讨论", "早期回答")])
    conn = db.connect()
    try:
        conn.execute("UPDATE sessions SET title='模型路由决定' WHERE id=?", (sid,))
        message_ids = [row[0] for row in conn.execute(
            "SELECT id FROM messages WHERE session_id=? ORDER BY created_at,id", (sid,),
        ).fetchall()]
        run_id, revision_id = db.new_id(), db.new_id()
        now = db.now()
        conn.execute(
            "INSERT INTO conversation_summary_runs("
            "id,idempotency_key,session_id,status,protocol_version,source_start_message_id,"
            "source_end_message_id,source_message_count,source_hash,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, f"test:{run_id}", sid, "completed", "conversation-summary-v1",
             message_ids[0], message_ids[-1], 2, "a" * 64, now, now),
        )
        conn.execute(
            "INSERT INTO conversation_summary_revisions("
            "id,session_id,run_id,revision,status,protocol_version,source_start_message_id,"
            "source_end_message_id,source_message_count,source_hash,summary_text,created_at,"
            "updated_at,activated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, sid, run_id, 1, "active", "conversation-summary-v1",
             message_ids[0], message_ids[-1], 2, "a" * 64,
             "用户最后决定采用模型能力表。", now, now, now),
        )
        conn.commit()
        indexed = dict(conn.execute(
            "SELECT title,summary_text FROM conversation_history_sessions_fts WHERE session_id=?",
            (sid,),
        ).fetchone())
        assert indexed == {"title": "模型路由决定", "summary_text": "用户最后决定采用模型能力表。"}

        conn.execute(
            "UPDATE conversation_summary_revisions SET status='invalid' WHERE id=?",
            (revision_id,),
        )
        conn.commit()
        assert conn.execute(
            "SELECT summary_text FROM conversation_history_sessions_fts WHERE session_id=?",
            (sid,),
        ).fetchone()[0] == ""
    finally:
        conn.close()


def test_fixed_recall_evaluation_set_returns_real_complete_turns():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "history_recall_eval_v1.json")
        .read_text(encoding="utf-8")
    )
    assert fixture["protocol_version"] == "conversation-history-eval-v1"
    for case in fixture["cases"]:
        old = _session(case["title"], [(case["user"], case["assistant"])])
        current = _session(f"评测-{case['name']}", [])
        result = _prepare(case["query"], current)
        assert result["turns"], case["name"]
        turn = result["turns"][0]
        assert turn["session_id"] == old
        assert case["expected"] in turn["user_text"] + turn["assistant_text"]
        assert turn["locator"].startswith(f"session:{old}/messages:")


def test_context_assembler_gives_cross_session_history_an_independent_budget():
    current = "current-session"
    candidate = {
        "source_type": "cross_session_history",
        "session_id": "old-session",
        "session_title": "旧讨论",
        "user_message_id": "old-u",
        "assistant_message_id": "old-a",
        "user_text": "我们决定继续做陪伴型产品。" * 100,
        "assistant_text": "好，我会沿着陪伴和聊天的方向继续。" * 100,
        "locator": "session:old-session/messages:old-u:old-a",
        "score": 10,
    }
    history = [
        {"id": "u1", "role": "user", "content": "最近问题", "model": ""},
        {"id": "a1", "role": "assistant", "content": "最近回答", "model": "m"},
        {"id": "now", "role": "user", "content": "继续陪我聊聊", "model": ""},
    ]
    package = context_assembler.assemble(
        history=history, capability=_capability(), current_session_id=current,
        cross_session_recall=[candidate], memory_digest="记忆" * 2_000,
        knowledge_block="知识" * 2_000, lore_digest="设定" * 2_000,
    )
    meta = package.public_meta()

    assert package.budget_plan.reserved_total_tokens <= 16_384
    assert package.messages[-1]["content"] == "继续陪我聊聊"
    assert [message["role"] for message in package.messages[-3:]] == [
        "user", "assistant", "user",
    ]
    assert meta["component_tokens"]["cross_session_recall"] > 0
    assert meta["source_type_counts"]["cross_session_history"] == 1
    assert meta["source_type_counts"]["existing_memory"] == 1
    assert meta["source_type_counts"]["user_knowledge"] == 1


def test_context_assembler_rejects_current_session_or_incomplete_history_candidate():
    base = {
        "source_type": "cross_session_history",
        "session_id": "current",
        "session_title": "当前",
        "user_message_id": "u",
        "assistant_message_id": "a",
        "user_text": "旧问题", "assistant_text": "旧回答",
        "locator": "session:current/messages:u:a", "score": 9,
    }
    package = context_assembler.assemble(
        history=[{"id": "now", "role": "user", "content": "现在", "model": ""}],
        capability=_capability(), current_session_id="current",
        cross_session_recall=[base, {**base, "session_id": "old", "assistant_text": ""}],
    )
    assert package.cross_session_turns == ()
    assert package.public_meta()["cross_session_recall_count"] == 0


def test_recall_event_and_public_meta_never_contain_query_or_recalled_text():
    marker = "PRIVATE-HISTORY-MARKER"
    _session("隐私测试", [(marker, "只用于测试")])
    current = _session("当前", [])
    result = _prepare(f"我之前说过 {marker} 吗？", current)
    events = client.get(f"/api/history-recall/events?session_id={current}").json()
    package = context_assembler.assemble(
        history=[{"id": "now", "role": "user", "content": "当前", "model": ""}],
        capability=_capability(), current_session_id=current,
        cross_session_recall=result["turns"],
    )
    encoded = json.dumps({"events": events, "meta": package.public_meta()}, ensure_ascii=False)

    assert marker not in encoded
    assert len(events[0]["query_sha256"]) == 64
    assert events[0]["score_version"] == history_recall.SCORE_VERSION


def test_chat_integration_injects_only_selected_old_turn_and_returns_real_locator(monkeypatch):
    old = _session("窗口决定", [("我们决定只用单主窗口", "好，就保留单主窗口。")])
    _session("无关会话", [("今晚吃面", "可以。")])
    current = _session("现在", [])
    captured = {}

    async def fake_stream(_provider, _model, messages, **_kwargs):
        captured["messages"] = list(messages)
        yield "我记得，我们决定保留单主窗口。"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": current, "content": "你还记得我们之前决定的窗口布局吗？"},
    ) as response:
        body = "".join(response.iter_text())

    encoded = "\n".join(message["content"] for message in captured["messages"])
    assert "我们决定只用单主窗口" in captured["messages"][0]["content"]
    assert "今晚吃面" not in encoded
    assert '"history_recall_used": true' in body
    assert f'"session_id": "{old}"' in body
    assert "source_type: cross_session_history" in captured["messages"][0]["content"]

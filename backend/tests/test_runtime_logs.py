import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, runtime_logs
from app.main import app

CLIENT = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


def _session(session_id: str, *, created_at: float = 100.0) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,archived,created_at,updated_at) VALUES(?,?,?,?,?)",
            (session_id, "日志测试", 0, created_at, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _message(
    message_id: str, session_id: str, role: str, content: str, created_at: float,
    *, model: str | None = None, proactive_delivery_id: str | None = None,
) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,proactive_delivery_id,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (message_id, session_id, role, content, model, proactive_delivery_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _tool(log_id: str, created_at: float) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO tool_logs(id,tool,risk_level,status,summary,created_at) VALUES(?,?,?,?,?,?)",
            (log_id, "file.read", "S0", "done", "读取操作完成", created_at),
        )
        conn.commit()
    finally:
        conn.close()


def test_runtime_feed_unifies_sources_without_copying_full_chat_bodies():
    _session("runtime-feed-session")
    long_input = "输入正文-" + "甲" * 400
    long_output = "输出正文-" + "乙" * 400
    _message("runtime-feed-user", "runtime-feed-session", "user", long_input, 101.0)
    _message(
        "runtime-feed-assistant", "runtime-feed-session", "assistant", long_output,
        102.0, model="deepseek-v4-flash",
    )
    _tool("runtime-feed-tool", 103.0)

    result = runtime_logs.list_feed(limit=50)
    assert {item["category"] for item in result["items"]} >= {"model", "tool"}
    assert result["counts"]["model"] >= 1
    assert result["counts"]["tool"] >= 1
    assert result["total"] == len(result["items"])
    chat = next(item for item in result["items"] if item["id"] == "chat:runtime-feed-assistant")
    assert chat["detail_available"] is True
    assert chat["details"]["input_count"] == 1
    assert "输入：" in chat["summary"] and "输出：" in chat["summary"]
    rendered = json.dumps(result, ensure_ascii=False)
    assert long_input not in rendered
    assert long_output not in rendered
    assert "系统提示词" in result["privacy_notice"]
    assert "最终回复" in result["privacy_notice"]


def test_runtime_feed_filters_after_global_window_and_uses_stable_order():
    _session("runtime-window-session", created_at=9_000_000_000.0)
    _message("runtime-window-user", "runtime-window-session", "user", "窗口输入", 9_000_000_001.0)
    _message("runtime-window-b", "runtime-window-session", "assistant", "回复 B", 9_000_000_002.0)
    _message("runtime-window-a", "runtime-window-session", "assistant", "回复 A", 9_000_000_002.0)
    _tool("runtime-window-tool", 9_000_000_003.0)

    result = runtime_logs.list_feed(limit=3)
    assert [item["id"] for item in result["items"][:3]] == [
        "tool:runtime-window-tool", "chat:runtime-window-b", "chat:runtime-window-a",
    ]
    assert sum(result["counts"].values()) == 3

    tools = runtime_logs.list_feed(category="tool", status="success", limit=3)
    assert [item["id"] for item in tools["items"]] == ["tool:runtime-window-tool"]
    assert sum(tools["counts"].values()) == 3
    assert tools["total"] == 1


def test_runtime_feed_validates_enums_and_bounds_limit():
    with pytest.raises(runtime_logs.RuntimeLogError) as category_error:
        runtime_logs.list_feed(category="unknown")
    assert category_error.value.code == "runtime_log_category_invalid"

    with pytest.raises(runtime_logs.RuntimeLogError) as status_error:
        runtime_logs.list_feed(status="unknown")
    assert status_error.value.code == "runtime_log_status_invalid"

    assert runtime_logs.list_feed(limit=0)["total"] <= 1
    assert runtime_logs.list_feed(limit=9999)["total"] <= 500

    response = CLIENT.get("/api/runtime-logs", params={"category": "unknown"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "runtime_log_category_invalid"


def test_chat_detail_groups_single_and_cie_multi_input_turns():
    _session("runtime-cie-session")
    _message("runtime-cie-u0", "runtime-cie-session", "user", "上一轮输入", 300.0)
    _message("runtime-cie-a0", "runtime-cie-session", "assistant", "上一轮回复", 301.0)
    _message("runtime-cie-u1", "runtime-cie-session", "user", "第一条", 302.0)
    _message("runtime-cie-u2", "runtime-cie-session", "user", "第二条", 302.0)
    _message("runtime-cie-u3", "runtime-cie-session", "user", "第三条", 303.0)
    _message(
        "runtime-cie-a1", "runtime-cie-session", "assistant", "最终回复", 304.0,
        model="deepseek-v4-flash",
    )

    detail = runtime_logs.get_detail("chat:runtime-cie-a1")
    assert detail["representation"] == "persisted-turn-final-v1"
    assert [item["message_id"] for item in detail["inputs"]] == [
        "runtime-cie-u1", "runtime-cie-u2", "runtime-cie-u3",
    ]
    assert [item["content"] for item in detail["inputs"]] == ["第一条", "第二条", "第三条"]
    assert detail["assistant"]["content"] == "最终回复"
    assert "上一轮输入" not in json.dumps(detail, ensure_ascii=False)

    response = CLIENT.get("/api/runtime-logs/chat%3Aruntime-cie-a1")
    assert response.status_code == 200
    assert response.json()["assistant"]["message_id"] == "runtime-cie-a1"


def test_chat_detail_allows_proactive_assistant_without_inputs():
    _session("runtime-proactive-session")
    _message(
        "runtime-proactive-assistant", "runtime-proactive-session", "assistant", "主动问候",
        401.0, proactive_delivery_id="runtime-proactive-delivery",
    )

    detail = runtime_logs.get_detail("chat:runtime-proactive-assistant")
    assert detail["inputs"] == []
    assert detail["assistant"]["content"] == "主动问候"


def test_chat_detail_returns_not_found_for_invalid_or_deleted_events():
    for event_id in ("bad", "tool:anything", "chat:missing"):
        with pytest.raises(runtime_logs.RuntimeLogNotFound):
            runtime_logs.get_detail(event_id)

    response = CLIENT.get("/api/runtime-logs/bad")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "runtime_log_not_found"

    _session("runtime-delete-session")
    _message("runtime-delete-a", "runtime-delete-session", "assistant", "即将删除", 500.0)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE id=?", ("runtime-delete-session",))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(runtime_logs.RuntimeLogNotFound):
        runtime_logs.get_detail("chat:runtime-delete-a")


def test_optional_tables_degrade_only_when_the_table_is_missing():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert runtime_logs._optional_rows(conn, "SELECT id FROM absent_table LIMIT ?", 5) == []
    conn.execute("CREATE TABLE present_table(id TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        runtime_logs._optional_rows(conn, "SELECT missing_column FROM present_table LIMIT ?", 5)
    conn.close()


def test_runtime_reads_are_body_scoped_and_do_not_mutate_authoritative_tables():
    _session("runtime-readonly-session")
    _message("runtime-readonly-u", "runtime-readonly-session", "user", "允许展示的输入", 600.0)
    _message("runtime-readonly-a", "runtime-readonly-session", "assistant", "允许展示的回复", 601.0)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO providers(id,name,base_url,api_key,models,enabled,sort) VALUES(?,?,?,?,?,?,?)",
            ("runtime-secret-provider", "secret", "https://example.invalid", "sk-runtime-secret", "[]", 0, 0),
        )
        conn.execute(
            "INSERT INTO memory_fragments(id,layer,content,tags,source,confidence,sensitivity,status,enabled,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("runtime-secret-memory", "L1", "memory-secret-body", "", "manual", 1.0, "normal", "active", 1, 600.0, 600.0),
        )
        before = {
            table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in ("messages", "tool_logs", "memory_fragments", "providers")
        }
        conn.commit()
    finally:
        conn.close()

    rendered = json.dumps({
        "feed": runtime_logs.list_feed(limit=100),
        "detail": runtime_logs.get_detail("chat:runtime-readonly-a"),
    }, ensure_ascii=False)
    assert "允许展示的输入" in rendered and "允许展示的回复" in rendered
    for forbidden in ("sk-runtime-secret", "memory-secret-body", "chain_of_thought"):
        assert forbidden not in rendered
    assert '"system_prompt"' not in json.dumps(
        runtime_logs.get_detail("chat:runtime-readonly-a"), ensure_ascii=False,
    )

    conn = db.connect()
    try:
        after = {
            table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in ("messages", "tool_logs", "memory_fragments", "providers")
        }
    finally:
        conn.close()
    assert after == before

"""后端核心 API 冒烟测试（需求 11.2 工程验收）。"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# 用临时库，避免污染开发数据
os.environ["XIADIE_DATA_DIR"] = tempfile.mkdtemp(prefix="xiadie-test-")
TEST_API_TOKEN = "test-token-with-at-least-thirty-two-bytes"
os.environ["XIADIE_API_TOKEN"] = TEST_API_TOKEN

from app.main import app  # noqa: E402

client = TestClient(app, headers={"X-Xiadie-Token": TEST_API_TOKEN})


def test_health():
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_local_api_requires_correct_token():
    untrusted = TestClient(app)
    assert untrusted.get("/api/providers").status_code == 401
    assert untrusted.get(
        "/api/providers", headers={"X-Xiadie-Token": "wrong-token"}
    ).status_code == 401
    assert untrusted.get(
        "/api/providers", headers={"X-Xiadie-Token": TEST_API_TOKEN}
    ).status_code == 200


def test_explicit_browser_dev_mode_is_origin_limited(monkeypatch):
    monkeypatch.delenv("XIADIE_API_TOKEN")
    monkeypatch.setenv("XIADIE_DEV_MODE", "1")
    browser = TestClient(app)
    assert browser.get(
        "/api/providers", headers={"Origin": "http://127.0.0.1:5173"}
    ).status_code == 200
    assert browser.get(
        "/api/providers", headers={"Origin": "https://example.com"}
    ).status_code == 401
    assert browser.get("/api/providers").status_code == 401


def test_cors_only_allows_known_local_origins():
    browser = TestClient(app)
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-Xiadie-Token",
    }
    allowed = browser.options(
        "/api/providers",
        headers={"Origin": "http://127.0.0.1:5173", **preflight},
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    denied = browser.options(
        "/api/providers", headers={"Origin": "https://example.com", **preflight}
    )
    assert denied.status_code == 400


def test_default_providers_seeded():
    r = client.get("/api/providers")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    # 需求 MODEL-001 列出的供应商都应存在
    for pid in ("mock", "deepseek", "openai", "glm", "qwen", "kimi",
                "openrouter", "siliconflow", "ollama", "custom"):
        assert pid in ids


def test_api_key_not_leaked():
    # 设置一个 key，确认列表接口不明文回传
    client.patch("/api/providers/deepseek", json={"api_key": "sk-secret-123"})
    r = client.get("/api/providers")
    for p in r.json():
        assert "api_key" not in p
        if p["id"] == "deepseek":
            assert p["has_key"] is True


def test_session_and_chat_flow():
    s = client.post("/api/sessions", json={}).json()
    sid = s["id"]
    # mock 供应商默认启用，聊天应能流式返回并落库
    with client.stream("POST", "/api/chat",
                       json={"session_id": sid, "content": "你好遐蝶"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: meta" in body
    assert "event: done" in body
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]
    assert msgs[0]["content"] == "你好遐蝶"
    assert len(msgs[1]["content"]) > 0
    # 首条消息应成为会话标题
    assert client.get("/api/sessions").json()[0]["title"].startswith("你好")


def test_memory_crud_and_toggle():
    m = client.post("/api/memories",
                    json={"layer": "L0", "content": "用户偏好中文"}).json()
    assert m["layer"] == "L0"
    mid = m["id"]
    client.patch(f"/api/memories/{mid}", json={"enabled": False})
    got = [x for x in client.get("/api/memories").json() if x["id"] == mid][0]
    assert got["enabled"] is False
    client.delete(f"/api/memories/{mid}")
    assert all(x["id"] != mid for x in client.get("/api/memories").json())


def test_auto_memory_extraction():
    s = client.post("/api/sessions", json={}).json()
    before = len(client.get("/api/memories").json())
    with client.stream("POST", "/api/chat",
                       json={"session_id": s["id"], "content": "记住我正在做遐蝶 Agent 项目"}) as resp:
        "".join(resp.iter_text())
    after = client.get("/api/memories").json()
    assert len(after) == before + 1
    assert any(x["source"] == "auto" for x in after)


def test_task_flow_from_chat():
    s = client.post("/api/sessions", json={}).json()
    t = client.post("/api/tasks",
                    json={"title": "继续改 UI", "source_session_id": s["id"]}).json()
    assert t["source"] == "chat"
    tid = t["id"]
    client.patch(f"/api/tasks/{tid}", json={"status": "done"})
    todo = client.get("/api/tasks", params={"today": True}).json()
    assert all(x["id"] != tid for x in todo)  # done 不在今日待办


def test_model_selection():
    r = client.post("/api/current-model",
                    json={"provider_id": "mock", "model": "xiadie-mock"})
    assert r.status_code == 200
    cur = client.get("/api/current-model").json()
    assert cur["provider_id"] == "mock"
    assert "stream" in cur["capabilities"]


def test_regenerate_does_not_duplicate_assistant():
    # 回归：重新生成应替换最后一条 assistant 回复，而非追加重复
    s = client.post("/api/sessions", json={}).json()
    sid = s["id"]
    with client.stream("POST", "/api/chat",
                       json={"session_id": sid, "content": "第一次提问"}) as r:
        "".join(r.iter_text())
    assert [m["role"] for m in client.get(f"/api/sessions/{sid}/messages").json()] == [
        "user", "assistant"]
    # 重新生成
    with client.stream("POST", "/api/chat",
                       json={"session_id": sid, "content": "第一次提问", "regenerate": True}) as r:
        "".join(r.iter_text())
    roles = [m["role"] for m in client.get(f"/api/sessions/{sid}/messages").json()]
    assert roles == ["user", "assistant"], f"重新生成不应堆积重复消息，实际: {roles}"


def test_reserved_setting_key_protected():
    # 回归：通用 settings 端点不能写坏 current_model
    r = client.put("/api/settings/current_model", json={"value": "garbage"})
    assert r.status_code == 400
    # 聊天仍可用（current_model 未被污染）
    cur = client.get("/api/current-model")
    assert cur.status_code == 200


def test_invalid_enum_values_return_400():
    # 回归：非法 layer / status 返回 400 而非 500
    m = client.post("/api/memories", json={"layer": "L2", "content": "x"}).json()
    assert client.patch(f"/api/memories/{m['id']}", json={"layer": "L9"}).status_code == 400
    t = client.post("/api/tasks", json={"title": "y"}).json()
    assert client.patch(f"/api/tasks/{t['id']}", json={"status": "bogus"}).status_code == 400

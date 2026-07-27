"""后端核心 API 冒烟测试（需求 11.2 工程验收）。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

# conftest.py 在任何 app 模块导入前建立临时库，避免测试收集顺序污染开发数据。
TEST_API_TOKEN = "test-token-with-at-least-thirty-two-bytes"

from app import db  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app, headers={"X-Xiadie-Token": TEST_API_TOKEN})


def test_health():
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_long_term_memory_defaults_on_but_preserves_explicit_user_off():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM settings WHERE key='memory_enabled'")
        conn.commit()
    finally:
        conn.close()

    db.init_db()
    assert client.get("/api/settings/memory_enabled").json()["value"] == "1"

    assert client.put(
        "/api/settings/memory_enabled", json={"value": "0"},
    ).json()["value"] == "0"
    db.init_db()
    assert client.get("/api/settings/memory_enabled").json()["value"] == "0"
    assert client.put(
        "/api/settings/memory_enabled", json={"value": "2"},
    ).status_code == 400

    # 不把本测试中的主动关闭泄漏给后续聊天/记忆测试。
    client.put("/api/settings/memory_enabled", json={"value": "1"})


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


def test_discover_models_uses_unsaved_config_without_leaking_key(monkeypatch):
    captured = {}

    async def fake_discover(base_url, api_key):
        captured.update(base_url=base_url, api_key=api_key)
        return {"ok": True, "models": ["model-a", "model-b"], "message": "发现 2 个可用模型"}

    monkeypatch.setattr("app.llm.discover_models", fake_discover)
    response = client.post(
        "/api/providers/discover-models",
        json={
            "provider_id": "custom",
            "base_url": "https://example.com/v1/",
            "api_key": "temporary-secret",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "models": ["model-a", "model-b"],
        "message": "发现 2 个可用模型",
    }
    assert captured == {
        "base_url": "https://example.com/v1/",
        "api_key": "temporary-secret",
    }
    assert "temporary-secret" not in response.text


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


def test_persona_core_and_lore_are_separated_and_injected():
    from app import lore
    from app.persona import PERSONA_PROMPT, build_system_prompt

    assert "你就是遐蝶本人" in PERSONA_PROMPT
    assert "不要默认用户是开拓者" in PERSONA_PROMPT
    assert "玻吕茜亚无法忍受永恒孤独" not in PERSONA_PROMPT

    related = lore.retrieve_lore("说说你和玻吕茜亚妹妹的过去")
    unrelated = lore.retrieve_lore("请帮我计算 12 乘以 8")
    assert "起源与死亡权能" in related
    assert "玻吕茜亚" in related
    assert unrelated == ""

    prompt = build_system_prompt("- 用户喜欢安静", "语气平静", related)
    assert "与当前话题相关的角色设定" in prompt
    assert "你与用户的相处记忆" in prompt
    assert "用户喜欢安静" in prompt


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


def test_auto_memory_creates_traceable_candidate_then_accepts():
    s = client.post("/api/sessions", json={}).json()
    before = len(client.get("/api/memories").json())
    with client.stream("POST", "/api/chat",
                       json={"session_id": s["id"], "content": "记住我正在做遐蝶 Agent 项目"}) as resp:
        body = "".join(resp.iter_text())
    assert "memory_candidate" in body
    # 自动识别只能提出候选，不能静默写入正式记忆。
    assert len(client.get("/api/memories").json()) == before
    candidates = client.get("/api/memory-candidates").json()
    candidate = next(x for x in candidates if x["source_session_id"] == s["id"])
    assert candidate["status"] == "pending"
    assert candidate["source_message_id"]

    result = client.post(
        f"/api/memory-candidates/{candidate['id']}/accept",
        json={"content": "用户正在开发遐蝶 Agent", "layer": "L1"},
    ).json()
    assert result["candidate"]["status"] == "accepted"
    assert result["memory"]["content"] == "用户正在开发遐蝶 Agent"
    assert result["memory"]["source_message_id"] == candidate["source_message_id"]
    assert len(client.get("/api/memories").json()) == before + 1
    events = client.get(f"/api/memory-events/candidate/{candidate['id']}").json()
    assert [event["action"] for event in events] == ["proposed", "accepted"]


def test_sensitive_memory_never_enters_chat_digest():
    from app import memory

    item = memory.create_memory(
        "L1",
        "用户的秘密花园通行口令只用于敏感测试",
        sensitivity="sensitive",
    )
    digest, recalled = memory.build_digest("秘密花园通行口令")
    assert item["sensitivity"] == "sensitive"
    assert digest == ""
    assert recalled == []


def test_memory_candidate_can_be_rejected_and_not_reprocessed():
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "我喜欢安静的工作环境"},
    ) as response:
        "".join(response.iter_text())
    candidate = next(
        item for item in client.get("/api/memory-candidates").json()
        if item["source_session_id"] == session["id"]
    )
    rejected = client.post(
        f"/api/memory-candidates/{candidate['id']}/reject",
        json={"note": "不需要长期保存"},
    ).json()
    assert rejected["status"] == "rejected"
    assert rejected["resolution_note"] == "不需要长期保存"
    assert client.post(
        f"/api/memory-candidates/{candidate['id']}/accept", json={}
    ).status_code == 409


def test_memory_source_becomes_unavailable_after_session_deleted():
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "记住我喜欢观察星星"},
    ) as response:
        "".join(response.iter_text())
    candidate = next(
        item for item in client.get("/api/memory-candidates").json()
        if item["source_session_id"] == session["id"]
    )
    assert candidate["source_available"] is True
    assert candidate["source_session_title"]
    client.delete(f"/api/sessions/{session['id']}")
    after = client.get(f"/api/memory-candidates/{candidate['id']}").json()
    assert after["source_available"] is False
    assert after["source_session_id"] is None
    assert after["source_message_id"] is None


def test_fts_retrieval_only_returns_relevant_active_enabled_memories():
    from app import db, memory

    relevant = client.post(
        "/api/memories", json={"layer": "L1", "content": "用户的猫叫月光，喜欢趴在窗边"}
    ).json()
    unrelated = client.post(
        "/api/memories", json={"layer": "L2", "content": "用户曾经学习过水彩画"}
    ).json()

    found = memory.search_memories("那只猫叫月光吗")
    assert relevant["id"] in {item["id"] for item in found}
    assert unrelated["id"] not in {item["id"] for item in found}

    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "那只猫叫月光吗"},
    ) as response:
        stream_body = "".join(response.iter_text())
    assert '"memory_count": 1' in stream_body
    assert relevant["id"] in stream_body

    client.patch(f"/api/memories/{relevant['id']}", json={"enabled": False})
    assert relevant["id"] not in {
        item["id"] for item in memory.search_memories("猫叫月光")
    }
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET enabled=1,status='frozen',frozen_at=? WHERE id=?",
            (db.now(), relevant["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert relevant["id"] not in {
        item["id"] for item in memory.search_memories("猫叫月光")
    }


def test_entity_auto_link_alias_update_unlink_and_merge():
    first = client.post(
        "/api/memories", json={"layer": "L1", "content": "我的猫叫星澜，喜欢趴在窗边"}
    ).json()
    entities_list = client.get("/api/entities").json()
    moon = next(entity for entity in entities_list if entity["name"] == "星澜")
    assert moon["entity_type"] == "pet"
    assert moon["fragment_count"] == 1

    second = client.post(
        "/api/memories", json={"layer": "L2", "content": "星澜今天很安静"}
    ).json()
    detail = client.get(f"/api/entities/{moon['id']}").json()
    assert {fragment["id"] for fragment in detail["fragments"]} == {first["id"], second["id"]}

    updated = client.patch(
        f"/api/entities/{moon['id']}",
        json={"aliases": ["小星澜"], "summary": "用户的猫", "current_status": "喜欢窗边"},
    ).json()
    assert updated["aliases"] == ["小星澜"]
    assert updated["summary"] == "用户的猫"

    unlinked = client.delete(f"/api/entities/{moon['id']}/links/{second['id']}").json()
    assert {fragment["id"] for fragment in unlinked["fragments"]} == {first["id"]}

    duplicate = client.post(
        "/api/entities",
        json={"name": "星澜猫", "entity_type": "pet", "aliases": ["Starlight"]},
    ).json()
    merged = client.post(
        f"/api/entities/{moon['id']}/merge",
        json={"source_entity_id": duplicate["id"]},
    ).json()
    assert "星澜猫" in merged["aliases"]
    assert client.get(f"/api/entities/{duplicate['id']}").status_code == 404
    events = client.get(f"/api/memory-events/entity/{moon['id']}").json()
    assert {event["action"] for event in events} >= {
        "created", "fragment_linked", "updated", "fragment_unlinked", "merged_in"
    }


def test_new_entity_and_alias_backfill_existing_fragments():
    old_memory = client.post(
        "/api/memories", json={"layer": "L2", "content": "今天继续整理北辰计划的资料"}
    ).json()
    entity = client.post(
        "/api/entities", json={"name": "北辰计划", "entity_type": "project"}
    ).json()
    assert old_memory["id"] in {fragment["id"] for fragment in entity["fragments"]}

    alias_memory = client.post(
        "/api/memories", json={"layer": "L2", "content": "Project Polaris 已进入下一阶段"}
    ).json()
    updated = client.patch(
        f"/api/entities/{entity['id']}", json={"aliases": ["Project Polaris"]}
    ).json()
    assert alias_memory["id"] in {fragment["id"] for fragment in updated["fragments"]}


def test_archived_entity_name_can_be_created_again():
    memory_item = client.post(
        "/api/memories", json={"layer": "L2", "content": "归航项目正在整理需求"}
    ).json()
    original = client.post(
        "/api/entities", json={"name": "归航项目", "entity_type": "project"}
    ).json()
    assert client.delete(f"/api/entities/{original['id']}").status_code == 200
    assert client.get(f"/api/entities/{original['id']}").status_code == 404

    recreated_response = client.post(
        "/api/entities", json={"name": "归航项目", "entity_type": "project"}
    )
    assert recreated_response.status_code == 200
    recreated = recreated_response.json()
    assert recreated["id"] != original["id"]
    assert recreated["status"] == "active"
    assert memory_item["id"] in {fragment["id"] for fragment in recreated["fragments"]}


def test_episode_candidate_generation_acceptance_and_audit():
    first = client.post(
        "/api/memories",
        json={"layer": "L1", "content": "项目名为晨曦计划，完成模型配置界面"},
    ).json()
    second = client.post(
        "/api/memories",
        json={"layer": "L1", "content": "晨曦计划完成模型列表自动获取"},
    ).json()
    generated = client.post("/api/episode-candidates/generate")
    assert generated.status_code == 200
    assert generated.json()["queued"] is True
    from app import episode_consolidator
    asyncio.run(episode_consolidator.process_due(limit=20))
    episode = next(
        client.get(f"/api/episodes/{item['id']}").json()
        for item in client.get("/api/episodes").json()
        if {fragment["id"] for fragment in client.get(
            f"/api/episodes/{item['id']}"
        ).json()["fragments"]} == {first["id"], second["id"]}
    )
    assert episode["source"] == "consolidator_auto"
    assert episode["application_version"] == "episode-application-v1"
    assert episode["confidence"] >= 0.68
    assert {fragment["id"] for fragment in episode["fragments"]} == {first["id"], second["id"]}
    assert any(entity["name"] == "晨曦计划" for entity in episode["entities"])
    candidate = next(
        item for item in client.get("/api/episode-candidates?status=accepted").json()
        if item["resolved_episode_id"] == episode["id"]
    )
    assert client.post(
        f"/api/episode-candidates/{candidate['id']}/accept", json={}
    ).status_code == 409
    events = client.get(f"/api/memory-events/episode/{episode['id']}").json()
    assert [event["action"] for event in events] == ["created"]
    corrected = client.post(
        f"/api/episodes/{episode['id']}/correct",
        json={
            "title": "晨曦计划的两次模型准备",
            "summary": "用户纠正：这是两次连续的模型准备。",
            "significance": 6,
            "note": "用户明确补充了经历范围",
        },
    )
    assert corrected.status_code == 200
    corrected_episode = corrected.json()
    assert corrected_episode["summary_status"] == "user_edited"
    assert corrected_episode["summary_protocol_version"] == "manual-v1"
    assert corrected_episode["summary_evidence_fragment_ids"] == []
    assert corrected_episode["correction_note"] == "用户明确补充了经历范围"
    assert corrected_episode["corrected_at"] is not None
    assert corrected_episode["source_hash"] == episode["source_hash"]
    assert corrected_episode["source_fragment_ids"] == episode["source_fragment_ids"]
    corrected_events = client.get(f"/api/memory-events/episode/{episode['id']}").json()
    assert [
        event["action"] for event in corrected_events
    ] == ["created", "corrected"]
    assert corrected_events[-1]["source"] == "user_correction"
    assert corrected_events[-1]["after"]["correction_note"] == "用户明确补充了经历范围"
    assert client.post(
        f"/api/episodes/{episode['id']}/correct", json={"title": ""}
    ).status_code == 400


def test_episode_candidate_rejects_and_requires_two_fragments():
    first = client.post(
        "/api/memories", json={"layer": "L1", "content": "项目名为晚风计划，开始整理文档"}
    ).json()
    second = client.post(
        "/api/memories", json={"layer": "L1", "content": "晚风计划继续整理文档目录"}
    ).json()
    from app import episodes
    episodes.generate_candidates()
    candidate = next(
        item for item in client.get("/api/episode-candidates").json()
        if {fragment["id"] for fragment in item["fragments"]} == {first["id"], second["id"]}
    )
    assert client.post(
        f"/api/episode-candidates/{candidate['id']}/accept",
        json={"fragment_ids": [first["id"]]},
    ).status_code == 400
    rejected = client.post(
        f"/api/episode-candidates/{candidate['id']}/reject",
        json={"note": "它们不是同一次经历"},
    ).json()
    assert rejected["status"] == "rejected"
    assert rejected["resolution_note"] == "它们不是同一次经历"
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


def test_companion_state_changes_after_successful_chat_and_resets():
    initial = client.post("/api/companion-state/reset").json()
    assert set(("affect", "relationship", "derived", "signals")) <= set(initial)
    assert initial["derived"]["style_guidance"]
    assert initial["derived"]["cluster"]

    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST",
        "/api/chat",
        json={"session_id": session["id"], "content": "谢谢你，继续专注完成这个功能"},
    ) as response:
        assert response.status_code == 200
        stream_body = "".join(response.iter_text())
    assert '"companion_state": {' in stream_body
    assert '"guardedness_band":' in stream_body

    changed = client.get("/api/companion-state").json()
    assert changed["affect"]["contact_need"] < initial["affect"]["contact_need"]
    assert changed["affect"]["guardedness"] < initial["affect"]["guardedness"]
    assert changed["affect"]["immersion"] > initial["affect"]["immersion"]
    # R2: fallback never changes relationship; only grounded cognition may do so later.
    assert changed["relationship"]["bond"] == initial["relationship"]["bond"]
    assert changed["relationship"]["interaction_count"] == 1
    for key in ("contact_need", "guardedness", "immersion"):
        assert 0 <= changed["affect"][key] <= 1
    for key in ("valence", "arousal"):
        assert -1 <= changed["affect"][key] <= 1

    events = client.get("/api/companion-state/events").json()
    assert events[0]["event_type"] == "interaction"
    assert events[0]["source_message_id"]

    reset = client.post("/api/companion-state/reset").json()
    assert reset["affect"]["contact_need"] == initial["affect"]["contact_need"]
    assert reset["relationship"]["bond"] == initial["relationship"]["bond"]


def test_affect_timeline_is_deterministic_and_handles_one_night_and_seven_days():
    from app.affect import engine, repository

    initial = {
        "affect": dict(engine.DEFAULT_AFFECT),
        "relationship": dict(engine.DEFAULT_RELATIONSHIP),
    }
    first = engine.advance(initial, 8 * 60)
    second = engine.advance(initial, 8 * 60)
    assert first == second
    # 参数基线断言依赖 CONTACT_NEED_RATE_PER_MINUTE=0.00012；调参时必须同步复核整条时间线。
    assert 0.10 < first["affect"]["contact_need"] < 0.13
    assert first["affect"]["immersion"] < initial["affect"]["immersion"]
    replied = engine.apply_fallback_interaction(first, "早上好，我回来了")
    assert replied["affect"]["contact_need"] == 0.03
    assert replied["relationship"]["bond"] == first["relationship"]["bond"]

    day = engine.advance(initial, 24 * 60)
    assert 0.22 < day["affect"]["contact_need"] < 0.25
    assert engine.signals(day) == []

    three_days = engine.advance(initial, 3 * 24 * 60)
    assert 0.70 < three_days["affect"]["contact_need"] < 0.75
    assert engine.signals(three_days)[0]["action"] == "find_activity"

    week = engine.advance(initial, 7 * 24 * 60)
    assert 0 <= week["affect"]["contact_need"] <= 1
    assert -1 <= week["affect"]["valence"] <= 1
    assert -1 <= week["affect"]["arousal"] <= 1
    assert -0.25 <= week["affect"]["guardedness_transient"] <= 0.25

    repository.reset()
    ticked = client.post("/api/companion-state/tick", json={"minutes": 480}).json()
    assert ticked["affect"]["contact_need"] > 0.05
    assert client.post("/api/companion-state/tick", json={"minutes": 0}).status_code == 422


def test_affect_math_boundaries_and_signal_thresholds():
    from app.affect import engine

    expected_factors = {
        -1.0: 0.50,
        -0.60: 0.90,
        -0.30: 1.05,
        0.0: 0.90,
        0.50: 0.85,
        1.0: 0.80,
    }
    for value, expected in expected_factors.items():
        assert engine.valence_factor(value) == pytest.approx(expected)
    for boundary in (-0.60, -0.30, 0.0):
        left = engine.valence_factor(boundary - 1e-7)
        right = engine.valence_factor(boundary + 1e-7)
        assert abs(left - right) < 1e-5

    def snapshot(contact_need: float, *, trust: float = 0.25) -> dict:
        affect = dict(engine.DEFAULT_AFFECT)
        affect["contact_need"] = contact_need
        relationship = dict(engine.DEFAULT_RELATIONSHIP)
        relationship["trust"] = trust
        return {"affect": affect, "relationship": relationship}

    assert engine.signals(snapshot(0.2999)) == []
    assert engine.signals(snapshot(0.30))[0]["action"] == "observation"
    assert engine.signals(snapshot(0.5499))[0]["action"] == "observation"
    assert engine.signals(snapshot(0.55))[0]["action"] == "find_activity"
    assert engine.signals(snapshot(0.55, trust=1.0))[0]["action"] == "consider_contact"
    assert engine.signals(snapshot(0.75))[0]["action"] == "contact"


def test_reply_reduces_contact_need_proportionally_and_rebases_latest_state():
    from app import companion_state
    from app.affect import engine

    low = {
        "affect": {**engine.DEFAULT_AFFECT, "contact_need": 0.05},
        "relationship": dict(engine.DEFAULT_RELATIONSHIP),
    }
    high = {
        "affect": {**engine.DEFAULT_AFFECT, "contact_need": 0.80},
        "relationship": dict(engine.DEFAULT_RELATIONSHIP),
    }
    low_reply = engine.apply_fallback_interaction(low, "我回来了")
    high_reply = engine.apply_fallback_interaction(high, "我回来了")
    assert low_reply["affect"]["contact_need"] == pytest.approx(0.03)
    assert high_reply["affect"]["contact_need"] == pytest.approx(0.16)
    assert high_reply["relationship"]["bond"] == high["relationship"]["bond"]
    assert low_reply["relationship"]["bond"] == low["relationship"]["bond"]

    companion_state.reset_state()
    stale_preview = companion_state.preview_interaction("较早生成的预览")
    advanced = companion_state.tick(3 * 24 * 60)
    saved = companion_state.commit_interaction("流式回复成功后提交")
    assert advanced["affect"]["contact_need"] > 0.70
    assert saved["affect"]["contact_need"] > stale_preview["affect"]["contact_need"]
    assert saved["affect"]["contact_need"] == pytest.approx(
        advanced["affect"]["contact_need"] * 0.20,
        rel=0.02,
    )


def test_generation_preview_advances_time_without_writing_state_or_event():
    from app import companion_state, db

    companion_state.reset_state()
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE affect_state SET last_tick_at = ? WHERE id = 1",
            (db.now() - 24 * 60 * 60,),
        )
        conn.commit()
        stored_before = conn.execute(
            "SELECT contact_need FROM affect_state WHERE id = 1"
        ).fetchone()["contact_need"]
        events_before = conn.execute("SELECT COUNT(*) c FROM affect_events").fetchone()["c"]
    finally:
        conn.close()

    preview = companion_state.get_state(persist_advance=False)
    assert preview["affect"]["contact_need"] > stored_before

    conn = db.connect()
    try:
        stored_after = conn.execute(
            "SELECT contact_need FROM affect_state WHERE id = 1"
        ).fetchone()["contact_need"]
        events_after = conn.execute("SELECT COUNT(*) c FROM affect_events").fetchone()["c"]
    finally:
        conn.close()
    assert stored_after == stored_before
    assert events_after == events_before


def test_observer_failure_does_not_break_chat_done_event(monkeypatch):
    from app import db, llm
    from app import main as main_module
    from app.proactive import cognition_service

    provider = {
        "id": "test-observer-provider",
        "base_url": "https://example.invalid/v1",
        "api_key": "not-logged",
    }
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO providers(id,name,base_url,api_key,models,enabled,sort)"
            " VALUES(?,?,?,?,?,1,99)",
            (provider["id"], "失败隔离测试", provider["base_url"], provider["api_key"], '["test-model"]'),
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(main_module, "_current_model", lambda: (provider, "test-model"))

    async def fake_stream(*_args, **_kwargs):
        yield "观察失败也不影响这条回复"

    async def failing_observer(*_args, **_kwargs):
        raise llm.LLMError("观察失败中的敏感详情", "不要记录")

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    monkeypatch.setattr(llm, "complete_json", failing_observer)
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "继续测试失败隔离"},
    ) as response:
        body = "".join(response.iter_text())
    assert "event: done" in body
    assert '"status": "queued"' in body
    assert "观察失败中的敏感详情" not in body

    # 聊天热路径只入队；模型观察尚未执行，因此不会延迟 done。
    queued_run = next(
        item for item in client.get("/api/companion-state/cognition-runs").json()
        if item["source_session_id"] == session["id"]
    )
    assert queued_run["attempt_count"] == 0
    for _ in range(20):
        asyncio.run(cognition_service.process_due(limit=20))
        if get_run := next(
            (item for item in cognition_service.list_runs(200) if item["id"] == queued_run["id"]),
            None,
        ):
            if get_run["status"] != "queued":
                break
    runs = client.get("/api/companion-state/cognition-runs").json()
    run = next(item for item in runs if item["source_session_id"] == session["id"])
    assert run["status"] == "recovery_pending"
    assert run["result"] is None


def test_proactive_runtime_hook_failures_do_not_break_chat_done_event(monkeypatch):
    from app import llm
    from app.proactive import orchestrator as proactive_orchestrator

    async def fake_stream(*_args, **_kwargs):
        yield "主聊天仍然正常完成"

    def fail_hook(*_args, **_kwargs):
        raise RuntimeError("proactive worker unavailable")

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    monkeypatch.setattr(proactive_orchestrator, "handle_user_message", fail_hook)
    monkeypatch.setattr(proactive_orchestrator, "enqueue_after_chat", fail_hook)
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "继续聊天"},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: done" in body and "主聊天仍然正常完成" in body


def test_system_resume_api_enables_a_fail_closed_guard():
    from app.proactive import settings as proactive_settings

    response = client.post("/api/proactive/runtime/system-resume")
    assert response.status_code == 200
    guard_until = response.json()["guard_until"]
    assert guard_until > db.now()
    assert "system_resume_guard" in proactive_settings.effective_policy().blocked_reasons
    db.set_setting("proactive_resume_guard_until", "0")


def test_observer_model_config_api_validates_dedicated_model():
    client.patch("/api/providers/deepseek", json={"enabled": True})
    current = client.put(
        "/api/companion-state/observer-model",
        json={"mode": "current", "provider_id": None, "model": None},
    )
    assert current.status_code == 200
    assert current.json()["mode"] == "current"

    dedicated = client.put(
        "/api/companion-state/observer-model",
        json={"mode": "dedicated", "provider_id": "deepseek", "model": "deepseek-chat"},
    )
    assert dedicated.status_code == 200
    assert client.get("/api/companion-state/observer-model").json() == {
        "mode": "dedicated", "provider_id": "deepseek", "model": "deepseek-chat",
    }
    invalid = client.put(
        "/api/companion-state/observer-model",
        json={"mode": "dedicated", "provider_id": "deepseek", "model": "missing"},
    )
    assert invalid.status_code == 400


def test_memory_observer_internal_api_is_read_only_and_validates_model():
    client.patch("/api/providers/deepseek", json={"enabled": True})
    current = client.put(
        "/api/memory-observer/model",
        json={"mode": "current", "provider_id": None, "model": None},
    )
    assert current.status_code == 200
    dedicated = client.put(
        "/api/memory-observer/model",
        json={"mode": "dedicated", "provider_id": "deepseek", "model": "deepseek-chat"},
    )
    assert dedicated.status_code == 200
    assert client.get("/api/memory-observer/model").json() == {
        "mode": "dedicated", "provider_id": "deepseek", "model": "deepseek-chat",
    }
    assert client.put(
        "/api/memory-observer/model",
        json={"mode": "dedicated", "provider_id": "mock", "model": "xiadie-mock"},
    ).status_code == 400
    before = client.get("/api/memory-observer/runs").json()
    after = client.get("/api/memory-observer/runs").json()
    assert after == before
    assert client.get("/api/memory-observer/runs/not-found/result").status_code == 404
    client.put(
        "/api/memory-observer/model",
        json={"mode": "current", "provider_id": None, "model": None},
    )


def test_memory_correction_has_distinct_audit_semantics():
    created = client.post(
        "/api/memories", json={"layer": "L2", "content": "用户喜欢清晨", "tags": ""}
    ).json()
    corrected = client.post(
        f"/api/memories/{created['id']}/correct",
        json={"content": "用户更喜欢安静的夜晚", "note": "用户明确纠正了时间偏好"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["content"] == "用户更喜欢安静的夜晚"
    events = client.get(f"/api/memory-events/fragment/{created['id']}").json()
    event = events[-1]
    assert event["action"] == "corrected" and event["source"] == "user_correction"
    assert event["after"]["correction_note"] == "用户明确纠正了时间偏好"


def test_real_memory_observer_path_does_not_create_legacy_candidate(monkeypatch):
    from app import db, llm, memory_observer_service

    async def fake_stream(*_args, **_kwargs):
        yield "我会记得。"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    monkeypatch.setattr(
        memory_observer_service, "enqueue_turn",
        lambda **_kwargs: {"id": "queued-memory-run", "status": "queued", "error_code": None},
    )
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "记住我喜欢安静的夜晚。"},
    ) as response:
        body = "".join(response.iter_text())
    assert "event: done" in body and '"memory_candidate": null' in body
    conn = db.connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_candidates WHERE source_session_id=?",
            (session["id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_legacy_fallback_failure_cannot_hide_successful_chat(monkeypatch):
    from app import llm, memory, memory_observer_service

    async def fake_stream(*_args, **_kwargs):
        yield "回复仍然成功。"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    monkeypatch.setattr(
        memory_observer_service, "enqueue_turn",
        lambda **_kwargs: {
            "status": "skipped", "error_code": "observer_model_unavailable"
        },
    )
    monkeypatch.setattr(
        memory, "maybe_create_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fallback db failed")),
    )
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "记住我喜欢安静的夜晚。"},
    ) as response:
        body = "".join(response.iter_text())
    assert "event: done" in body and "回复仍然成功" in body


def test_failed_regeneration_keeps_previous_reply(monkeypatch):
    from app import llm

    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat", json={"session_id": session["id"], "content": "生成一条旧回复"}
    ) as response:
        "".join(response.iter_text())
    before = client.get(f"/api/sessions/{session['id']}/messages").json()
    old_reply = next(item for item in before if item["role"] == "assistant")

    async def failing_stream(*_args, **_kwargs):
        raise llm.LLMError("测试失败", "保留旧回复")
        yield ""  # pragma: no cover - 保持 async generator 形态

    monkeypatch.setattr(llm, "stream_chat", failing_stream)
    with client.stream(
        "POST",
        "/api/chat",
        json={"session_id": session["id"], "content": "生成一条旧回复", "regenerate": True},
    ) as response:
        body = "".join(response.iter_text())
    assert "event: error" in body
    after = client.get(f"/api/sessions/{session['id']}/messages").json()
    assert any(item["id"] == old_reply["id"] for item in after)


def test_schema_migration_is_idempotent():
    from app import db

    db.init_db()
    db.init_db()
    conn = db.connect()
    try:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        assert version == "72"
        assert conn.execute("SELECT COUNT(*) c FROM companion_state").fetchone()["c"] <= 1
        assert conn.execute("SELECT COUNT(*) c FROM affect_state").fetchone()["c"] <= 1
        assert conn.execute("SELECT COUNT(*) c FROM relationship_state").fetchone()["c"] <= 1
        assert conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master"
            " WHERE type='table' AND name='affect_observer_runs'"
        ).fetchone()["c"] == 1
        run_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(memory_observer_runs)").fetchall()
        }
        assert {"latency_ms", "repair_attempted", "created_fragment_ids_json"} <= run_columns
        assert conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master"
            " WHERE type='table' AND name='memory_observer_runs'"
        ).fetchone()["c"] == 1
        consolidator_tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name IN ('episode_consolidator_runs','episode_consolidator_events')"
            ).fetchall()
        }
        assert consolidator_tables == {
            "episode_consolidator_runs", "episode_consolidator_events",
        }
        assert conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master"
            " WHERE type='table' AND name='episode_group_candidates'"
        ).fetchone()["c"] == 1
        episode_candidate_columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(memory_episode_candidates)"
            ).fetchall()
        }
        assert {
            "entity_score", "text_score", "time_score", "coherence_score",
            "score_details_json", "policy_version", "expires_at", "last_evaluated_at",
            "summary_status", "summary_protocol_version", "summary_provider_id",
            "summary_model", "summary_evidence_json", "summary_warnings_json",
            "summary_error_code", "summary_source_hash", "summary_prompt_tokens",
            "summary_completion_tokens", "summary_repair_attempted",
            "application_attempt_count", "application_error_code", "last_application_at",
        } <= episode_candidate_columns
        episode_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(memory_episodes)").fetchall()
        }
        assert {
            "grouping_fingerprint", "policy_version", "source_fragment_ids_json",
            "source_hash", "summary_status", "summary_protocol_version",
            "summary_provider_id", "summary_model", "summary_evidence_json",
            "application_version", "correction_note", "corrected_at",
        } <= episode_columns
        fragment_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(memory_fragments)").fetchall()
        }
        assert {
            "scope", "kind", "importance", "emotion", "inner_reason", "observer_version",
            "evidence_message_ids", "source_assistant_message_id", "idempotency_key",
            "last_recalled_at", "recall_count", "cooling_since", "frozen_at",
            "lifecycle_policy_version", "lifecycle_revision", "fts_indexed",
        } <= fragment_columns
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory_%'"
            ).fetchall()
        }
        assert {
            "memory_fragments", "memory_candidates", "memory_entities",
            "memory_fragment_entities", "memory_events", "memory_recall_events",
            "memory_lifecycle_events",
        } <= tables
        assert {
            "memory_sagas", "memory_saga_episodes", "memory_saga_entities",
            "memory_saga_events",
        } <= tables
        assert conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master"
            " WHERE type='table' AND name='saga_group_candidates'"
        ).fetchone()["c"] == 1
        assert conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master"
            " WHERE type='table' AND name='saga_candidate_summary_events'"
        ).fetchone()["c"] == 1
    finally:
        conn.close()

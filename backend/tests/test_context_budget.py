"""CTX.1 provider+model 能力、硬预算、聊天接入与无正文诊断测试。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import context_budget, db, llm
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


@pytest.fixture(autouse=True)
def clean_context_data():
    db.init_db()
    db.set_setting("current_model", '{"provider_id":"mock","model":"xiadie-mock"}')
    db.set_setting("model_context_capabilities", "{}")
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.commit()
    finally:
        conn.close()
    yield


def _capability(window: int, *, output: int = 1_024):
    return context_budget.resolve_model_context_capability(
        {"id": "custom"},
        "test-model",
        configured_profiles={
            "custom/test-model": {
                "context_window": window,
                "max_output_tokens": output,
                "default_output_tokens": output,
            },
        },
    )


def test_unknown_model_uses_conservative_unverified_fallback():
    capability = context_budget.resolve_model_context_capability(
        {"id": "unknown-provider"}, "unknown-model",
    )

    assert capability.effective_context_window == 4_096
    assert capability.source == "conservative_fallback"
    assert capability.verified is False


def test_application_ceiling_caps_larger_configured_window():
    capability = _capability(2_000_000, output=32_000)

    assert capability.declared_context_window == 2_000_000
    assert capability.effective_context_window == 1_000_000
    assert capability.max_output_tokens == 32_000


def test_128k_model_never_builds_a_request_over_its_own_window():
    history = []
    for index in range(100):
        history.extend([
            {"role": "user", "content": f"第 {index} 轮问题" + "长" * 800},
            {"role": "assistant", "content": f"第 {index} 轮回答" + "长" * 800},
        ])
    history.append({"role": "user", "content": "当前问题"})

    plan = context_budget.build_budget_plan(
        system_prompt="必要规则",
        history=history,
        capability=_capability(128_000, output=4_096),
    )

    assert plan.capability.effective_context_window == 128_000
    assert plan.reserved_total_tokens <= 128_000
    assert plan.trimmed_rounds > 0


def test_configured_one_million_window_can_use_a_large_safe_input():
    large_turn = "长期对话内容" * 70_000
    plan = context_budget.build_budget_plan(
        system_prompt="必要规则",
        history=[
            {"role": "user", "content": large_turn},
            {"role": "assistant", "content": "已理解"},
            {"role": "user", "content": "请继续"},
        ],
        capability=_capability(1_000_000, output=16_384),
    )

    assert plan.capability.effective_context_window == 1_000_000
    assert plan.history_tokens_kept > 400_000
    assert plan.output_reserve_tokens == 16_384
    assert plan.safety_margin_tokens > 0
    assert plan.reserved_total_tokens <= 1_000_000


@pytest.mark.parametrize("window", [4_096, 8_192, 128_000, 1_000_000])
@pytest.mark.parametrize("round_count", [0, 3, 40])
def test_every_successful_budget_plan_satisfies_the_hard_invariant(window, round_count):
    history = []
    for index in range(round_count):
        history.extend([
            {"role": "user", "content": f"问题 {index} " + "甲" * 200},
            {"role": "assistant", "content": f"回答 {index} " + "乙" * 200},
        ])
    history.append({"role": "user", "content": "当前消息"})

    plan = context_budget.build_budget_plan(
        system_prompt="系统规则",
        history=history,
        capability=_capability(window, output=min(2_048, window // 4)),
    )

    assert plan.estimated_input_tokens > 0
    assert plan.output_reserve_tokens > 0
    assert plan.safety_margin_tokens > 0
    assert plan.reserved_total_tokens <= plan.capability.effective_context_window


@pytest.mark.parametrize(
    "text",
    [
        "中文上下文预算",
        "English words and identifiers_123",
        "def hello(name: str) -> str:\n    return f'hi {name}'",
        '{"name":"遐蝶","enabled":true,"items":[1,2,3]}',
        "中文 mixed_with English 以及 JSON {\"ok\":true}",
    ],
)
def test_estimator_handles_supported_text_shapes(text):
    estimated = context_budget.estimate_tokens(text)

    assert isinstance(estimated, int) and estimated > 0


def test_budget_keeps_only_contiguous_complete_recent_turns():
    history = [
        {"role": "user", "content": "很早问题" * 600},
        {"role": "assistant", "content": "很早回答" * 600},
        {"role": "user", "content": "最近问题" * 20},
        {"role": "assistant", "content": "最近回答" * 20},
        {"role": "user", "content": "当前问题"},
    ]
    plan = context_budget.build_budget_plan(
        system_prompt="必要规则",
        history=history,
        capability=_capability(4_096, output=512),
    )

    roles = [message["role"] for message in plan.messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert plan.trimmed_messages == 2 and plan.trimmed_rounds == 1
    assert plan.reserved_total_tokens <= plan.capability.effective_context_window


def test_budget_discards_orphan_history_messages_instead_of_splitting_turns():
    plan = context_budget.build_budget_plan(
        system_prompt="必要规则",
        history=[
            {"role": "assistant", "content": "没有对应用户消息的旧回复"},
            {"role": "user", "content": "完整问题"},
            {"role": "assistant", "content": "完整回答"},
            {"role": "user", "content": "当前问题"},
        ],
        capability=_capability(8_192),
    )

    assert [message["role"] for message in plan.messages] == [
        "system", "user", "assistant", "user",
    ]
    assert all("没有对应" not in message["content"] for message in plan.messages)


def test_protected_system_region_fails_before_planning_history():
    with pytest.raises(context_budget.ContextBudgetError) as caught:
        context_budget.build_budget_plan(
            system_prompt="必要规则" * 2_000,
            history=[{"role": "user", "content": "你好"}],
            capability=_capability(4_096),
        )

    detail = caught.value.public_detail()
    assert detail["code"] == "context_protected_region_exceeds_window"
    assert "content" not in json.dumps(detail, ensure_ascii=False)


def test_budget_meta_contains_counts_but_no_message_content():
    plan = context_budget.build_budget_plan(
        system_prompt="不可泄露的系统正文",
        history=[{"role": "user", "content": "不可泄露的用户正文"}],
        capability=_capability(16_384),
        system_components={"existing_memory_digest": "不可泄露的记忆正文"},
    )
    encoded = json.dumps(plan.public_meta(), ensure_ascii=False)

    assert plan.output_reserve_tokens > 0
    assert plan.reserved_total_tokens <= plan.capability.effective_context_window
    assert "不可泄露" not in encoded
    assert plan.public_meta()["protocol_version"] == "context-budget-v1"


def test_chat_passes_output_reserve_and_emits_budget_meta(monkeypatch):
    captured = {}

    async def fake_stream(_provider, _model, messages, *, max_tokens):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        yield "陪你聊一会儿。"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    session = client.post("/api/sessions", json={}).json()
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "陪我聊聊天"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert captured["max_tokens"] > 0
    assert captured["messages"][-1] == {"role": "user", "content": "陪我聊聊天"}
    assert '"protocol_version": "context-budget-v1"' in body
    assert '"context_window_source": "verified"' in body


def test_regenerate_excludes_old_reply_and_keeps_complete_turns(monkeypatch):
    calls = []

    async def fake_stream(_provider, _model, messages, *, max_tokens):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        yield "旧回复" if len(calls) == 1 else "新回复"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    session = client.post("/api/sessions", json={}).json()
    client.post(
        "/api/chat",
        json={"session_id": session["id"], "content": "请回答这个问题"},
    )
    with client.stream(
        "POST", "/api/chat",
        json={
            "session_id": session["id"],
            "content": "请回答这个问题",
            "regenerate": True,
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    regenerated = calls[-1]["messages"]
    assert [message["role"] for message in regenerated] == ["system", "user"]
    assert all(message["content"] != "旧回复" for message in regenerated)
    assert calls[-1]["max_tokens"] > 0
    assert '"protocol_version": "context-budget-v1"' in body


def test_oversized_chat_fails_before_provider_and_rolls_back_message(monkeypatch):
    called = False

    async def fake_stream(*_args, **_kwargs):
        nonlocal called
        called = True
        yield "不应调用"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    session = client.post("/api/sessions", json={}).json()
    response = client.post(
        "/api/chat",
        json={"session_id": session["id"], "content": "长" * 10_000},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "context_protected_region_exceeds_window"
    assert called is False
    assert client.get(f"/api/sessions/{session['id']}/messages").json() == []


def test_openai_compatible_payload_includes_output_limit(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        async def aiter_lines(self):
            yield "data: [DONE]"

    class FakeStreamContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args):
            return False

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, _url, *, headers, json):
            captured.update(headers=headers, payload=json)
            return FakeStreamContext()

    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeClient)

    async def consume():
        return [chunk async for chunk in llm._stream_openai_compatible(
            "https://example.com/v1", "secret", "model", [], max_tokens=321,
        )]

    assert asyncio.run(consume()) == []
    assert captured["payload"]["max_tokens"] == 321
    assert captured["payload"]["stream"] is True

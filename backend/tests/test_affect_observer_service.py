import asyncio
import json

import pytest

from app import companion_state, db, llm
from app.affect import observer, observer_service


def create_context() -> dict:
    conn = db.connect()
    try:
        sid, uid, aid = db.new_id(), db.new_id(), db.new_id()
        now = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (sid, "观察测试", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (uid, sid, "user", "谢谢你认真帮我，我们继续。", now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
            (aid, sid, "assistant", "好，我们继续完成它。", "observer-test", now + 0.1),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "provider": {"id": "test", "base_url": "https://example.invalid/v1", "api_key": "secret"},
        "model": "observer-test",
        "session_id": sid,
        "user_message_id": uid,
        "assistant_message_id": aid,
        "user_text": "谢谢你认真帮我，我们继续。",
        "assistant_text": "好，我们继续完成它。",
        "current_state": companion_state.get_state(persist_advance=False),
        "persona_summary": "温柔、克制、有边界",
    }


def valid_output() -> str:
    return json.dumps({
        "protocol_version": observer.PROTOCOL_VERSION,
        "affect_delta": {
            "contact_need": -0.20, "guardedness": -0.02, "valence": 0.05,
            "arousal": 0.01, "immersion": 0.08,
        },
        "relationship_delta": {"bond": 0.002, "trust": 0.001},
        "user_status": "active",
        "trust_basis": "positive_reliability",
        "evidence": [{"speaker": "user", "quote": "谢谢你认真帮我"}],
        "reason": "用户表达感谢并继续合作",
        "confidence": 0.88,
    }, ensure_ascii=False)


def run(context: dict) -> dict:
    return asyncio.run(observer_service.observe_turn(**context))


def test_observer_service_saves_candidate_once_without_applying_state(monkeypatch):
    context = create_context()
    calls = []

    async def fake_complete(_provider, _model, _messages, *, max_tokens):
        calls.append(max_tokens)
        return {"text": valid_output(), "prompt_tokens": 321, "completion_tokens": 123}

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    state_before = companion_state.get_state(persist_advance=False)
    first = run(context)
    second = run(context)
    state_after = companion_state.get_state(persist_advance=False)

    assert first["status"] == second["status"] == "candidate"
    assert first["id"] == second["id"]
    assert calls == [llm.JSON_COMPLETION_MAX_TOKENS]
    assert state_after["relationship"]["bond"] == state_before["relationship"]["bond"]

    detail = next(item for item in observer_service.list_runs() if item["id"] == first["id"])
    assert detail["candidate"]["relationship_delta"]["bond"] == pytest.approx(0.002)
    assert detail["prompt_tokens"] == 321
    assert detail["completion_tokens"] == 123


def test_observer_claim_is_idempotent_under_concurrent_calls(monkeypatch):
    context = create_context()
    calls = 0

    async def fake_complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"text": valid_output(), "prompt_tokens": 10, "completion_tokens": 10}

    monkeypatch.setattr(llm, "complete_json", fake_complete)

    async def concurrent():
        return await asyncio.gather(
            observer_service.observe_turn(**context),
            observer_service.observe_turn(**context),
        )

    results = asyncio.run(concurrent())
    assert calls == 1
    assert results[0]["id"] == results[1]["id"]
    assert {item["status"] for item in results} <= {"running", "candidate"}


def test_invalid_output_enters_recovery_without_storing_raw_text(monkeypatch):
    context = create_context()
    sensitive_raw = "not-json secret-value-that-must-not-be-stored"

    async def fake_complete(*_args, **_kwargs):
        return {"text": sensitive_raw, "prompt_tokens": None, "completion_tokens": None}

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    result = run(context)
    assert result["status"] == "recovery_pending"
    assert result["error_code"] == "invalid_json"
    assert result["next_attempt_at"] > db.now()

    conn = db.connect()
    try:
        row = dict(conn.execute(
            "SELECT * FROM affect_observer_runs WHERE id=?", (result["id"],)
        ).fetchone())
        columns = {item[1] for item in conn.execute("PRAGMA table_info(affect_observer_runs)")}
    finally:
        conn.close()
    assert "raw_output" not in columns
    assert sensitive_raw not in json.dumps(row, ensure_ascii=False)
    assert row["candidate_json"] is None


def test_model_failure_isolated_and_mock_is_skipped(monkeypatch):
    failing_context = create_context()

    async def fail(*_args, **_kwargs):
        raise llm.LLMError("secret provider failure", "secret")

    monkeypatch.setattr(llm, "complete_json", fail)
    failed = run(failing_context)
    assert failed["status"] == "recovery_pending"
    assert failed["error_code"] == "model_call_failed"

    mock_context = create_context()
    mock_context["provider"] = {"id": "mock", "base_url": "", "api_key": ""}
    skipped = run(mock_context)
    assert skipped["status"] == "skipped"
    assert skipped["error_code"] == "observer_model_unavailable"


def test_oversized_input_skips_call_and_stale_running_recovers(monkeypatch):
    context = create_context()
    context["current_state"] = {"oversized": "x" * (observer_service.MAX_INPUT_CHARS + 1)}
    called = False

    async def should_not_call(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(llm, "complete_json", should_not_call)
    skipped = run(context)
    assert skipped["status"] == "skipped"
    assert skipped["error_code"] == "observer_input_too_large"
    assert called is False

    stale_context = create_context()
    row = observer_service._insert_initial(
        stale_context,
        idempotency_key=f"{observer.PROTOCOL_VERSION}:{stale_context['assistant_message_id']}",
        provider_id="test",
        status="running",
        error_code=None,
        input_chars=100,
    )
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE affect_observer_runs SET updated_at=? WHERE id=?",
            (db.now() - observer_service.RUNNING_STALE_SECONDS - 1, row["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert observer_service.recover_stale_runs() >= 1
    recovered = next(item for item in observer_service.list_runs() if item["id"] == row["id"])
    assert recovered["status"] == "recovery_pending"
    assert recovered["error_code"] == "observer_interrupted"


def test_complete_json_caps_tokens_and_rejects_oversized_response(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            }

    class Client:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "payload": json})
            return Response()

    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    provider = {"id": "test", "base_url": "https://example.test/v1", "api_key": "key"}
    result = asyncio.run(llm.complete_json(provider, "small", [], max_tokens=99999))
    assert captured["payload"]["max_tokens"] == llm.JSON_COMPLETION_MAX_TOKENS
    assert captured["payload"]["stream"] is False
    assert captured["timeout"] == llm.JSON_COMPLETION_TIMEOUT_SECONDS
    assert result["completion_tokens"] == 3

    Response.json = lambda _self: {
        "choices": [{"message": {"content": "x" * (llm.JSON_COMPLETION_MAX_CHARS + 1)}}]
    }
    with pytest.raises(llm.LLMError, match="响应过长"):
        asyncio.run(llm.complete_json(provider, "small", []))

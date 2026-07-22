import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import companion_state, db, llm
from app.affect import observer, observer_service, repository


def create_context(user_text="谢谢你认真帮我，我们继续。", assistant_text="好，我们继续完成它。") -> dict:
    conn = db.connect()
    try:
        sid, uid, aid, pid = db.new_id(), db.new_id(), db.new_id(), db.new_id()
        now = db.now()
        conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)", (sid, "观察测试", now, now))
        conn.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (uid, sid, "user", user_text, now))
        conn.execute("INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)", (aid, sid, "assistant", assistant_text, "observer-test", now + 0.1))
        conn.execute("INSERT INTO providers(id,name,base_url,api_key,models,enabled,sort) VALUES(?,?,?,?,?,1,99)", (pid, "测试观察器", "https://example.invalid/v1", "secret", '["observer-test"]'))
        conn.commit()
        provider = dict(conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone())
    finally:
        conn.close()
    return {"chat_provider": provider, "chat_model": "observer-test", "session_id": sid, "user_message_id": uid, "assistant_message_id": aid}


def valid_output() -> str:
    return json.dumps({
        "protocol_version": observer.PROTOCOL_VERSION,
        "affect_delta": {"contact_need": -0.20, "guardedness": -0.02, "valence": 0.05, "arousal": 0.01, "immersion": 0.08},
        "relationship_delta": {"bond": 0.002, "trust": 0.001},
        "user_status": "active", "trust_basis": "positive_reliability",
        "evidence": [{"speaker": "user", "quote": "谢谢你认真帮我"}],
        "reason": "用户表达感谢并继续合作", "confidence": 0.88,
    }, ensure_ascii=False)


def process(limit=5):
    return asyncio.run(observer_service.process_due(limit))


def get_run(run_id):
    return next(item for item in observer_service.list_runs(200) if item["id"] == run_id)


def test_background_candidate_applies_atomically_once(monkeypatch):
    context = create_context(); calls = []
    async def fake_complete(*_args, **_kwargs):
        calls.append(1); return {"text": valid_output(), "prompt_tokens": 321, "completion_tokens": 123}
    monkeypatch.setattr(llm, "complete_json", fake_complete)
    before = companion_state.get_state(persist_advance=False)
    queued = observer_service.enqueue_turn(**context)
    assert queued["status"] == "queued"
    assert process() == 1
    applied = get_run(queued["id"])
    after = companion_state.get_state(persist_advance=False)
    assert applied["status"] == "applied" and applied["applied_event_id"]
    assert after["relationship"]["bond"] == pytest.approx(before["relationship"]["bond"])
    assert after["relationship"]["trust"] == pytest.approx(before["relationship"]["trust"])
    assert calls == [1]
    assert process() == 0
    event = next(e for e in companion_state.list_events(200) if e["id"] == applied["applied_event_id"])
    assert event["event_type"] == "observation" and event["source"] == "observer"
    assert "legacy_relationship_delta_suppressed" in applied["warnings"]


def test_apply_failure_rolls_back_state_and_event(monkeypatch):
    context = create_context()
    async def fake_complete(*_args, **_kwargs):
        return {"text": valid_output(), "prompt_tokens": 1, "completion_tokens": 1}
    monkeypatch.setattr(llm, "complete_json", fake_complete)
    before = companion_state.get_state(persist_advance=False)
    event_ids_before = {event["id"] for event in companion_state.list_events(200)}
    monkeypatch.setattr(repository, "_save", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db fail")))
    queued = observer_service.enqueue_turn(**context)
    process()
    failed = get_run(queued["id"])
    after = companion_state.get_state(persist_advance=False)
    assert failed["status"] == "recovery_pending"
    assert failed["error_code"] == "observer_apply_failed"
    assert after["affect"] == before["affect"]
    assert after["relationship"] == before["relationship"]
    assert {event["id"] for event in companion_state.list_events(200)} == event_ids_before


def test_concurrent_enqueue_is_idempotent(monkeypatch):
    context = create_context(); calls = 0
    async def fake_complete(*_args, **_kwargs):
        nonlocal calls; calls += 1; return {"text": valid_output(), "prompt_tokens": 1, "completion_tokens": 1}
    monkeypatch.setattr(llm, "complete_json", fake_complete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: observer_service.enqueue_turn(**context), range(2)))
    assert rows[0]["id"] == rows[1]["id"]
    process(); assert calls == 1


def test_failures_retry_three_times_then_exhaust_without_raw_text(monkeypatch):
    context = create_context(); sensitive = "not-json secret-value-that-must-not-be-stored"; calls = 0
    async def invalid(*_args, **_kwargs):
        nonlocal calls; calls += 1; return {"text": sensitive, "prompt_tokens": None, "completion_tokens": None}
    monkeypatch.setattr(llm, "complete_json", invalid)
    run = observer_service.enqueue_turn(**context)
    for expected_attempt in (1, 2, 3):
        process(); current = get_run(run["id"])
        assert current["attempt_count"] == expected_attempt
        if expected_attempt < 3:
            assert current["status"] == "recovery_pending"
            assert current["next_attempt_at"] - current["updated_at"] == pytest.approx(
                observer_service.FIRST_RETRY_DELAY_SECONDS * (2 ** (expected_attempt - 1)),
                abs=0.1,
            )
            conn = db.connect(); conn.execute("UPDATE affect_observer_runs SET next_attempt_at=? WHERE id=?", (db.now() - 1, run["id"])); conn.commit(); conn.close()
    exhausted = get_run(run["id"])
    assert exhausted["status"] == "exhausted" and exhausted["next_attempt_at"] is None
    assert calls == 3 and exhausted["candidate"] is None
    conn = db.connect(); raw = json.dumps(dict(conn.execute("SELECT * FROM affect_observer_runs WHERE id=?", (run["id"],)).fetchone()), ensure_ascii=False); conn.close()
    assert sensitive not in raw


def test_mock_and_oversized_input_are_skipped(monkeypatch):
    mock = create_context(); mock["chat_provider"] = {"id": "mock", "base_url": ""}
    assert observer_service.enqueue_turn(**mock)["status"] == "skipped"
    huge = create_context("谢谢你认真帮我" + "很长" * 5000, "好的" * 5000)
    called = False
    async def should_not_call(*_args, **_kwargs):
        nonlocal called; called = True
    monkeypatch.setattr(llm, "complete_json", should_not_call)
    row = observer_service.enqueue_turn(**huge); process()
    assert get_run(row["id"])["status"] == "skipped" and called is False


def test_stale_running_recovers_only_in_worker_scan():
    context = create_context(); row = observer_service.enqueue_turn(**context)
    conn = db.connect(); conn.execute("UPDATE affect_observer_runs SET status='running',updated_at=? WHERE id=?", (db.now() - observer_service.RUNNING_STALE_SECONDS - 1, row["id"])); conn.commit(); conn.close()
    observer_service.list_runs()
    assert get_run(row["id"])["status"] == "running"  # GET 保持只读（N5）
    assert observer_service.recover_stale_runs() >= 1
    assert get_run(row["id"])["status"] == "recovery_pending"


def test_dedicated_model_config_and_api_validation():
    context = create_context(); pid = context["chat_provider"]["id"]
    assert observer_service.set_model_config("dedicated", pid, "observer-test")["mode"] == "dedicated"
    other = create_context(); queued = observer_service.enqueue_turn(**other)
    detail = get_run(queued["id"])
    assert detail["provider_id"] == pid and detail["model"] == "observer-test"
    with pytest.raises(ValueError): observer_service.set_model_config("dedicated", pid, "missing")
    observer_service.set_model_config("current", None, None)


def test_worker_wake_processes_queue_without_blocking_enqueue(monkeypatch):
    context = create_context()
    async def fake_complete(*_args, **_kwargs): return {"text": valid_output(), "prompt_tokens": 1, "completion_tokens": 1}
    monkeypatch.setattr(llm, "complete_json", fake_complete)
    async def scenario():
        await observer_service.start_worker()
        try:
            row = observer_service.enqueue_turn(**context)
            for _ in range(100):
                if get_run(row["id"])["status"] == "applied": return row
                await asyncio.sleep(0.01)
            raise AssertionError("worker did not apply queued observation")
        finally: await observer_service.stop_worker()
    asyncio.run(scenario())


def test_complete_json_caps_tokens(monkeypatch):
    captured = {}
    class Response:
        status_code = 200
        def json(self): return {"choices": [{"message": {"content": "{}"}}], "usage": {"completion_tokens": 3}}
    class Client:
        def __init__(self, *, timeout): captured["timeout"] = timeout
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, url, *, headers, json): captured["payload"] = json; return Response()
    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    provider = {"id": "test", "base_url": "https://example.test/v1", "api_key": "key"}
    asyncio.run(llm.complete_json(provider, "small", [], max_tokens=99999))
    assert captured["payload"]["max_tokens"] == llm.JSON_COMPLETION_MAX_TOKENS
    assert captured["payload"]["temperature"] == 0

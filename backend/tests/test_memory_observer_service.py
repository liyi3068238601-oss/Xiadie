import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db, llm, memory, memory_observer as observer, memory_observer_service as service

db.init_db()


def create_context(
    user_text="我准备未来三个月持续开发遐蝶项目。",
    assistant_text="我会陪你把遐蝶项目一步步做完。",
) -> dict:
    conn = db.connect()
    try:
        sid, uid, aid, pid = db.new_id(), db.new_id(), db.new_id(), db.new_id()
        now = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (sid, "记忆观察测试", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (uid, sid, "user", user_text, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
            (aid, sid, "assistant", assistant_text, "observer-test", now + 0.1),
        )
        conn.execute(
            "INSERT INTO providers(id,name,base_url,api_key,models,enabled,sort)"
            " VALUES(?,?,?,?,?,1,99)",
            (pid, "记忆观察测试", "https://example.invalid/v1", "secret", '["observer-test"]'),
        )
        conn.commit()
        provider = dict(conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone())
    finally:
        conn.close()
    return {
        "chat_provider": provider, "chat_model": "observer-test", "session_id": sid,
        "user_message_id": uid, "assistant_message_id": aid,
    }


def valid_output(context: dict) -> str:
    return json.dumps({
        "protocol_version": observer.PROTOCOL_VERSION,
        "should_write": True,
        "items": [{
            "scope": "user", "kind": "plan",
            "content": "用户准备未来三个月持续开发遐蝶项目",
            "inner_reason": "这是具有后续价值的明确长期计划",
            "importance": 0.84, "confidence": 0.91, "emotion": "期待",
            "entities": ["遐蝶"], "sensitivity": "normal",
            "evidence_message_ids": [context["user_message_id"]],
        }],
    }, ensure_ascii=False)


def process(limit=5):
    return asyncio.run(service.process_due(limit))


def get_run(run_id):
    return next(item for item in service.list_runs(200) if item["id"] == run_id)


def fragment_count() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM memory_fragments").fetchone()[0]
    finally:
        conn.close()


def test_valid_candidate_is_audited_without_writing_fragment(monkeypatch):
    context = create_context()

    async def fake_complete(*_args, **_kwargs):
        return {"text": valid_output(context), "prompt_tokens": 800, "completion_tokens": 180}

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    before = fragment_count()
    queued = service.enqueue_turn(**context)
    assert queued["status"] == "queued"
    assert process() >= 1
    run = get_run(queued["id"])
    assert run["status"] == "validated"
    assert run["candidate"]["items"][0]["kind"] == "plan"
    assert run["prompt_tokens"] == 800 and run["completion_tokens"] == 180
    assert run["latency_ms"] is not None and run["latency_ms"] >= 0
    assert run["repair_attempted"] is False
    assert fragment_count() == before
    assert process() == 0


def test_concurrent_enqueue_is_idempotent(monkeypatch):
    context = create_context()
    calls = 0

    async def fake_complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"text": valid_output(context), "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(llm, "complete_json", fake_complete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: service.enqueue_turn(**context), range(2)))
    assert rows[0]["id"] == rows[1]["id"]
    process()
    assert calls == 1


def test_invalid_json_uses_only_one_model_repair_for_entire_run(monkeypatch):
    context = create_context()
    sensitive_raw = "not-json private-output-that-must-not-be-stored"
    outputs = [sensitive_raw, sensitive_raw, sensitive_raw]
    calls = 0

    async def invalid(*_args, **_kwargs):
        nonlocal calls
        result = outputs[min(calls, len(outputs) - 1)]
        calls += 1
        return {"text": result, "prompt_tokens": 10, "completion_tokens": 2}

    monkeypatch.setattr(llm, "complete_json", invalid)
    queued = service.enqueue_turn(**context)
    process()
    first = get_run(queued["id"])
    assert first["status"] == "recovery_pending"
    assert first["error_code"] == "invalid_json"
    assert first["repair_attempted"] is True and calls == 2

    conn = db.connect()
    try:
        raw = json.dumps(dict(conn.execute(
            "SELECT * FROM memory_observer_runs WHERE id=?", (queued["id"],)
        ).fetchone()), ensure_ascii=False)
        conn.execute(
            "UPDATE memory_observer_runs SET next_attempt_at=? WHERE id=?",
            (db.now() - 1, queued["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert sensitive_raw not in raw
    process()
    assert calls == 3  # 第二次任务尝试不再进入第二次修复调用


def test_model_timeout_is_safe_and_retryable(monkeypatch):
    context = create_context()

    async def timeout(*_args, **_kwargs):
        raise llm.LLMError("包含不应存储的供应商正文", code="observer_model_timeout")

    monkeypatch.setattr(llm, "complete_json", timeout)
    queued = service.enqueue_turn(**context)
    process()
    run = get_run(queued["id"])
    assert run["status"] == "recovery_pending"
    assert run["error_code"] == "observer_model_timeout"
    conn = db.connect()
    try:
        raw = json.dumps(dict(conn.execute(
            "SELECT * FROM memory_observer_runs WHERE id=?", (queued["id"],)
        ).fetchone()), ensure_ascii=False)
    finally:
        conn.close()
    assert "包含不应存储" not in raw


def test_context_failure_never_leaves_run_stuck_running(monkeypatch):
    context = create_context()
    monkeypatch.setattr(
        memory, "search_memories", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError())
    )
    queued = service.enqueue_turn(**context)
    process()
    run = get_run(queued["id"])
    assert run["status"] == "recovery_pending"
    assert run["error_code"] == "observer_context_failed"


def test_stale_running_recovers_only_during_worker_scan():
    context = create_context()
    queued = service.enqueue_turn(**context)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_observer_runs SET status='running',updated_at=? WHERE id=?",
            (db.now() - service.RUNNING_STALE_SECONDS - 1, queued["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    service.list_runs()
    assert get_run(queued["id"])["status"] == "running"
    assert service.recover_stale_runs() >= 1
    assert get_run(queued["id"])["status"] == "recovery_pending"


def test_dedicated_model_config_rejects_mock_and_unknown_model():
    context = create_context()
    pid = context["chat_provider"]["id"]
    assert service.set_model_config("dedicated", pid, "observer-test")["mode"] == "dedicated"
    other = create_context()
    queued = service.enqueue_turn(**other)
    run = get_run(queued["id"])
    assert run["provider_id"] == pid and run["model"] == "observer-test"
    with pytest.raises(ValueError):
        service.set_model_config("dedicated", "mock", "xiadie-mock")
    with pytest.raises(ValueError):
        service.set_model_config("dedicated", pid, "missing")
    service.set_model_config("current", None, None)


def test_worker_wake_processes_queue_without_blocking_enqueue(monkeypatch):
    context = create_context()

    async def fake_complete(*_args, **_kwargs):
        return {"text": valid_output(context), "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(llm, "complete_json", fake_complete)

    async def scenario():
        await service.start_worker()
        try:
            queued = service.enqueue_turn(**context)
            assert queued["status"] == "queued" and queued["attempt_count"] == 0
            for _ in range(100):
                if get_run(queued["id"])["status"] == "validated":
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("memory observer did not validate queued run")
        finally:
            await service.stop_worker()

    asyncio.run(scenario())

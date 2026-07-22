import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db, llm
from app import main as main_module
from app.affect import observer_service, repository
from app.proactive import cognition, cognition_service, relationship
from app.proactive import orchestrator
from app.proactive.run_ledger import get_run

client = TestClient(
    main_module.app,
    headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


@pytest.fixture(autouse=True)
def reset_cognition_state():
    db.init_db()
    observer_service.set_model_config("current", None, None)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM decision_runs WHERE task_kind=?", (cognition_service.TASK_KIND,))
        conn.commit()
    finally:
        conn.close()
    repository.reset()
    yield


def _turn(user_text="谢谢你帮我解决了问题", assistant_text="很高兴能帮上忙"):
    sid, uid, aid = db.new_id(), db.new_id(), db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (sid, "cognition", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (uid, sid, "user", user_text, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
            (aid, sid, "assistant", assistant_text, "test-model", now + 0.1),
        )
        conn.commit()
    finally:
        conn.close()
    return sid, uid, aid


def _provider():
    provider = {
        "id": "cognition-test-provider", "name": "cognition", "enabled": 1,
        "base_url": "https://example.invalid/v1", "api_key": "secret",
        "models": json.dumps(["test-model"]),
    }
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO providers(id,name,base_url,api_key,models,enabled,sort)"
            " VALUES(?,?,?,?,?,1,99)",
            (provider["id"], provider["name"], provider["base_url"],
             provider["api_key"], provider["models"]),
        )
        conn.commit()
    finally:
        conn.close()
    return provider


def _valid_result():
    return {
        "user_affect": {
            "protocol_version": "user-affect-observation-v1", "state": "positive",
            "needs": ["celebrate"], "evidence": [{"quote": "谢谢你"}],
            "confidence": 0.9, "reason": "explicit appreciation",
        },
        "relationship_meaning": {
            "protocol_version": "relationship-meaning-v1", "label": "shared_appreciation",
            "evidence": [{"speaker": "user", "quote": "谢谢你"}],
            "confidence": 0.95, "reason": "explicit appreciation",
        },
    }


def test_cognition_contract_is_grounded_and_strict():
    parsed = cognition.parse_and_validate(
        _valid_result(), user_text="谢谢你帮我解决了问题", assistant_text="很高兴能帮上忙",
    )
    assert parsed["relationship_meaning"]["label"] == "shared_appreciation"
    invalid = _valid_result()
    invalid["user_affect"]["evidence"] = [{"quote": "模型编造"}]
    with pytest.raises(Exception):
        cognition.parse_and_validate(
            invalid, user_text="谢谢你", assistant_text="不客气",
        )


def test_real_worker_applies_grounded_relationship_once(monkeypatch):
    sid, uid, aid = _turn()
    provider = _provider()

    async def complete(*_args, **_kwargs):
        return {
            "text": json.dumps(_valid_result(), ensure_ascii=False),
            "prompt_tokens": 20, "completion_tokens": 10, "latency_ms": 5,
        }

    monkeypatch.setattr(llm, "complete_json", complete)
    before = repository.get_snapshot(advance_time=False)
    queued = cognition_service.enqueue_turn(
        chat_provider=provider, chat_model="test-model", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    duplicate = cognition_service.enqueue_turn(
        chat_provider=provider, chat_model="test-model", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    assert duplicate["id"] == queued["id"]
    assert asyncio.run(cognition_service.process_due()) == 1
    run = get_run(queued["id"])
    after = repository.get_snapshot(advance_time=False)
    assert run.status == "applied" and run.attempt_count == 1
    assert after["relationship"]["bond"] == pytest.approx(
        before["relationship"]["bond"]
        + relationship.LABEL_DELTAS[relationship.RelationshipLabel.SHARED_APPRECIATION]["bond_delta"]
    )
    result = next(item for item in cognition_service.list_runs() if item["id"] == queued["id"])
    assert result["result"]["user_affect"]["state"] == "positive"
    assert result["result"]["relationship_label"] == "shared_appreciation"


def test_unavailable_observer_uses_zero_relationship_fallback():
    sid, uid, aid = _turn("普通问题", "普通回答")
    before = repository.get_snapshot(advance_time=False)
    queued = cognition_service.enqueue_turn(
        chat_provider={"id": "mock", "base_url": "", "enabled": 1},
        chat_model="xiadie-mock", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    asyncio.run(cognition_service.process_due())
    after = repository.get_snapshot(advance_time=False)
    run = get_run(queued["id"])
    assert run.status == "applied"
    assert "cognition_model_unavailable" in run.warnings
    assert after["relationship"]["bond"] == before["relationship"]["bond"]
    assert after["relationship"]["trust"] == before["relationship"]["trust"]


def test_grounded_low_affect_enqueues_emotional_care_source(monkeypatch):
    sid, uid, aid = _turn()
    provider = _provider()
    result = _valid_result()
    result["user_affect"].update(
        state="low", needs=["comfort"], confidence=0.9,
        reason="grounded low affect",
    )

    async def complete(*_args, **_kwargs):
        return {"text": json.dumps(result, ensure_ascii=False)}

    monkeypatch.setattr(llm, "complete_json", complete)
    queued = cognition_service.enqueue_turn(
        chat_provider=provider, chat_model="test-model", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    asyncio.run(cognition_service.process_due())
    sources = [
        item for item in orchestrator.list_runtime_sources(limit=200)
        if item["source_kind"] == orchestrator.SOURCE_EMOTIONAL_CARE
        and item["source_ref_id"] == queued["id"]
    ]
    assert len(sources) == 1 and sources[0]["status"] == "queued"


def test_source_change_before_claim_is_skipped(monkeypatch):
    sid, uid, aid = _turn()
    provider = _provider()
    queued = cognition_service.enqueue_turn(
        chat_provider=provider, chat_model="test-model", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    conn = db.connect()
    try:
        conn.execute("UPDATE messages SET content='修正后的消息' WHERE id=?", (uid,))
        conn.commit()
    finally:
        conn.close()
    asyncio.run(cognition_service.process_due())
    run = get_run(queued["id"])
    assert run.status == "skipped"
    assert run.error_code == "cognition_source_changed"
    assert relationship.get_suggestion_by_source_message(uid) is None


def test_model_failures_retry_then_apply_conservative_fallback(monkeypatch):
    sid, uid, aid = _turn()
    provider = _provider()

    async def fail(*_args, **_kwargs):
        raise llm.LLMError("private provider detail", code="timeout")

    monkeypatch.setattr(llm, "complete_json", fail)
    queued = cognition_service.enqueue_turn(
        chat_provider=provider, chat_model="test-model", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    for attempt in range(3):
        asyncio.run(cognition_service.process_due())
        run = get_run(queued["id"])
        if attempt < 2:
            assert run.status == "recovery_pending"
            conn = db.connect()
            try:
                conn.execute(
                    "UPDATE decision_runs SET next_attempt_at=? WHERE id=?", (db.now() - 1, run.id),
                )
                conn.commit()
            finally:
                conn.close()
    run = get_run(queued["id"])
    assert run.status == "applied" and run.attempt_count == 3
    assert "conservative_fallback_after_exhaustion" in run.warnings
    assert "private provider detail" not in json.dumps(cognition_service.list_runs())


def test_invalid_model_json_enters_bounded_recovery(monkeypatch):
    sid, uid, aid = _turn()
    provider = _provider()

    async def complete(*_args, **_kwargs):
        return {"text": "{"}

    monkeypatch.setattr(llm, "complete_json", complete)
    queued = cognition_service.enqueue_turn(
        chat_provider=provider, chat_model="test-model", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    before = repository.get_snapshot(advance_time=False)["relationship"]
    asyncio.run(cognition_service.process_due())
    run = get_run(queued["id"])
    after = repository.get_snapshot(advance_time=False)["relationship"]
    assert run.status == "recovery_pending"
    assert run.error_code == "invalid_json"
    assert after["bond"] == before["bond"]
    assert after["trust"] == before["trust"]


def test_disabled_proactive_and_elapsed_time_do_not_reduce_relationship():
    before_setting = db.get_setting("proactive_enabled", "1")
    before = repository.get_snapshot(advance_time=False)["relationship"]
    try:
        db.set_setting("proactive_enabled", "0")
        after = repository.advance_by(24 * 60)["relationship"]
        assert after["bond"] == before["bond"]
        assert after["trust"] == before["trust"]
    finally:
        db.set_setting("proactive_enabled", before_setting)


def test_stale_running_cognition_is_recovered():
    sid, uid, aid = _turn()
    queued = cognition_service.enqueue_turn(
        chat_provider={"id": "mock", "base_url": "", "enabled": 1},
        chat_model="xiadie-mock", session_id=sid,
        user_message_id=uid, assistant_message_id=aid,
    )
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE decision_runs SET status='running',attempt_count=1,updated_at=? WHERE id=?",
            (db.now() - cognition_service.RUNNING_STALE_SECONDS - 1, queued["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert cognition_service.recover_stale_runs() == 1
    assert get_run(queued["id"]).status == "recovery_pending"


def test_real_chat_api_queues_cognition_without_mechanical_bond(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(main_module, "_current_model", lambda: (provider, "test-model"))

    async def stream(*_args, **_kwargs):
        yield "很高兴能帮上忙"

    async def complete(*_args, **_kwargs):
        return {
            "text": json.dumps(_valid_result(), ensure_ascii=False),
            "prompt_tokens": 20, "completion_tokens": 10,
        }

    monkeypatch.setattr(llm, "stream_chat", stream)
    monkeypatch.setattr(llm, "complete_json", complete)
    session = client.post("/api/sessions", json={}).json()
    before = repository.get_snapshot(advance_time=False)
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session["id"], "content": "谢谢你帮我解决了问题"},
    ) as response:
        body = "".join(response.iter_text())
    hot_path = repository.get_snapshot(advance_time=False)
    assert response.status_code == 200 and '"companion_cognition": {' in body
    assert hot_path["relationship"]["bond"] == before["relationship"]["bond"]
    asyncio.run(cognition_service.process_due(limit=20))
    after = repository.get_snapshot(advance_time=False)
    assert after["relationship"]["bond"] > before["relationship"]["bond"]


def test_regenerated_reply_revokes_old_meaning_and_recalculates(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(main_module, "_current_model", lambda: (provider, "test-model"))
    replies = iter(["很高兴能帮上忙", "这是普通回答"])
    results = iter([
        _valid_result(),
        {
            "user_affect": {
                "protocol_version": "user-affect-observation-v1", "state": "unknown",
                "needs": [], "evidence": [], "confidence": 0.0, "reason": "no evidence",
            },
            "relationship_meaning": {
                "protocol_version": "relationship-meaning-v1", "label": "ordinary_exchange",
                "evidence": [], "confidence": 0.9, "reason": "ordinary replacement",
            },
        },
    ])

    async def stream(*_args, **_kwargs):
        yield next(replies)

    async def complete(*_args, **_kwargs):
        return {"text": json.dumps(next(results), ensure_ascii=False)}

    monkeypatch.setattr(llm, "stream_chat", stream)
    monkeypatch.setattr(llm, "complete_json", complete)
    session = client.post("/api/sessions", json={}).json()
    baseline = repository.get_snapshot(advance_time=False)["relationship"]["bond"]
    for regenerate in (False, True):
        with client.stream(
            "POST", "/api/chat",
            json={
                "session_id": session["id"], "content": "谢谢你帮我解决了问题",
                "regenerate": regenerate,
            },
        ) as response:
            assert response.status_code == 200
            "".join(response.iter_text())
        asyncio.run(cognition_service.process_due(limit=20))
    final = repository.get_snapshot(advance_time=False)
    assert final["relationship"]["bond"] == pytest.approx(baseline)
    assert final["relationship"]["interaction_count"] == 1
    conn = db.connect()
    try:
        statuses = [row[0] for row in conn.execute(
            "SELECT status FROM episode_relationship_delta_suggestions "
            "WHERE session_id=? ORDER BY created_at", (session["id"],),
        ).fetchall()]
    finally:
        conn.close()
    assert statuses == ["revoked", "applied"]

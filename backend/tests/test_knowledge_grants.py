"""K.4 knowledge transmission grants: fail-closed, bound, single-use authorization."""
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge, knowledge_recall, knowledge_search, knowledge_worker, llm
from app.main import app


client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)
QUERY = "请根据文档告诉我星穹密钥"
CONTENT = "星穹密钥是仅用于授权测试的紫色回声。"
NONCE = "grant-test-nonce-0001"


@pytest.fixture(autouse=True)
def clean_grant_data():
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM knowledge_documents")
        conn.execute(
            "UPDATE providers SET enabled=1,execution_location='remote',location_revision=1 "
            "WHERE id='deepseek'"
        )
        conn.commit()
    finally:
        conn.close()
    db.set_setting("current_model", json.dumps({
        "provider_id": "deepseek", "model": "deepseek-chat",
    }))
    knowledge_recall.update_settings(mode="explicit", shadow_enabled=True)
    yield
    db.set_setting("current_model", json.dumps({
        "provider_id": "mock", "model": "xiadie-mock",
    }))
    knowledge_recall.update_settings(mode="explicit", shadow_enabled=True)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()


def _session() -> dict:
    return client.post("/api/sessions", json={}).json()


def _index(*, sensitivity: str = "normal") -> dict:
    imported = knowledge.import_file(
        "远传授权资料.md", "text/markdown", CONTENT.encode("utf-8"),
        sensitivity=sensitivity,
    )
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    document = imported["document"]
    # migration 47 后非敏感文档默认 remote_allowed，不再触发 grant 流程；
    # grant 测试需要 ask_each_time 才能测试授权卡片流程。只改策略，不增加 revision，
    # 以保持 patch 测试的 revision 断言语义（起始 revision=1，patch 后 +1=2）
    if sensitivity == "normal":
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE knowledge_documents SET transmission_policy='ask_each_time' WHERE id=?",
                (document["id"],),
            )
            conn.commit()
        finally:
            conn.close()
    return document


def _preflight(session_id: str, nonce: str = NONCE) -> dict:
    response = client.post("/api/knowledge/recall/preflight", json={
        "session_id": session_id, "request_nonce": nonce, "content": QUERY,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _resolve(grant: dict, session_id: str, action: str = "allow_once") -> dict:
    response = client.post("/api/knowledge/transmission-grants", json={
        "grant_id": grant["id"], "action": action, "session_id": session_id,
        "request_nonce": NONCE, "content": QUERY,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _chat(payload: dict):
    with client.stream("POST", "/api/chat", json=payload) as response:
        return response.status_code, "".join(response.iter_text())


def test_schema_38_grants_are_body_and_plaintext_token_free():
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "72"
        decision_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_recall_decisions)")
        }
        assert "threshold_version" in decision_columns
        for table in (
            "knowledge_transmission_grants", "knowledge_transmission_grant_items",
            "knowledge_transmission_grant_events",
        ):
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert not ({"content", "query", "token", "path", "original_name"} & columns)
        grants = {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_transmission_grants)")
        }
        assert {"token_hash", "plan_sha256", "policy_snapshot_sha256"} <= grants
    finally:
        conn.close()


def test_remote_restricted_chat_requires_grant_and_writes_no_message(monkeypatch):
    _index()
    session = _session()
    called = False

    async def fake_stream(*_args, **_kwargs):
        nonlocal called
        called = True
        yield "should not run"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    status, body = _chat({"session_id": session["id"], "content": QUERY})
    assert status == 409
    assert "knowledge_grant_required" in body
    assert called is False
    assert client.get(f"/api/sessions/{session['id']}/messages").json() == []


def test_preflight_exposes_preview_without_content_and_persists_hashes_only():
    document = _index()
    session = _session()
    grant = _preflight(session["id"])
    assert grant["status"] == "pending"
    assert grant["provider"]["location"] == "remote"
    assert grant["documents"] == [{
        "id": document["id"], "name": "远传授权资料.md", "policy": "ask_each_time",
        "sensitivity": "normal", "chunk_count": 1,
        "token_estimate": grant["documents"][0]["token_estimate"],
    }]
    assert grant["can_allow_once"] is True and grant["can_always_allow"] is True
    assert CONTENT not in json.dumps(grant, ensure_ascii=False)
    assert grant["stores_content"] is False

    conn = db.connect()
    try:
        row = dict(conn.execute(
            "SELECT * FROM knowledge_transmission_grants WHERE id=?", (grant["id"],),
        ).fetchone())
        assert row["token_hash"] is None
        assert row["user_content_sha256"] == hashlib.sha256(QUERY.casefold().encode()).hexdigest()
        assert CONTENT not in json.dumps(row, ensure_ascii=False)
    finally:
        conn.close()


def test_allow_once_is_hashed_consumed_and_cannot_be_replayed(monkeypatch):
    _index()
    session = _session()
    grant = _preflight(session["id"])
    issued = _resolve(grant, session["id"])
    token = issued["token"]
    assert token and issued["single_use"] is True

    captured = {}

    async def fake_stream(_provider, _model, messages, **_kwargs):
        captured["system"] = messages[0]["content"]
        yield "已查到 [资料:K1]"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    payload = {
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_grant_token": token,
    }
    status, body = _chat(payload)
    assert status == 200 and '"knowledge_used": true' in body
    assert CONTENT in captured["system"]

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status,token_hash,user_message_id FROM knowledge_transmission_grants WHERE id=?",
            (grant["id"],),
        ).fetchone()
        assert row["status"] == "consumed" and row["user_message_id"]
        assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token != row["token_hash"]
    finally:
        conn.close()

    before = len(client.get(f"/api/sessions/{session['id']}/messages").json())
    replay_status, replay_body = _chat(payload)
    assert replay_status == 409 and "grant_replayed" in replay_body
    assert len(client.get(f"/api/sessions/{session['id']}/messages").json()) == before


def test_deny_then_skip_sends_chat_without_restricted_chunks(monkeypatch):
    _index()
    session = _session()
    grant = _preflight(session["id"])
    denied = client.post(f"/api/knowledge/transmission-grants/{grant['id']}/deny")
    assert denied.status_code == 200 and denied.json()["status"] == "denied"
    captured = {}

    async def fake_stream(_provider, _model, messages, **_kwargs):
        captured["system"] = messages[0]["content"]
        yield "没有使用资料"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    status, body = _chat({
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_skip_restricted": True,
    })
    assert status == 200 and '"knowledge_used": false' in body
    assert CONTENT not in captured["system"]


def test_sensitive_local_only_cannot_be_granted_or_made_remote_allowed():
    _index(sensitivity="sensitive")
    session = _session()
    grant = _preflight(session["id"])
    assert grant["documents"][0]["policy"] == "local_only"
    assert grant["can_allow_once"] is False and grant["can_always_allow"] is False
    for action, code in (
        ("allow_once", "local_only_cannot_grant"),
        ("always_allow", "sensitive_remote_forbidden"),
    ):
        response = client.post("/api/knowledge/transmission-grants", json={
            "grant_id": grant["id"], "action": action, "session_id": session["id"],
            "request_nonce": NONCE, "content": QUERY,
        })
        assert response.status_code == 409 and code in response.text


def test_expired_grant_is_cleared_and_fails_closed():
    _index()
    session = _session()
    grant = _preflight(session["id"])
    issued = _resolve(grant, session["id"])
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_transmission_grants SET expires_at=? WHERE id=?",
            (db.now() - 1, grant["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    status, body = _chat({
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_grant_token": issued["token"],
    })
    assert status == 409 and "grant_expired" in body
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status,token_hash FROM knowledge_transmission_grants WHERE id=?", (grant["id"],),
        ).fetchone()
        assert tuple(row) == ("expired", None)
    finally:
        conn.close()


def test_policy_revision_change_revokes_issued_grant():
    document = _index()
    session = _session()
    grant = _preflight(session["id"])
    issued = _resolve(grant, session["id"])
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_documents SET policy_revision=policy_revision+1 WHERE id=?",
            (document["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    status, body = _chat({
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_grant_token": issued["token"],
    })
    assert status == 409 and "grant_binding_changed" in body
    assert client.get(
        f"/api/knowledge/transmission-grants/{grant['id']}"
    ).json()["status"] == "revoked"


def test_concurrent_replay_allows_exactly_one_chat(monkeypatch):
    _index()
    session = _session()
    grant = _preflight(session["id"])
    issued = _resolve(grant, session["id"])

    async def fake_stream(*_args, **_kwargs):
        yield "唯一成功的回复"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    payload = {
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_grant_token": issued["token"],
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: _chat(payload), range(2)))
    assert sorted(status for status, _body in results) == [200, 409]
    assert sum("grant_replayed" in body for _status, body in results) == 1
    messages = client.get(f"/api/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user", "assistant"]


def test_online_failure_consumes_grant_and_retry_requires_new_confirmation(monkeypatch):
    _index()
    session = _session()
    grant = _preflight(session["id"])
    issued = _resolve(grant, session["id"])

    async def failing_stream(*_args, **_kwargs):
        raise llm.LLMError("provider unavailable", "请稍后重试")
        yield  # pragma: no cover - keep this an async generator

    monkeypatch.setattr(llm, "stream_chat", failing_stream)
    payload = {
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_grant_token": issued["token"],
    }
    status, body = _chat(payload)
    assert status == 200 and 'event: error' in body
    replay_status, replay_body = _chat(payload)
    assert replay_status == 409 and "grant_replayed" in replay_body


@pytest.mark.parametrize(
    ("location", "policy", "expected", "can_allow_once"),
    [
        ("local", "ask_each_time", "not_needed", False),
        ("remote", "ask_each_time", "pending", True),
        ("unknown", "ask_each_time", "pending", True),
        ("remote", "local_only", "pending", False),
        ("remote", "remote_allowed", "not_needed", False),
    ],
)
def test_provider_location_and_document_policy_matrix(
    location, policy, expected, can_allow_once,
):
    document = _index()
    session = _session()
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE providers SET execution_location=? WHERE id='deepseek'", (location,),
        )
        conn.execute(
            "UPDATE knowledge_documents SET transmission_policy=? WHERE id=?",
            (policy, document["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    grant = _preflight(session["id"])
    assert grant["status"] == expected
    assert grant["can_allow_once"] is can_allow_once


@pytest.mark.parametrize(
    ("action", "expected_policy"),
    [("always_allow", "remote_allowed"), ("local_only", "local_only")],
)
def test_persistent_policy_choice_is_revisioned_and_audited(action, expected_policy):
    document = _index()
    session = _session()
    grant = _preflight(session["id"])
    result = _resolve(grant, session["id"], action)
    assert result["status"] == "policy_updated"
    conn = db.connect()
    try:
        current = conn.execute(
            "SELECT transmission_policy,policy_revision FROM knowledge_documents WHERE id=?",
            (document["id"],),
        ).fetchone()
        event = conn.execute(
            "SELECT after_policy,actor,reason_code FROM knowledge_document_policy_events "
            "WHERE document_id=? ORDER BY created_at DESC LIMIT 1", (document["id"],),
        ).fetchone()
        grant_status = conn.execute(
            "SELECT status FROM knowledge_transmission_grants WHERE id=?", (grant["id"],),
        ).fetchone()[0]
        assert tuple(current) == (expected_policy, 2)
        assert tuple(event) == (expected_policy, "user", "grant_ui_policy_change")
        assert grant_status == "revoked"
    finally:
        conn.close()


def test_smart_natural_ask_reuses_grant_and_records_confirmed_source(monkeypatch):
    # 使用标题中的强实体形成 high-confidence 自然召回；第四个任务建立本地向量索引。
    imported = knowledge.import_file(
        "星穹密钥说明.md", "text/markdown", CONTENT.encode("utf-8"),
    )
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
    # migration 47 后非敏感文档默认 remote_allowed；显式设为 ask_each_time 以测试 grant 流程
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_documents SET transmission_policy='ask_each_time' WHERE id=?",
            (imported["document"]["id"],),
        )
        conn.commit()
        row = conn.execute(
            "SELECT transmission_policy FROM knowledge_documents WHERE id=?",
            (imported["document"]["id"],),
        ).fetchone()
        assert row["transmission_policy"] == "ask_each_time"
    finally:
        conn.close()
    knowledge_recall.update_settings(mode="smart", shadow_enabled=True)
    session = _session()
    natural_query = "星穹密钥说明里记录了什么？"
    preflight = client.post("/api/knowledge/recall/preflight", json={
        "session_id": session["id"], "request_nonce": NONCE, "content": natural_query,
    })
    assert preflight.status_code == 200
    grant = preflight.json()
    assert grant["status"] == "pending" and grant["recall_mode"] == "smart"
    issued_response = client.post("/api/knowledge/transmission-grants", json={
        "grant_id": grant["id"], "action": "allow_once", "session_id": session["id"],
        "request_nonce": NONCE, "content": natural_query,
    })
    assert issued_response.status_code == 200

    async def fake_stream(*_args, **_kwargs):
        yield "已确认资料 [资料:K1]"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    status, body = _chat({
        "session_id": session["id"], "content": natural_query, "request_nonce": NONCE,
        "knowledge_grant_token": issued_response.json()["token"],
    })
    assert status == 200
    assert '"knowledge_source": "confirmed"' in body
    assert '"knowledge_recall_mode": "smart"' in body
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT recall_decision_id,status FROM knowledge_transmission_grants WHERE id=?",
            (grant["id"],),
        ).fetchone()
        assert row["status"] == "consumed" and row["recall_decision_id"]
        decision = conn.execute(
            "SELECT shadow,action,confidence_band,injected_count FROM knowledge_recall_decisions "
            "WHERE id=?", (row["recall_decision_id"],),
        ).fetchone()
        assert tuple(decision) == (0, "ask", "high", 1)
    finally:
        conn.close()


def test_recall_mode_change_invalidates_pending_grant():
    _index()
    session = _session()
    grant = _preflight(session["id"])
    knowledge_recall.update_settings(mode="smart", shadow_enabled=True)
    response = client.post("/api/knowledge/transmission-grants", json={
        "grant_id": grant["id"], "action": "allow_once", "session_id": session["id"],
        "request_nonce": NONCE, "content": QUERY,
    })
    assert response.status_code == 409
    assert "grant_binding_changed" in response.text


def test_off_mode_preflight_does_not_run_search(monkeypatch):
    _index()
    session = _session()
    knowledge_recall.update_settings(mode="off", shadow_enabled=True)

    def forbidden_search(*_args, **_kwargs):
        raise AssertionError("off mode must not search")

    monkeypatch.setattr(knowledge_search, "hybrid_search", forbidden_search)
    result = _preflight(session["id"])
    assert result["status"] == "not_needed"
    assert result["recall_mode"] == "off"


def test_model_switch_revokes_an_issued_grant_before_any_message_is_written():
    _index()
    session = _session()
    grant = _preflight(session["id"])
    issued = _resolve(grant, session["id"])
    db.set_setting("current_model", json.dumps({
        "provider_id": "deepseek", "model": "deepseek-reasoner",
    }))

    status, body = _chat({
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_grant_token": issued["token"],
    })

    assert status == 409 and "grant_binding_changed" in body
    assert client.get(f"/api/sessions/{session['id']}/messages").json() == []
    current = client.get(
        f"/api/knowledge/transmission-grants/{grant['id']}"
    ).json()
    assert current["status"] == "revoked"


def test_source_change_after_issue_revokes_grant_and_fails_closed():
    document = _index()
    session = _session()
    grant = _preflight(session["id"])
    issued = _resolve(grant, session["id"])
    changed = CONTENT + " changed-after-confirmation"
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_chunks SET content=?,content_sha256=? WHERE document_id=?",
            (changed, hashlib.sha256(changed.encode()).hexdigest(), document["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    status, body = _chat({
        "session_id": session["id"], "content": QUERY, "request_nonce": NONCE,
        "knowledge_grant_token": issued["token"],
    })

    assert status == 409
    assert "grant_binding_changed" in body or "grant_source_or_policy_changed" in body
    assert client.get(f"/api/sessions/{session['id']}/messages").json() == []
    current = client.get(
        f"/api/knowledge/transmission-grants/{grant['id']}"
    ).json()
    assert current["status"] == "revoked"

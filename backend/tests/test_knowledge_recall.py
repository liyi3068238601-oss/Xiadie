import hashlib
import time

from app import db, knowledge_recall, knowledge_recall_service, knowledge_search
from app.main import app
from fastapi.testclient import TestClient

CLIENT = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


def _found(*, mode="fts", vector=False, error=None, results=None):
    return {
        "results": results or [], "result_count": len(results or []),
        "retrieval_mode": mode, "vector_available": vector, "vector_error_code": error,
    }


def _item(document_id="doc", name="星港项目规范.md", match_type="primary"):
    return {"document_id": document_id, "original_name": name, "match_type": match_type, "tags": []}


def test_schema_36_decisions_are_versioned_body_free_and_repeatable():
    db.init_db()
    db.init_db()
    conn = db.connect()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_recall_decisions)")}
        assert {"protocol_version", "action", "reason_code", "query_sha256", "candidate_count",
                "eligible_count", "policy_snapshot_sha256", "provider_location_revision"} <= columns
        assert not ({"query", "content", "original_name", "chunk_id", "document_id", "raw_output"} & columns)
        assert db.get_setting("knowledge_shadow_recall_enabled") == "1"
    finally:
        conn.close()


def test_deterministic_skip_and_explicit_rules_do_not_search(monkeypatch):
    def forbidden_search(*_args, **_kwargs):
        raise AssertionError("skip rule must not search")
    monkeypatch.setattr(knowledge_search, "hybrid_search", forbidden_search)
    assert knowledge_recall.evaluate("晚上好，今天陪我聊一会儿吧。")["reason_code"] == "companion_smalltalk"
    assert knowledge_recall.evaluate("今天有点累。")["reason_code"] == "emotional_support"
    assert knowledge_recall.evaluate("帮我翻译 hello")["reason_code"] == "simple_task"
    assert knowledge_recall.evaluate("她呢？")["reason_code"] == "ambiguous_reference"
    assert knowledge_recall.evaluate("不要查知识库里的内容")["reason_code"] == "explicit_forbidden"


def test_local_fts_dense_stats_and_stable_reasons(monkeypatch):
    rows = [_item(match_type="hybrid")]
    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: _found(
        mode="hybrid", vector=True, results=rows,
    ))
    monkeypatch.setattr(knowledge_recall, "_document_policies", lambda _ids: {
        "doc": {"transmission_policy": "remote_allowed", "policy_revision": 2},
    })
    result = knowledge_recall.evaluate(
        "星港项目的删除申请有什么要求？",
        {"execution_location": "remote"},
    )
    assert (result["action"], result["reason_code"], result["confidence_band"]) == (
        "retrieve", "exact_term_hit", "high",
    )
    assert result["candidate_count"] == result["eligible_count"] == 1
    assert result["retrieval_mode"] == "hybrid" and result["vector_available"] is True
    assert len(result["policy_snapshot_sha256"]) == 64


def test_empty_database_model_missing_and_vector_failure_degrade(monkeypatch):
    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: _found(
        mode="fts", vector=False, error="embedding_unavailable",
    ))
    empty = knowledge_recall.evaluate("星港项目的删除要求是什么？")
    assert empty["action"] == "skip" and empty["reason_code"] == "no_candidates"
    assert empty["vector_available"] is False

    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: _found(
        mode="fts", vector=False, error="embedding_search_failed", results=[_item()],
    ))
    monkeypatch.setattr(knowledge_recall, "_document_policies", lambda _ids: {
        "doc": {"transmission_policy": "remote_allowed", "policy_revision": 1},
    })
    degraded = knowledge_recall.evaluate("项目删除有什么要求？", {"execution_location": "remote"})
    assert degraded["action"] == "retrieve"
    assert degraded["retrieval_mode"] == "fts"
    assert degraded["vector_error_code"] == "embedding_search_failed"


def test_remote_candidate_counts_exclude_local_only_documents(monkeypatch):
    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: _found(results=[
        _item("remote", "远程资料.md"), _item("private", "私人资料.md"),
    ]))
    monkeypatch.setattr(knowledge_recall, "_document_policies", lambda _ids: {
        "remote": {"transmission_policy": "remote_allowed", "policy_revision": 1},
        "private": {"transmission_policy": "local_only", "policy_revision": 4},
    })
    result = knowledge_recall.evaluate("远程资料的内容", {"execution_location": "remote"})
    assert result["action"] == "retrieve"
    assert result["candidate_count"] == 2 and result["eligible_count"] == 1


def test_fts_no_terms_error_is_body_free_failure(monkeypatch):
    def no_terms(*_args, **_kwargs):
        raise knowledge_search.SearchError("knowledge_query_has_no_terms", "no terms")
    monkeypatch.setattr(knowledge_search, "hybrid_search", no_terms)
    result = knowledge_recall.evaluate("🦋🦋")
    assert result["status"] == "failed" and result["reason_code"] == "fts_no_terms"


def test_shadow_record_never_reports_injection_and_public_api_hides_hashes(monkeypatch):
    session_id, message_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        now = db.now()
        conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,'test',?,?)", (session_id, now, now))
        conn.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,'user','private body',?)", (message_id, session_id, now))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(knowledge_recall_service, "enqueue", lambda *_a, **_k: None)
    decision_id = knowledge_recall.enqueue(
        session_id=session_id, user_message_id=message_id, user_text="private body",
        provider={"id": "mock", "execution_location": "local", "location_revision": 3},
    )
    knowledge_recall.complete(decision_id, {
        "action": "retrieve", "reason_code": "lexical_candidate", "confidence_band": "medium",
        "candidate_count": 2, "eligible_count": 2, "retrieval_mode": "fts",
        "vector_available": False, "vector_error_code": None,
        "policy_snapshot_sha256": hashlib.sha256(b"policy").hexdigest(),
        "latency_ms": 4, "status": "completed",
    })
    public = knowledge_recall.get_decision(decision_id)
    assert public["shadow"] is True and public["injected_count"] == 0
    assert "query_sha256" not in public and "policy_snapshot_sha256" not in public
    assert "private body" not in str(public)


def test_worker_marks_over_budget_evaluation_timed_out(monkeypatch):
    monkeypatch.setattr(knowledge_recall, "TIMEOUT_MS", 0)
    monkeypatch.setattr(knowledge_recall, "evaluate", lambda *_a, **_k: {
        "latency_ms": 1,
    })
    calls = []
    monkeypatch.setattr(knowledge_recall, "fail", lambda decision_id, timed_out=False: calls.append((decision_id, timed_out)))
    knowledge_recall_service._queue.put(("decision", "body", {}))
    thread = knowledge_recall_service.threading.Thread(target=knowledge_recall_service._loop, daemon=True)
    thread.start()
    for _ in range(50):
        if calls:
            break
        time.sleep(.01)
    knowledge_recall_service._queue.put(None)
    thread.join(timeout=.5)
    assert calls == [("decision", True)]


def test_recall_diagnostic_api_is_body_free_and_shadow_toggle_is_explicit():
    disabled = CLIENT.patch("/api/knowledge/recall/settings", json={"shadow_enabled": False})
    assert disabled.status_code == 200 and disabled.json()["shadow_enabled"] is False
    enabled = CLIENT.patch("/api/knowledge/recall/settings", json={"shadow_enabled": True})
    assert enabled.status_code == 200
    assert enabled.json() == {
        "shadow_enabled": True,
        "protocol_version": knowledge_recall.PROTOCOL_VERSION,
        "answer_behavior": "explicit_unchanged",
        "stores_query_or_content": False,
    }
    response = CLIENT.get("/api/knowledge/recall-decisions?limit=100")
    assert response.status_code == 200
    serialized = response.text.lower()
    assert "private body" not in serialized
    assert "query_sha256" not in serialized and "policy_snapshot_sha256" not in serialized
    assert CLIENT.get("/api/knowledge/recall-decisions?limit=0").status_code == 400

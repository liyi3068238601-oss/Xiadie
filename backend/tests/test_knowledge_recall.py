import hashlib
import json
import time
from pathlib import Path

from app import (
    db, knowledge_recall, knowledge_recall_evaluation, knowledge_recall_service,
    knowledge_recall_thresholds, knowledge_search,
)
from scripts import run_knowledge_recall_eval
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
    assert result["action"] == "ask"
    assert result["candidate_count"] == 2 and result["eligible_count"] == 1


def test_duplicate_cluster_can_use_an_allowed_source_without_consent(monkeypatch):
    clustered = {**_item("private", "重复资料.md"),
                 "duplicate_document_ids": ["private", "allowed"]}
    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: _found(results=[clustered]))
    monkeypatch.setattr(knowledge_recall, "_document_policies", lambda _ids: {
        "private": {"transmission_policy": "local_only", "policy_revision": 1},
        "allowed": {"transmission_policy": "remote_allowed", "policy_revision": 2},
    })
    result = knowledge_recall.evaluate("重复资料怎么规定？", {"execution_location": "remote"})
    assert result["action"] == "retrieve" and result["eligible_count"] == 1


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
        "mode": "explicit",
        "shadow_enabled": True,
        "protocol_version": knowledge_recall.PROTOCOL_VERSION,
        "threshold_version": knowledge_recall_thresholds.THRESHOLD_VERSION,
        "natural_token_budget": knowledge_recall.NATURAL_TOKEN_BUDGET,
        "automatic_injection_enabled": True,
        "answer_behavior": "explicit_unchanged",
        "stores_query_or_content": False,
    }
    response = CLIENT.get("/api/knowledge/recall-decisions?limit=100")
    assert response.status_code == 200
    serialized = response.text.lower()
    assert "private body" not in serialized
    assert "query_sha256" not in serialized and "policy_snapshot_sha256" not in serialized
    assert CLIENT.get("/api/knowledge/recall-decisions?limit=0").status_code == 400


def test_term_strength_semantic_lexical_and_negation_boundaries(monkeypatch):
    policies = {"doc": {"transmission_policy": "remote_allowed", "policy_revision": 1}}
    monkeypatch.setattr(knowledge_recall, "_document_policies", lambda _ids: policies)

    def result(name, match_type, **features):
        return _found(results=[{**_item(name=name, match_type=match_type), **features}])

    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: result("项目规范.md", "vector"))
    assert knowledge_recall.evaluate("项目以后怎么处理？")["reason_code"] == "entity_hit"

    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: result("无关标题.md", "vector"))
    assert knowledge_recall.evaluate("换一种说法描述清理规则")["reason_code"] == "semantic_candidate"

    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: result("无关标题.md", "primary"))
    assert knowledge_recall.evaluate("删除申请确认后副本怎么办")["reason_code"] == "lexical_candidate"

    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: result("星港项目.md", "primary"))
    assert knowledge_recall.evaluate("不要不查知识库，星港项目怎么删除？")["reason_code"] == "explicit_request"
    assert knowledge_recall.evaluate("如果不知道，就不要查知识库")["reason_code"] == "explicit_forbidden"


def test_dense_floor_natural_budget_and_source_conflict(monkeypatch):
    weak = {**_item(name="弱语义.md", match_type="vector"), "fts_position": None,
            "dense_position": 1, "vector_score": knowledge_recall_thresholds.SEMANTIC_CANDIDATE_MIN_SCORE - .001,
            "content": "弱候选", "heading_path": []}
    strong = {**_item(name="关系表.md", match_type="vector"), "fts_position": None,
              "dense_position": 1, "vector_score": knowledge_recall_thresholds.SEMANTIC_CANDIDATE_MIN_SCORE + .001,
              "content": "岚音与澄川是同事", "heading_path": []}
    monkeypatch.setattr(knowledge_recall, "_document_policies", lambda _ids: {
        "doc": {"transmission_policy": "remote_allowed", "policy_revision": 1},
    })
    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: _found(
        mode="vector", vector=True, results=[weak],
    ))
    assert knowledge_recall.evaluate("完全无关的问题")["reason_code"] == "no_candidates"

    monkeypatch.setattr(knowledge_search, "hybrid_search", lambda *_a, **_k: _found(
        mode="vector", vector=True, results=[strong],
    ))
    conflict = knowledge_recall.evaluate("我记得岚音和澄川是姐妹，但资料里怎么写？")
    assert conflict["reason_code"] == "source_conflict"
    assert conflict["confidence_band"] == "high"

    candidates = [
        {"original_name": f"资料{i}.md", "heading_path": [], "content": "知识" * 260}
        for i in range(6)
    ]
    selected, tokens = knowledge_recall.select_natural_candidates(candidates)
    assert 1 <= len(selected) <= knowledge_recall.MAX_NATURAL_RESULTS
    assert tokens <= knowledge_recall.NATURAL_TOKEN_BUDGET


def test_evaluation_v3_fixture_threshold_evidence_and_reports_are_stable():
    fixtures = Path(__file__).parent / "fixtures"
    fixture = knowledge_recall_evaluation.load_fixture(
        fixtures / "knowledge_recall_evaluation_v3.json"
    )
    assert len(fixture["cases"]) == knowledge_recall_thresholds.SOURCE_SAMPLE_COUNT == 52
    fixture_json = json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(fixture_json.encode()).hexdigest() == knowledge_recall_thresholds.SOURCE_FIXTURE_SHA256
    categories = {case["category"] for case in fixture["cases"]}
    assert {"explicit_recall", "skip", "vector_strong", "negative_dense", "duplicate_sources",
            "local_only", "prompt_injection", "negation_boundary", "fts_no_terms",
            "expanded_positive", "expanded_negative"} <= categories
    assert knowledge_recall_thresholds.AUTOMATIC_INJECTION_ENABLED is True
    assert knowledge_recall_thresholds.SEMANTIC_AUTO_HIGH_ENABLED is False

    reports = Path(__file__).parents[2] / "docs" / "reports"
    baseline = json.loads((reports / "knowledge-recall-eval-v2-baseline.json").read_text(encoding="utf-8"))
    old = json.loads((reports / "knowledge-recall-eval-v2-calibrated.json").read_text(encoding="utf-8"))
    assert baseline["fixture_sha256"] == old["fixture_sha256"]
    calibrated = json.loads(
        (reports / "knowledge-recall-eval-v3-calibrated.json").read_text(encoding="utf-8")
    )
    assert calibrated["fixture_sha256"] == knowledge_recall_thresholds.SOURCE_FIXTURE_SHA256
    assert calibrated["score_evidence"]["positive_top_dense"]["sample_count"] == 30
    assert calibrated["score_evidence"]["negative_top_dense"]["sample_count"] == 15
    assert calibrated["score_evidence"]["dense_classes_separable"] is False
    assert calibrated["score_evidence"]["high_confidence_auto_precision"] == 1.0
    assert calibrated["threshold_decision"]["automatic_injection_enabled"] is True
    assert calibrated["threshold_decision"]["semantic_auto_high_enabled"] is False
    search_v2 = json.loads(
        (reports / "knowledge-recall-eval-v3-search-v2.json").read_text(encoding="utf-8")
    )
    assert search_v2["search_protocol_version"] == "knowledge-search-v2"
    assert search_v2["environment"]["search_protocol_version"] == "knowledge-search-v2"
    assert run_knowledge_recall_eval.DEFAULT_JSON_OUTPUT_NAME.endswith("-search-v2.json")
    assert run_knowledge_recall_eval.DEFAULT_MARKDOWN_OUTPUT_NAME.endswith("-search-v2.md")
    assert "calibrated" not in run_knowledge_recall_eval.DEFAULT_JSON_OUTPUT_NAME


def test_decision_stats_endpoint_returns_counts_rates_and_percentiles():
    response = CLIENT.get("/api/knowledge/recall-decisions/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "global"
    assert set(payload["action_counts"]) == {"skip", "retrieve", "ask"}
    assert set(payload["latency_ms"]) == {"average", "p50", "p90", "p99"}
    assert 0 <= payload["vector_available_rate"] <= 1
    assert 0 <= payload["timeout_rate"] <= 1


def test_recall_modes_default_to_explicit_and_changes_are_body_free_audited():
    original = knowledge_recall.update_settings(mode="explicit", shadow_enabled=True)
    assert original["mode"] == "explicit"
    smart = CLIENT.patch("/api/knowledge/recall/settings", json={"mode": "smart"})
    assert smart.status_code == 200
    assert smart.json()["mode"] == "smart"
    assert smart.json()["answer_behavior"] == "smart_high_confidence"
    off = CLIENT.patch("/api/knowledge/recall/settings", json={"mode": "off"})
    assert off.status_code == 200 and off.json()["answer_behavior"] == "disabled"
    invalid = CLIENT.patch("/api/knowledge/recall/settings", json={"mode": "unsafe"})
    assert invalid.status_code == 422
    conn = db.connect()
    try:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_recall_mode_events)")
        }
        assert {"before_mode", "after_mode", "actor", "reason_code"} <= columns
        assert not ({"query", "content", "path", "token"} & columns)
        events = conn.execute(
            "SELECT before_mode,after_mode FROM knowledge_recall_mode_events ORDER BY created_at"
        ).fetchall()
        assert ("explicit", "smart") in [tuple(row) for row in events]
        assert ("smart", "off") in [tuple(row) for row in events]
    finally:
        conn.close()
        knowledge_recall.update_settings(mode="explicit", shadow_enabled=True)


def test_k5_authorization_fixture_is_synthetic_and_covers_allow_deny_filter():
    path = Path(__file__).parent / "fixtures" / "knowledge_smart_authorization_v1.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["protocol_version"] == "knowledge-smart-authorization-eval-v1"
    assert fixture["synthetic_only"] is True
    assert {case["user_action"] for case in fixture["cases"]} == {
        "allow_once", "skip", "none",
    }
    assert {case["expected_source"] for case in fixture["cases"]} == {"confirmed", "none"}
    serialized = json.dumps(fixture, ensure_ascii=False).lower()
    assert "grant_token" not in serialized and "api_key" not in serialized


def test_smart_medium_candidate_never_enters_real_prepared_context(monkeypatch):
    candidate = {
        "document_id": "doc", "chunk_id": "chunk", "original_name": "测试资料.md",
        "heading_path": [], "content": "不应注入", "content_sha256": hashlib.sha256(
            "不应注入".encode()
        ).hexdigest(),
    }
    monkeypatch.setattr(knowledge_recall, "evaluate", lambda *_args, **_kwargs: {
        "recall_mode": "smart", "action": "retrieve", "reason_code": "semantic_candidate",
        "confidence_band": "medium", "_selected_results": [candidate],
    })
    from app import knowledge_context
    prepared, decision = knowledge_context.prepare_for_mode(
        "换一种说法", mode="smart", provider={"execution_location": "local"},
    )
    assert prepared is None and decision["confidence_band"] == "medium"


def test_k7_natural_recall_uses_cleaned_query_without_losing_names_numbers_or_english():
    captured = {}

    def search(query, **_kwargs):
        captured["query"] = query
        return {"results": [], "retrieval_mode": "fts", "diagnostics": {}}

    original = "你好，请帮我看看 Nebula Gateway 的 retry budget 2026 是多少，谢谢"
    result = knowledge_recall.evaluate(original, search_fn=search)

    assert captured["query"] != original
    assert "Nebula Gateway" in captured["query"]
    assert "retry budget" in captured["query"]
    assert "2026" in captured["query"]
    assert result["features"]["query_changed"] is True
    assert "_search_query" in result


def test_k7_pronoun_continuation_only_appends_locally_verified_recent_entities(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        knowledge_recall, "recent_context_entities",
        lambda session_id: ["Nebula Gateway"] if session_id == "session-1" else [],
    )

    def search(query, **_kwargs):
        captured["query"] = query
        return {"results": [], "retrieval_mode": "fts", "diagnostics": {}}

    knowledge_recall.evaluate(
        "它的 retry budget 是多少？", session_id="session-1", search_fn=search,
    )
    assert captured["query"].endswith("Nebula Gateway")
    assert knowledge_recall.clean_query(
        "Nebula Gateway 的 retry budget 是多少？",
        context_entities=["Nebula Gateway", "未出现的项目"],
    ).count("Nebula Gateway") == 1

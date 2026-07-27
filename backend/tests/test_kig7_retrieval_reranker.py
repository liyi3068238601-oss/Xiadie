import asyncio
import json
from dataclasses import replace

import pytest

from app import cognitive_decision as cds, db, kig_reranker as reranker, kig_retrieval, kig_sources, llm, memory


def _batch(count=4):
    candidates = []
    for index in range(count):
        item = memory.create_memory("L1", f"星河候选 {index} unique-{db.new_id()}")
        ref = kig_sources.registry.resolve("memory_fragment", item["id"])
        candidates.append(kig_retrieval._candidate(
            source="memory", ref=ref, excerpt=item["content"],
            lexical_score=max(0.05, 0.9 - index * 0.2), vector_score=None,
            occurred_at=float(item["updated_at"]), authority="user_memory",
        ))
    return kig_retrieval.RetrievalBatch(
        candidates=tuple(candidates), diagnostics={}, failed_sources=(), lexical_fallback_sources=(),
    )


def _payload(count=4, max_selected=3):
    return reranker.adapt(
        _batch(count), request_id=f"test-{db.new_id()}", query="哪个星河候选最相关",
        max_selected=max_selected,
    )


def _raw(result):
    return json.dumps({
        **result.__dict__, "selected_ids": list(result.selected_ids),
        "ranked_ids": list(result.ranked_ids),
        "relevance_roles": list(result.relevance_roles),
        "rank_buckets": list(result.rank_buckets),
        "item_confidences": list(result.item_confidences),
        "reason_codes": list(result.reason_codes),
    }, ensure_ascii=False)


def test_adapts_unified_candidates_into_cds_candidate_and_source_snapshots():
    payload = _payload()
    fallback = reranker.deterministic_fusion(payload)
    reranker.validate(payload, fallback)
    assert payload.candidate_ids == tuple(item.id for item in payload.candidates)
    assert tuple(ref.id for ref in payload.candidate_refs) == payload.candidate_ids
    assert all(len(ref.content_hash) == 64 for ref in payload.candidate_refs)
    assert reranker.current_source_snapshot(payload) == reranker.source_snapshot(payload)
    assert fallback.selected_ids == fallback.ranked_ids[:3]
    assert fallback.reason_codes == ("deterministic_fusion",)


def test_model_schema_distinguishes_all_seven_relevance_roles():
    payload = _payload(count=7, max_selected=4)
    roles = ("direct", "partial", "background", "conflict", "outdated", "duplicate", "irrelevant")
    result = reranker.RetrievalRerankResult(
        action="select", selected_ids=payload.candidate_ids[:4],
        ranked_ids=payload.candidate_ids, relevance_roles=roles,
        rank_buckets=("primary", "primary", "secondary", "secondary", "excluded", "excluded", "excluded"),
        item_confidences=("high", "medium", "medium", "medium", "high", "high", "high"),
        reason_codes=("semantic_rerank",), confidence_band="high", proposal_only=True,
    )
    reranker.validate(payload, result)


def test_validator_rejects_invented_duplicate_or_excluded_selection():
    payload = _payload()
    fallback = reranker.deterministic_fusion(payload)
    forged = replace(
        fallback, selected_ids=("invented",), action="select",
    )
    with pytest.raises(cds.DecisionProtocolError) as caught:
        reranker.validate(payload, forged)
    assert caught.value.code == "candidate_not_allowed"
    excluded = replace(
        fallback, selected_ids=(fallback.ranked_ids[-1],), action="select",
        relevance_roles=tuple(
            "irrelevant" if item == fallback.ranked_ids[-1] else role
            for item, role in zip(fallback.ranked_ids, fallback.relevance_roles, strict=True)
        ),
        rank_buckets=tuple(
            "excluded" if item == fallback.ranked_ids[-1] else bucket
            for item, bucket in zip(fallback.ranked_ids, fallback.rank_buckets, strict=True)
        ),
    )
    with pytest.raises(cds.DecisionProtocolError) as caught:
        reranker.validate(payload, excluded)
    assert caught.value.code == "excluded_candidate_selected"
    duplicate = replace(fallback, ranked_ids=(fallback.ranked_ids[0],) * len(fallback.ranked_ids))
    with pytest.raises(cds.DecisionProtocolError):
        reranker.validate(payload, duplicate)


def test_model_failure_uses_deterministic_fusion(monkeypatch):
    payload = _payload()

    async def failed(*_args, **_kwargs):
        raise llm.LLMError("offline", code="observer_model_timeout")

    monkeypatch.setattr(reranker.llm, "complete_json", failed)
    result = asyncio.run(reranker.propose(
        payload, provider={"id": "deepseek", "execution_location": "remote"},
        model="deepseek-chat", remote_authorized=True,
    ))
    assert result["model_called"] is True
    assert result["proposal"] == reranker.deterministic_fusion(payload)
    assert result["outcome"]["fallback_used"] is True
    assert result["outcome"]["application_allowed"] is False


def test_valid_model_result_remains_shadow_and_records_body_free_comparison(monkeypatch):
    payload = _payload()
    fallback = reranker.deterministic_fusion(payload)
    proposed = replace(
        fallback, selected_ids=(fallback.ranked_ids[1], fallback.ranked_ids[0]),
        ranked_ids=(fallback.ranked_ids[1], fallback.ranked_ids[0], *fallback.ranked_ids[2:]),
        reason_codes=("semantic_rerank",), confidence_band="high",
    )

    captured = {}

    async def complete(*_args, **kwargs):
        captured.update(kwargs)
        return {"text": _raw(proposed), "latency_ms": 3, "prompt_tokens": 30, "completion_tokens": 40}

    monkeypatch.setattr(reranker.llm, "complete_json", complete)
    result = asyncio.run(reranker.propose(
        payload, provider={"id": "deepseek", "execution_location": "remote"},
        model="deepseek-chat", remote_authorized=True,
    ))
    assert result["proposal"] == proposed and result["model_called"] is True
    assert result["outcome"]["fallback_used"] is False
    assert result["outcome"]["application_allowed"] is False
    assert captured["json_mode"] is True
    assert result["comparison"] == {
        "selected_jaccard": round(2 / 3, 4), "changed_positions": 2,
        "model_selected_count": 2, "fallback_selected_count": 3,
    }
    assert "星河候选" not in str(result["comparison"])


def test_source_change_rejects_old_model_result_and_fallback_drops_changed_candidate():
    payload = _payload()
    original_fallback = reranker.deterministic_fusion(payload)
    first = payload.candidates[0]
    header = cds.build_header(
        decision_kind=reranker.DECISION_KIND, policy_version=reranker.POLICY_VERSION,
        request_id=f"changed-{db.new_id()}", mode=cds.DecisionMode.SHADOW,
        source_snapshot=reranker.source_snapshot(payload),
    )
    run, _ = cds.create_run(header, payload, payload.candidate_refs)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET status='tombstone',enabled=0,lifecycle_revision=lifecycle_revision+1 "
            "WHERE id=?", (first.source_id,),
        )
        conn.commit()
    finally:
        conn.close()
    outcome = cds.evaluate_output(
        run.id, header, payload, _raw(original_fallback),
        current_snapshot=reranker.current_source_snapshot(payload),
    )
    assert outcome["fallback_used"] is True
    assert outcome["error_code"] == "source_revision_changed"
    assert first.id not in outcome["selected_ids"]


def test_single_candidate_and_unauthorized_remote_bypass_model(monkeypatch):
    called = False

    async def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("model should not be called")

    monkeypatch.setattr(reranker.llm, "complete_json", forbidden)
    single = asyncio.run(reranker.propose(_payload(count=1, max_selected=1)))
    denied = asyncio.run(reranker.propose(
        _payload(), provider={"id": "deepseek", "execution_location": "remote"},
        model="deepseek-chat", remote_authorized=False,
    ))
    assert single["error_code"] == "rerank_not_needed"
    assert denied["error_code"] == "model_not_authorized"
    assert single["model_called"] is denied["model_called"] is called is False


def test_remote_rerank_rejects_local_only_knowledge_before_model(monkeypatch):
    payload = _payload()
    fake_knowledge = replace(
        payload.candidates[0], source="knowledge", source_type="knowledge_chunk",
        privacy_scope="normal:local_only",
    )
    payload = replace(payload, candidates=(fake_knowledge, *payload.candidates[1:]))
    called = False

    async def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("local-only excerpt must not leave the device")

    monkeypatch.setattr(reranker.llm, "complete_json", forbidden)
    result = asyncio.run(reranker.propose(
        payload, provider={"id": "deepseek", "execution_location": "remote"},
        model="deepseek-chat", remote_authorized=True,
    ))
    assert result["error_code"] == "source_changed" or result["error_code"] == "transfer_not_authorized"
    assert result["model_called"] is called is False


def test_registry_is_shadow_and_active_mode_is_forbidden():
    definition = cds.REGISTRY.get(reranker.DECISION_KIND)
    assert definition.mode is cds.DecisionMode.SHADOW
    assert definition.fallback_owner == "kig" and definition.application_owner == "kig_retrieval"
    assert definition.max_candidates == reranker.MAX_CANDIDATES
    payload = _payload()
    header = cds.build_header(
        decision_kind=reranker.DECISION_KIND, policy_version=reranker.POLICY_VERSION,
        request_id=f"active-{db.new_id()}", mode=cds.DecisionMode.ACTIVE,
        source_snapshot=reranker.source_snapshot(payload),
    )
    with pytest.raises(cds.DecisionProtocolError) as caught:
        cds.create_run(header, payload, payload.candidate_refs)
    assert caught.value.code == "mode_not_authorized"


def test_shared_json_completion_mode_is_opt_in(monkeypatch):
    captured = []

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, json):
            captured.append(json)
            return Response()

    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    provider = {"id": "test", "base_url": "https://example.test/v1", "api_key": "key"}
    asyncio.run(llm.complete_json(provider, "model", [], json_mode=True))
    asyncio.run(llm.complete_json(provider, "model", []))
    assert captured[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in captured[1]

from __future__ import annotations

import hashlib
import json

import pytest

from app import candidate_reranker_shadow as reranker
from app import cognitive_decision as cds
from app import lore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidates() -> tuple[reranker.RerankCandidate, ...]:
    return (
        reranker.RerankCandidate("memory-1", "memory", "fragment-1", "3", _hash("memory-1"), "personal_fact", 2, True),
        reranker.RerankCandidate("history-1", "history_turn", "turn-1", "7", _hash("history-1"), "prior_decision", 1, True),
        reranker.RerankCandidate("knowledge-1", "knowledge_evidence_window", "chunk-1", "5", _hash("knowledge-1"), "direct_evidence", 0, True),
        reranker.RerankCandidate("lore-1", "lore_section", "section-1", "1", _hash("lore-1"), "canon_background", 3, True),
    )


def _payload() -> reranker.CandidateRerankerInput:
    candidates = _candidates()
    return reranker.CandidateRerankerInput(
        candidate_ids=tuple(item.id for item in candidates),
        candidates=candidates,
        max_selected=3,
    )


def _source_snapshot(candidates: tuple[reranker.RerankCandidate, ...]) -> tuple[cds.SourceSnapshot, ...]:
    return tuple(
        cds.SourceSnapshot(item.source_kind, item.source_id, item.source_revision, item.content_hash)
        for item in candidates
    )


def test_registry_is_shadow_only_and_preserves_domain_ownership():
    definition = cds.REGISTRY.get(reranker.DECISION_KIND)
    assert definition.input_type is reranker.CandidateRerankerInput
    assert definition.result_type is reranker.CandidateRerankerResult
    assert definition.mode is cds.DecisionMode.SHADOW
    assert definition.fallback_owner == definition.application_owner == "candidate_domains"
    assert definition.privacy_class == "user_private_body_free"


def test_four_domain_envelopes_keep_source_specific_purposes_and_legacy_fallback_order():
    payload = _payload()
    result = reranker.legacy_fallback(payload)
    reranker.validate(payload, result)
    assert {item.source_kind for item in payload.candidates} == {
        "memory", "history_turn", "knowledge_evidence_window", "lore_section",
    }
    assert result.selected_ids == ("memory-1", "history-1", "knowledge-1")
    assert result.purpose_codes == ("personal_fact", "prior_decision", "direct_evidence")
    assert result.reason_codes == ("legacy_order",)


def test_validator_rejects_cross_domain_purpose_and_non_candidate_selection():
    payload = _payload()
    with pytest.raises(cds.DecisionProtocolError) as exc:
        reranker.validate(payload, reranker.CandidateRerankerResult(
            action="select",
            selected_ids=("memory-1",),
            purpose_codes=("direct_evidence",),
            reason_codes=("semantic_relevance",),
            confidence_band="high",
        ))
    assert exc.value.code == "purpose_not_allowed"

    with pytest.raises(cds.DecisionProtocolError) as exc:
        reranker.validate(payload, reranker.CandidateRerankerResult(
            action="select",
            selected_ids=("invented",),
            purpose_codes=("personal_fact",),
            reason_codes=("semantic_relevance",),
            confidence_band="high",
        ))
    assert exc.value.code == "candidate_not_allowed"


def test_unavailable_candidate_is_never_selected_even_by_legacy_fallback():
    candidates = tuple(
        reranker.RerankCandidate(
            item.id, item.source_kind, item.source_id, item.source_revision,
            item.content_hash, item.purpose, item.legacy_rank,
            False if item.id == "knowledge-1" else item.source_available,
        )
        for item in _candidates()
    )
    payload = reranker.CandidateRerankerInput(
        candidate_ids=tuple(item.id for item in candidates), candidates=candidates, max_selected=3,
    )
    result = reranker.legacy_fallback(payload)
    reranker.validate(payload, result)
    assert "knowledge-1" not in result.selected_ids


def test_changed_source_fails_closed_and_shadow_never_applies():
    payload = _payload()
    source = _source_snapshot(payload.candidates)
    header = cds.build_header(
        decision_kind=reranker.DECISION_KIND,
        policy_version=reranker.POLICY_VERSION,
        request_id="reranker-shadow-run",
        mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    refs = tuple(cds.CandidateRef(item.id, item.source_kind, item.content_hash) for item in payload.candidates)
    run, _ = cds.create_run(header, payload, refs)
    result = reranker.CandidateRerankerResult(
        action="select",
        selected_ids=("knowledge-1", "memory-1"),
        purpose_codes=("direct_evidence", "personal_fact"),
        reason_codes=("semantic_relevance",),
        confidence_band="high",
    )
    raw = json.dumps({
        **result.__dict__,
        "selected_ids": list(result.selected_ids),
        "purpose_codes": list(result.purpose_codes),
        "reason_codes": list(result.reason_codes),
    })
    changed = tuple(
        cds.SourceSnapshot(item.kind, item.id, item.revision, "f" * 64)
        if item.id == "chunk-1" else item
        for item in source
    )
    outcome = cds.evaluate_output(run.id, header, payload, raw, current_snapshot=changed)
    assert outcome["application_allowed"] is False
    assert outcome["fallback_used"] is True
    assert outcome["error_code"] == "source_revision_changed"


def test_active_mode_is_rejected_before_run_creation():
    payload = _payload()
    source = _source_snapshot(payload.candidates)
    header = cds.build_header(
        decision_kind=reranker.DECISION_KIND,
        policy_version=reranker.POLICY_VERSION,
        request_id="reranker-active-run",
        mode=cds.DecisionMode.ACTIVE,
        source_snapshot=source,
    )
    refs = tuple(cds.CandidateRef(item.id, item.source_kind, item.content_hash) for item in payload.candidates)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        cds.create_run(header, payload, refs)
    assert exc.value.code == "mode_not_authorized"


def test_run_creation_rejects_candidate_ref_that_disagrees_with_domain_envelope():
    payload = _payload()
    source = _source_snapshot(payload.candidates)
    header = cds.build_header(
        decision_kind=reranker.DECISION_KIND,
        policy_version=reranker.POLICY_VERSION,
        request_id="reranker-candidate-binding",
        mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    refs = tuple(
        cds.CandidateRef(
            item.id,
            "memory" if item.id == "knowledge-1" else item.source_kind,
            _hash("forged") if item.id == "memory-1" else item.content_hash,
        )
        for item in payload.candidates
    )
    with pytest.raises(cds.DecisionProtocolError) as exc:
        cds.create_run(header, payload, refs)
    assert exc.value.code == "candidate_snapshot_mismatch"


def test_legacy_fallback_never_compares_domain_local_ranks_across_sources():
    payload = _payload()
    result = reranker.legacy_fallback(payload)
    assert result.selected_ids == ("memory-1", "history-1", "knowledge-1")


def test_validator_rejects_duplicate_selected_candidate_ids():
    payload = _payload()
    with pytest.raises(cds.DecisionProtocolError) as exc:
        reranker.validate(payload, reranker.CandidateRerankerResult(
            action="select",
            selected_ids=("memory-1", "memory-1"),
            purpose_codes=("personal_fact", "personal_fact"),
            reason_codes=("semantic_relevance",),
            confidence_band="high",
        ))
    assert exc.value.code == "selection_duplicate"


def test_real_read_only_domain_results_adapt_to_body_free_envelopes():
    memory_results = [{
        "id": "fragment-real", "content": "用户偏好安静的夜晚", "kind": "preference",
        "lifecycle_revision": 4, "source_available": True,
    }]
    history_turns = [{
        "session_id": "session-old", "user_message_id": "user-old",
        "assistant_message_id": "assistant-old", "user_text": "采用哪个方案？",
        "assistant_text": "采用蓝色方案。", "user_created_at": 10.0,
        "assistant_created_at": 11.0, "score": 8.5,
    }]
    knowledge_results = [{
        "chunk_id": "chunk-real", "content": "迁移应先双读再切换。",
        "content_sha256": _hash("迁移应先双读再切换。"), "match_type": "primary",
        "document_id": "document-real", "ordinal": 2, "rank": -1.5,
    }]
    lore_results = lore.retrieve_lore_candidates("说说你和玻吕茜亚妹妹的过去")

    candidates = (
        *reranker.adapt_memory_results(memory_results),
        *reranker.adapt_history_turns(history_turns),
        *reranker.adapt_knowledge_results(knowledge_results),
        *reranker.adapt_lore_sections(lore_results),
    )

    assert {item.source_kind for item in candidates} == {
        "memory", "history_turn", "knowledge_evidence_window", "lore_section",
    }
    assert [item.purpose for item in candidates[:3]] == [
        "preference", "conversation_continuity", "direct_evidence",
    ]
    assert all(item.purpose == "canon_background" for item in candidates[3:])
    assert all(len(item.content_hash) == 64 and item.source_revision for item in candidates)
    assert set(reranker.RerankCandidate.__dataclass_fields__) == {
        "id", "source_kind", "source_id", "source_revision", "content_hash",
        "purpose", "legacy_rank", "source_available",
    }


def test_adapters_preserve_domain_order_and_availability_without_mutating_results():
    memory_results = [
        {"id": "m2", "content": "第二条", "lifecycle_revision": 2, "source_available": False},
        {"id": "m1", "content": "第一条", "lifecycle_revision": 1, "source_available": True},
    ]
    before = json.loads(json.dumps(memory_results, ensure_ascii=False))

    candidates = reranker.adapt_memory_results(memory_results)

    assert [item.source_id for item in candidates] == ["m2", "m1"]
    assert [item.legacy_rank for item in candidates] == [0, 1]
    assert [item.source_available for item in candidates] == [False, True]
    assert memory_results == before


def test_lore_candidate_entry_matches_legacy_rendering_and_has_stable_identity():
    query = "说说你和玻吕茜亚妹妹的过去"
    first = lore.retrieve_lore_candidates(query)
    second = lore.retrieve_lore_candidates(query)

    assert first == second and first
    assert lore.retrieve_lore(query) == "\n\n".join(item["content"] for item in first)
    assert all({
        "section_id", "revision", "content_sha256", "content", "legacy_rank",
        "source_available",
    } <= set(item) for item in first)
    assert all(len(item["revision"]) == len(item["content_sha256"]) == 64 for item in first)

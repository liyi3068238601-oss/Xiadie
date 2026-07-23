from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Sequence

from . import cognitive_decision as cds

DECISION_KIND = "candidate_reranker"
POLICY_VERSION = "candidate-reranker-shadow-policy-v1"

SOURCE_PURPOSES = {
    "memory": frozenset({"personal_fact", "preference", "relationship_context", "current_plan"}),
    "history_turn": frozenset({"prior_decision", "verbatim_context", "conversation_continuity"}),
    "knowledge_evidence_window": frozenset({"direct_evidence", "background", "conflict", "outdated"}),
    "lore_section": frozenset({"canon_background", "character_context", "world_rule"}),
}
REASON_CODES = frozenset({"semantic_relevance", "legacy_order"})
MAX_CANDIDATES = 32


@dataclass(frozen=True)
class RerankCandidate:
    id: str
    source_kind: str
    source_id: str
    source_revision: str
    content_hash: str
    purpose: str
    legacy_rank: int
    source_available: bool


@dataclass(frozen=True)
class CandidateRerankerInput:
    candidate_ids: tuple[str, ...]
    candidates: tuple[RerankCandidate, ...]
    max_selected: int

    @property
    def candidate_refs(self) -> tuple[cds.CandidateRef, ...]:
        return tuple(
            cds.CandidateRef(item.id, item.source_kind, item.content_hash)
            for item in self.candidates
        )


@dataclass(frozen=True)
class CandidateRerankerResult:
    action: str
    selected_ids: tuple[str, ...]
    purpose_codes: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str


def adapt_memory_results(results: Sequence[Mapping[str, object]]) -> tuple[RerankCandidate, ...]:
    return tuple(
        _candidate(
            source_kind="memory",
            source_id=str(item.get("id") or ""),
            source_revision=str(item.get("lifecycle_revision") or item.get("updated_at") or "1"),
            content_hash=_content_hash(item),
            purpose=_memory_purpose(item),
            legacy_rank=index,
            source_available=bool(item.get("source_available", True)),
        )
        for index, item in enumerate(results)
    )


def adapt_history_turns(turns: Sequence[Mapping[str, object]]) -> tuple[RerankCandidate, ...]:
    return tuple(
        _candidate(
            source_kind="history_turn",
            source_id=(
                f"{item.get('session_id') or ''}:"
                f"{item.get('user_message_id') or ''}:{item.get('assistant_message_id') or ''}"
            ),
            source_revision=_canonical_hash({
                "user_created_at": item.get("user_created_at"),
                "assistant_created_at": item.get("assistant_created_at"),
            }),
            content_hash=_canonical_hash({
                "user_text": item.get("user_text") or "",
                "assistant_text": item.get("assistant_text") or "",
            }),
            purpose="conversation_continuity",
            legacy_rank=index,
            source_available=bool(item.get("source_available", True)),
        )
        for index, item in enumerate(turns)
    )


def adapt_knowledge_results(results: Sequence[Mapping[str, object]]) -> tuple[RerankCandidate, ...]:
    return tuple(
        _candidate(
            source_kind="knowledge_evidence_window",
            source_id=str(item.get("chunk_id") or ""),
            source_revision=_canonical_hash({
                "document_id": item.get("document_id") or "",
                "ordinal": item.get("ordinal"),
                "content_sha256": item.get("content_sha256") or "",
            }),
            content_hash=str(item.get("content_sha256") or _content_hash(item)),
            purpose="direct_evidence" if item.get("match_type") != "context" else "background",
            legacy_rank=index,
            source_available=bool(item.get("source_available", True)),
        )
        for index, item in enumerate(results)
    )


def adapt_lore_sections(sections: Sequence[Mapping[str, object]]) -> tuple[RerankCandidate, ...]:
    return tuple(
        _candidate(
            source_kind="lore_section",
            source_id=str(item.get("section_id") or ""),
            source_revision=str(item.get("revision") or ""),
            content_hash=str(item.get("content_sha256") or _content_hash(item)),
            purpose="canon_background",
            legacy_rank=int(item.get("legacy_rank") or index),
            source_available=bool(item.get("source_available", True)),
        )
        for index, item in enumerate(sections)
    )


def _candidate(
    *, source_kind: str, source_id: str, source_revision: str, content_hash: str,
    purpose: str, legacy_rank: int, source_available: bool,
) -> RerankCandidate:
    candidate_id = f"{source_kind}:{source_id}"
    return RerankCandidate(
        candidate_id, source_kind, source_id, source_revision, content_hash,
        purpose, legacy_rank, source_available,
    )


def _memory_purpose(item: Mapping[str, object]) -> str:
    kind = str(item.get("kind") or "")
    if kind == "preference":
        return "preference"
    if kind in {"relationship", "relationship_context"}:
        return "relationship_context"
    if kind in {"plan", "current_plan"}:
        return "current_plan"
    return "personal_fact"


def _content_hash(item: Mapping[str, object]) -> str:
    return hashlib.sha256(str(item.get("content") or "").encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def legacy_fallback(payload: CandidateRerankerInput) -> CandidateRerankerResult:
    domain_order = tuple(dict.fromkeys(item.source_kind for item in payload.candidates))
    selected = tuple(
        item
        for source_kind in domain_order
        for item in sorted(
            (
                candidate
                for candidate in payload.candidates
                if candidate.source_kind == source_kind and candidate.source_available
            ),
            key=lambda candidate: (candidate.legacy_rank, candidate.id),
        )
    )[:payload.max_selected]
    return CandidateRerankerResult(
        action=cds.DecisionAction.SELECT.value if selected else cds.DecisionAction.SKIP.value,
        selected_ids=tuple(item.id for item in selected),
        purpose_codes=tuple(item.purpose for item in selected),
        reason_codes=("legacy_order",),
        confidence_band=cds.ConfidenceBand.MEDIUM.value if selected else cds.ConfidenceBand.LOW.value,
    )


def validate(payload: CandidateRerankerInput, result: CandidateRerankerResult) -> None:
    if not isinstance(payload.candidate_ids, tuple) or not isinstance(payload.candidates, tuple):
        raise cds.DecisionProtocolError("input_schema_invalid", "reranker input must use tuples")
    if not 1 <= len(payload.candidates) <= MAX_CANDIDATES:
        raise cds.DecisionProtocolError("candidate_limit_invalid", "reranker candidate count is invalid")
    if payload.candidate_ids != tuple(item.id for item in payload.candidates):
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "candidate IDs and envelopes differ")
    if len(payload.candidate_ids) != len(set(payload.candidate_ids)):
        raise cds.DecisionProtocolError("candidate_duplicate", "candidate IDs must be unique")
    if not 1 <= payload.max_selected <= len(payload.candidates):
        raise cds.DecisionProtocolError("selection_limit_invalid", "selection limit is invalid")
    for item in payload.candidates:
        if item.source_kind not in SOURCE_PURPOSES:
            raise cds.DecisionProtocolError("source_kind_not_allowed", "candidate source kind is unsupported")
        if item.purpose not in SOURCE_PURPOSES[item.source_kind]:
            raise cds.DecisionProtocolError("purpose_not_allowed", "candidate purpose is invalid for its source")
        if not item.source_id or not item.source_revision or item.legacy_rank < 0:
            raise cds.DecisionProtocolError("candidate_identity_invalid", "candidate envelope is incomplete")
        cds.CandidateRef(item.id, item.source_kind, item.content_hash)
    if not isinstance(result.selected_ids, tuple) or not isinstance(result.purpose_codes, tuple):
        raise cds.DecisionProtocolError("output_schema_invalid", "reranker result must use tuples")
    if len(result.selected_ids) != len(result.purpose_codes):
        raise cds.DecisionProtocolError("purpose_count_mismatch", "every selected candidate needs one purpose")
    if len(result.selected_ids) != len(set(result.selected_ids)):
        raise cds.DecisionProtocolError("selection_duplicate", "selected candidate IDs must be unique")
    if len(result.selected_ids) > payload.max_selected:
        raise cds.DecisionProtocolError("selection_limit_exceeded", "too many candidates selected")
    candidates = {item.id: item for item in payload.candidates}
    for candidate_id, purpose in zip(result.selected_ids, result.purpose_codes, strict=True):
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise cds.DecisionProtocolError("candidate_not_allowed", "result contains a non-candidate ID")
        if not candidate.source_available:
            raise cds.DecisionProtocolError("source_unavailable", "result selected an unavailable source")
        if purpose != candidate.purpose or purpose not in SOURCE_PURPOSES[candidate.source_kind]:
            raise cds.DecisionProtocolError("purpose_not_allowed", "result changed a domain purpose")
    if result.action == cds.DecisionAction.SELECT.value and not result.selected_ids:
        raise cds.DecisionProtocolError("selection_empty", "select action requires candidates")
    if result.action == cds.DecisionAction.SKIP.value and result.selected_ids:
        raise cds.DecisionProtocolError("selection_action_mismatch", "skip action cannot select candidates")
    if result.action not in {cds.DecisionAction.SELECT.value, cds.DecisionAction.SKIP.value}:
        raise cds.DecisionProtocolError("action_not_allowed", "reranker action is invalid")
    if not set(result.reason_codes).issubset(REASON_CODES) or not result.reason_codes:
        raise cds.DecisionProtocolError("reason_code_not_allowed", "reranker reason code is invalid")
    if result.confidence_band not in {item.value for item in cds.ConfidenceBand}:
        raise cds.DecisionProtocolError("confidence_invalid", "confidence band is invalid")


cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=DECISION_KIND,
    input_type=CandidateRerankerInput,
    result_type=CandidateRerankerResult,
    input_schema_version="candidate-reranker-input-v1",
    output_schema_version="candidate-reranker-result-v1",
    validator=validate,
    validator_version="candidate-reranker-validator-v1",
    fallback=legacy_fallback,
    fallback_version="candidate-reranker-domain-order-fallback-v1",
    fallback_owner="candidate_domains",
    application_owner="candidate_domains",
    privacy_class="user_private_body_free",
    max_candidates=MAX_CANDIDATES,
    timeout_seconds=8.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash("candidate-reranker-shadow-v1"),
))

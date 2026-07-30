"""KIG-R chat orchestration without bypassing CTX or owner-system controls."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from itertools import combinations

from . import (
    context_contributions, db, kig_evidence, kig_governance, kig_query_planner,
    kig_reranker, kig_retrieval, kig_sources, knowledge_context, knowledge_recall,
)

PROTOCOL_VERSION = "kig-retrieval-governance-v1"
MAX_GOVERNANCE_CANDIDATES = 10
MAX_RELATION_PAIRS = 24
_CONTRIBUTION_DIRECTIVE = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior).{0,32}instructions?|"
    r"忽略(?:以上|此前|之前).{0,32}(?:指令|要求)|"
    r"<\s*/?\s*(?:system|developer|assistant|tool)\b|"
    r"(?:system|developer)\s*(?:prompt|message)\s*:)",
    re.IGNORECASE,
)
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")


@dataclass(frozen=True)
class ChatRetrievalResult:
    bundle: kig_evidence.KnowledgeRetrievalBundle
    plan: kig_query_planner.QueryPlanResult
    batch: kig_retrieval.RetrievalBatch
    selected_candidate_ids: tuple[str, ...]
    allowed_knowledge_chunk_ids: frozenset[str]
    freshness: kig_governance.FreshnessAssessment
    governed_sources: tuple[kig_governance.GovernedSource, ...]
    deterministic_relations: tuple[kig_governance.VersionRelationResult, ...]
    deterministic_relation_count: int
    proposed_confirmation_count: int
    protocol_version: str = PROTOCOL_VERSION


@dataclass(frozen=True)
class ContextContributionGovernance:
    accepted: tuple[context_contributions.GovernedContribution, ...]
    rejected_reason_counts: dict[str, int]
    candidate_count: int
    protocol_version: str = context_contributions.PROTOCOL_VERSION


def govern_context_contributions(
    batch: context_contributions.CollectionBatch, *, provider: dict | None,
    temporary_chat: bool, now: float | None = None,
) -> ContextContributionGovernance:
    """KIG validates permission, freshness and evidence before CTX can see bodies."""
    current_time = db.now() if now is None else float(now)
    location = str((provider or {}).get("execution_location") or "local")
    id_counts = Counter(
        item.contribution_id
        for item in batch.contributions
        if isinstance(item, context_contributions.ContextContribution)
        and isinstance(item.contribution_id, str)
    )
    duplicate_ids = {item_id for item_id, count in id_counts.items() if count > 1}
    accepted: list[context_contributions.GovernedContribution] = []
    rejected: Counter[str] = Counter()
    for candidate in batch.contributions:
        reason = _context_contribution_rejection(
            candidate, batch=batch, provider_location=location,
            temporary_chat=temporary_chat, current_time=current_time,
            duplicate_ids=duplicate_ids,
        )
        if reason:
            rejected[reason] += 1
            continue
        evidence_locators: list[str] = []
        evidence_changed = False
        for item in candidate.evidence:
            try:
                current = kig_sources.registry.resolve(item.source_kind, item.source_id)
            except kig_sources.SourceRefError:
                evidence_changed = True
                break
            if (
                current.status != "active"
                or current.revision != item.revision
                or current.content_hash != item.content_hash
                or (
                    location != "local"
                    and not _context_evidence_remote_allowed(current)
                )
            ):
                evidence_changed = True
                break
            evidence_locators.append(current.locator)
        if evidence_changed:
            rejected["evidence_changed_during_governance"] += 1
            continue
        accepted.append(context_contributions.GovernedContribution(
            contribution_id=candidate.contribution_id,
            source=candidate.source,
            kind=candidate.kind,
            revision=candidate.revision,
            content_hash=candidate.content_hash,
            privacy=candidate.privacy,
            priority=candidate.priority,
            token_estimate=candidate.token_estimate,
            text=str(candidate.candidate_payload.get("text") or "").strip(),
            label=str(candidate.candidate_payload.get("label") or "")[:120],
            evidence_locators=tuple(evidence_locators),
        ))
    accepted.sort(key=lambda item: (-item.priority, item.source, item.contribution_id))
    context_contributions.record_governance(
        batch.request_id, accepted_count=len(accepted), rejected_counts=rejected,
    )
    return ContextContributionGovernance(
        accepted=tuple(accepted),
        rejected_reason_counts=dict(sorted(rejected.items())),
        candidate_count=len(batch.contributions),
    )


def _context_contribution_rejection(
    candidate: object, *, batch: context_contributions.CollectionBatch,
    provider_location: str, temporary_chat: bool, current_time: float,
    duplicate_ids: set[str],
) -> str | None:
    if not isinstance(candidate, context_contributions.ContextContribution):
        return "schema_invalid"
    spec = batch.specs.get(candidate.source)
    if candidate.protocol_version != context_contributions.PROTOCOL_VERSION:
        return "protocol_invalid"
    if not spec or candidate.source != spec.contributor_id:
        return "source_unregistered"
    if not context_contributions.CONTRIBUTION_ID_PATTERN.fullmatch(candidate.contribution_id):
        return "id_invalid"
    if candidate.contribution_id in duplicate_ids:
        return "duplicate_id"
    if candidate.kind not in spec.allowed_kinds:
        return "kind_forbidden"
    if candidate.privacy not in spec.allowed_privacy:
        return "privacy_forbidden"
    if provider_location != "local" and candidate.privacy not in {"remote_allowed", "public"}:
        return "remote_transfer_forbidden"
    if not isinstance(candidate.revision, str) or not candidate.revision[:128]:
        return "revision_invalid"
    if not context_contributions.HASH_PATTERN.fullmatch(candidate.content_hash):
        return "hash_invalid"
    if not isinstance(candidate.candidate_payload, dict):
        return "payload_schema_invalid"
    if set(candidate.candidate_payload) - {"text", "label"}:
        return "payload_schema_invalid"
    text = candidate.candidate_payload.get("text")
    label = candidate.candidate_payload.get("label", "")
    if not isinstance(text, str) or not text.strip() or not isinstance(label, str):
        return "payload_schema_invalid"
    if len(text) > context_contributions.MAX_PAYLOAD_CHARS or len(label) > 120:
        return "payload_too_large"
    normalized_text = _normalize_untrusted_text(text)
    normalized_label = _normalize_untrusted_text(label)
    if (_CONTRIBUTION_DIRECTIVE.search(normalized_text)
            or _CONTRIBUTION_DIRECTIVE.search(normalized_label)):
        return "prompt_injection_detected"
    if context_contributions.payload_hash(candidate.candidate_payload) != candidate.content_hash:
        return "hash_mismatch"
    try:
        created_at = float(candidate.created_at)
        expires_at = float(candidate.expires_at)
        priority = int(candidate.priority)
        estimate = int(candidate.token_estimate)
    except (TypeError, ValueError):
        return "numeric_field_invalid"
    if created_at > current_time + 30 or expires_at <= current_time:
        return "expired_or_future"
    if expires_at <= created_at or expires_at - created_at > context_contributions.MAX_TTL_SECONDS:
        return "ttl_invalid"
    if not 0 <= priority <= 100:
        return "priority_invalid"
    actual_tokens = knowledge_context.estimate_tokens(text)
    if not actual_tokens <= estimate <= context_contributions.MAX_TOKEN_ESTIMATE:
        return "token_estimate_invalid"
    if not candidate.evidence or len(candidate.evidence) > 8:
        return "evidence_missing"
    for evidence in candidate.evidence:
        try:
            current = kig_sources.registry.resolve(evidence.source_kind, evidence.source_id)
        except kig_sources.SourceRefError:
            return "evidence_unavailable"
        if current.status != "active":
            return "evidence_inactive"
        if current.revision != evidence.revision or current.content_hash != evidence.content_hash:
            return "evidence_stale"
        if temporary_chat and current.source_kind in {"message", "memory_fragment", "life_event"}:
            return "temporary_chat_boundary"
        if provider_location != "local" and not _context_evidence_remote_allowed(current):
            return "evidence_remote_forbidden"
    return None


def _context_evidence_remote_allowed(source: kig_sources.SourceRef) -> bool:
    if source.source_kind in {"knowledge_document", "knowledge_chunk"}:
        return source.privacy_scope.endswith(":remote_allowed")
    return source.source_kind == "lore_section" and source.privacy_scope == "public"


def _normalize_untrusted_text(value: str) -> str:
    """Collapse compatibility glyphs and invisible separators before inspection."""
    return _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", str(value or "")))


def prepare_for_chat(
    *, query: str, source_message_id: str, session_id: str,
    provider: dict | None, recall_mode: str,
    authorized_knowledge_chunk_ids: frozenset[str] = frozenset(),
    temporary_chat: bool = False,
) -> ChatRetrievalResult | None:
    """Use deterministic KIG decisions in chat; semantic model proposals stay Shadow."""
    if db.get_setting("kig_enabled", "1") != "1" or recall_mode == "off" \
            or not str(query or "").strip() or not source_message_id:
        return None
    if knowledge_recall.is_companion_smalltalk(query):
        return None
    enabled = _enabled_sources(provider, temporary_chat=temporary_chat)
    payload = kig_query_planner.QueryPlanInput(
        candidate_ids=kig_query_planner.candidate_ids(), source_message_id=source_message_id,
        text=query, enabled_sources=enabled,
    )
    plan = kig_query_planner.plan_programmatic(payload)
    if plan is None:
        # Ambiguous reference resolution remains model-Shadow. Do not apply it.
        plan = kig_query_planner.safe_fallback(payload)
    kig_query_planner.validate(payload, plan)
    if not plan.selected_sources or set(plan.selected_sources) <= {"knowledge"} \
            and not plan.version_required and not plan.conflict_required:
        return None
    request = kig_retrieval.RetrievalRequest(
        query=plan.subqueries[0] if plan.subqueries else query,
        selected_sources=plan.selected_sources,
        per_source_limits={source: 6 for source in plan.selected_sources},
        total_limit=24, current_session_id=session_id,
    )
    batch = kig_retrieval.retrieve(request)
    batch = _filter_knowledge_authorization(batch, authorized_knowledge_chunk_ids)
    batch = _filter_transfer(batch, provider)
    if not batch.candidates:
        bundle = kig_evidence.build_bundle(
            query=query, request_id=f"kig-chat:{source_message_id}:{db.new_id()}",
            selected_sources=plan.selected_sources, batch=batch,
            planner_protocol=kig_query_planner.POLICY_VERSION,
            query_plan_summary=_plan_summary(plan),
        )
        return ChatRetrievalResult(
            bundle=bundle, plan=plan, batch=batch, selected_candidate_ids=(),
            allowed_knowledge_chunk_ids=frozenset(),
            freshness=kig_governance.FreshnessAssessment({}, (), (), (),
                                                          ("no_candidates",)),
            governed_sources=(), deterministic_relations=(),
            deterministic_relation_count=0, proposed_confirmation_count=0,
        )

    rerank_input = kig_reranker.adapt(
        batch, request_id=source_message_id, query=query, max_selected=12,
    )
    # The semantic reranker remains Shadow. Only its independently validated,
    # deterministic fallback may determine the active CTX candidate set.
    baseline = kig_reranker.deterministic_fusion(rerank_input)
    candidate_by_id = {item.candidate_id: item for item in batch.candidates}
    governed = tuple(
        kig_governance.adapt_candidate(candidate_by_id[candidate_id])
        for candidate_id in baseline.selected_ids[:MAX_GOVERNANCE_CANDIDATES]
        if candidate_id in candidate_by_id
    )
    relations: list[kig_governance.VersionRelationResult] = []
    for pair_index, (left, right) in enumerate(combinations(governed, 2)):
        if pair_index >= MAX_RELATION_PAIRS:
            break
        relation = kig_governance.deterministic_relation(left, right, query=query)
        if relation is None:
            continue
        relations.append(relation)
    persisted, proposed_confirmations = _persisted_relations(governed)
    combined = _deduplicate_relations([*relations, *persisted])
    freshness = kig_governance.assess_freshness(governed, combined)
    active_ids = tuple(
        candidate_id for candidate_id in baseline.selected_ids
        if freshness.states.get(candidate_id, "current") not in {"superseded", "expired"}
    )
    roles = dict(zip(
        baseline.ranked_ids, baseline.relevance_roles, strict=True,
    ))
    for left, right in freshness.conflict_pairs:
        roles[left] = roles[right] = "conflict"
    bundle = kig_evidence.build_bundle(
        query=query, request_id=f"kig-chat:{source_message_id}:{db.new_id()}",
        selected_sources=plan.selected_sources, batch=batch, selected_ids=active_ids,
        relevance_roles=roles, freshness_states=freshness.states,
        planner_protocol=kig_query_planner.POLICY_VERSION,
        query_plan_summary=_plan_summary(plan),
    )
    conflicts = list(bundle.conflict_notes)
    insufficiencies = list(bundle.insufficiency_notes)
    if freshness.conflict_pairs:
        conflicts.append("version_conflict_unresolved")
    if proposed_confirmations:
        conflicts.append("high_impact_confirmation_required")
    if plan.conflict_required and not combined:
        insufficiencies.append("semantic_conflict_check_shadow_only")
    bundle = replace(
        bundle, conflict_notes=tuple(dict.fromkeys(conflicts)),
        insufficiency_notes=tuple(dict.fromkeys(insufficiencies)),
    )
    allowed_knowledge = frozenset(
        candidate_by_id[candidate_id].source_id for candidate_id in active_ids
        if candidate_id in candidate_by_id
        and candidate_by_id[candidate_id].source_type == "knowledge_chunk"
    )
    return ChatRetrievalResult(
        bundle=bundle, plan=plan, batch=batch, selected_candidate_ids=active_ids,
        allowed_knowledge_chunk_ids=allowed_knowledge, freshness=freshness,
        governed_sources=governed, deterministic_relations=tuple(relations),
        deterministic_relation_count=len(relations),
        proposed_confirmation_count=len(proposed_confirmations),
    )


def persist_deterministic_relations(result: ChatRetrievalResult) -> int:
    by_id = {item.candidate_id: item for item in result.governed_sources}
    persisted = 0
    for index, relation in enumerate(result.deterministic_relations):
        if relation.relation in {"exact_duplicate", "unrelated", "uncertain"}:
            continue
        older, newer = by_id.get(relation.older_id), by_id.get(relation.newer_id)
        if not older or not newer:
            continue
        payload = kig_governance.VersionRelationInput(
            candidate_ids=(older.candidate_id, newer.candidate_id),
            request_id=f"{result.bundle.request_id}:relation:{index}",
            query="deterministic version governance", sources=(older, newer),
            impact_level="high" if result.bundle.high_risk else "medium",
        )
        kig_governance.persist_relation(relation, payload)
        persisted += 1
    return persisted


def filter_knowledge_prepared(
    prepared: dict | None, result: ChatRetrievalResult | None,
) -> dict | None:
    if not prepared or not result or "knowledge" not in result.plan.selected_sources:
        return prepared
    # Never broaden the grant-authorized legacy set. If KIG found no matching
    # knowledge candidate, retain the authorized legacy result and expose the
    # insufficiency note rather than deleting all context accidentally.
    if not result.allowed_knowledge_chunk_ids:
        return prepared
    return knowledge_context.filter_prepared(prepared, set(result.allowed_knowledge_chunk_ids))


def _enabled_sources(provider: dict | None, *, temporary_chat: bool = False) -> tuple[str, ...]:
    sources = list(kig_query_planner.SOURCES)
    if temporary_chat:
        sources = [source for source in sources if source not in {"memory", "history"}]
    elif db.get_setting("memory_enabled", "1") != "1" and "memory" in sources:
        sources.remove("memory")
    if db.get_setting("conversation_history_recall_mode", "explicit_only") == "off" \
            and "history" in sources:
        sources.remove("history")
    if db.get_setting("life_enabled", "1") != "1" and "life" in sources:
        sources.remove("life")
    if provider and provider.get("execution_location") == "remote" \
            and db.get_setting("kig_remote_task_evidence", "0") != "1":
        sources.remove("task")
    return tuple(sources)


def _filter_transfer(
    batch: kig_retrieval.RetrievalBatch, provider: dict | None,
) -> kig_retrieval.RetrievalBatch:
    if not provider or provider.get("execution_location") != "remote":
        return batch
    task_evidence_allowed = db.get_setting("kig_remote_task_evidence", "0") == "1"
    allowed_scopes = {
        "message": frozenset({"private"}),
        "memory_fragment": frozenset({"normal"}),
        "life_event": frozenset({"private"}),
        "tool_run": frozenset({"private"}) if task_evidence_allowed else frozenset(),
        "lore_section": frozenset({"public"}),
    }

    def allowed(item: kig_retrieval.RetrievalCandidate) -> bool:
        if item.source_type == "knowledge_chunk":
            try:
                kig_sources.validate_privacy_scope(item.source_type, item.privacy_scope)
            except kig_sources.SourceRefError:
                return False
            # The preceding owner-grant filter is the authority for whether a
            # valid knowledge policy may cross this turn's remote boundary.
            return True
        return item.privacy_scope in allowed_scopes.get(item.source_type, frozenset())

    candidates = tuple(item for item in batch.candidates if allowed(item))
    return replace(batch, candidates=candidates)


def _filter_knowledge_authorization(
    batch: kig_retrieval.RetrievalBatch,
    authorized_chunk_ids: frozenset[str],
) -> kig_retrieval.RetrievalBatch:
    """Keep KIG inside the owner Knowledge system's per-turn grant boundary."""
    candidates = tuple(
        item for item in batch.candidates
        if item.source_type != "knowledge_chunk" or item.source_id in authorized_chunk_ids
    )
    return replace(batch, candidates=candidates)


def _persisted_relations(
    governed: tuple[kig_governance.GovernedSource, ...],
) -> tuple[list[kig_governance.VersionRelationResult], list[str]]:
    by_ref = {
        (item.source_kind, item.source_id, item.source_revision): item for item in governed
    }
    if not by_ref:
        return [], []
    # Restrict before ordering/limiting. A global "latest 200" window can hide
    # an older user-confirmed relation once unrelated projects create enough
    # newer rows, causing the same evidence pair to be governed differently as
    # the database grows.
    refs = tuple(by_ref)
    values_sql = ",".join("(?,?,?)" for _ in refs)
    params = tuple(value for ref in refs for value in ref)
    conn = db.connect()
    try:
        rows = conn.execute(
            f"WITH candidate_refs(source_kind,source_id,source_revision) AS "
            f"(VALUES {values_sql}) "
            "SELECT relation.* FROM kig_version_relations AS relation "
            "JOIN candidate_refs AS older ON "
            "older.source_kind=relation.older_source_kind AND "
            "older.source_id=relation.older_source_id AND "
            "older.source_revision=relation.older_source_revision "
            "JOIN candidate_refs AS newer ON "
            "newer.source_kind=relation.newer_source_kind AND "
            "newer.source_id=relation.newer_source_id AND "
            "newer.source_revision=relation.newer_source_revision "
            "WHERE relation.status IN ('confirmed','proposed') "
            "ORDER BY relation.updated_at DESC,relation.id DESC LIMIT 200",
            params,
        ).fetchall()
    finally:
        conn.close()
    relations: list[kig_governance.VersionRelationResult] = []
    proposed: list[str] = []
    # SQL retains the newest bounded window; consume it oldest-to-newest so
    # pair-level deduplication deterministically leaves the latest confirmation.
    for row in reversed(rows):
        older = by_ref.get((row["older_source_kind"], row["older_source_id"],
                            row["older_source_revision"]))
        newer = by_ref.get((row["newer_source_kind"], row["newer_source_id"],
                            row["newer_source_revision"]))
        if not older or not newer:
            continue
        if row["status"] == "proposed":
            if row["requires_confirmation"]:
                proposed.append(row["id"])
            continue
        relations.append(kig_governance.VersionRelationResult(
            action="select", selected_ids=(newer.candidate_id,), relation=row["relation"],
            older_id=older.candidate_id, newer_id=newer.candidate_id,
            scope_terms=tuple((json.loads(row["scope_json"]) or {}).get("terms", ())),
            reason_codes=("semantic_relation",),
            confidence_band="high" if row["confidence"] >= 0.8 else "medium",
            requires_confirmation=bool(row["requires_confirmation"]), proposal_only=True,
        ))
    return relations, proposed


def _deduplicate_relations(
    relations: list[kig_governance.VersionRelationResult],
) -> list[kig_governance.VersionRelationResult]:
    # Relations concern an unordered evidence pair. Later entries intentionally
    # win, so a persisted user-confirmed decision overrides a deterministic
    # inference even when the confirmed older/newer direction is reversed.
    result: dict[frozenset[str], kig_governance.VersionRelationResult] = {}
    for relation in relations:
        result[frozenset((relation.older_id, relation.newer_id))] = relation
    return list(result.values())


def _plan_summary(plan: kig_query_planner.QueryPlanResult) -> dict:
    return {
        "reason_codes": plan.reason_codes, "selected_sources": plan.selected_sources,
        "temporal_required": plan.temporal_required, "version_required": plan.version_required,
        "entity_required": plan.entity_required, "exact_quote_required": plan.exact_quote_required,
        "conflict_required": plan.conflict_required, "bypassed_model": plan.bypassed_model,
    }

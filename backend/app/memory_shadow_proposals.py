from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

from . import archivist, cognitive_decision as cds, db, memory_conflicts

CONFLICT_DECISION_KIND = "memory_conflict_proposal"
RETENTION_DECISION_KIND = "memory_retention_proposal"
CONFLICT_POLICY_VERSION = "memory-conflict-proposal-v1"
RETENTION_POLICY_VERSION = "memory-retention-proposal-v1"
ORIGINS = frozenset({"user_confirmed", "observed", "automatic", "system_injected"})
ORIGIN_RANK = {"system_injected": 0, "automatic": 1, "observed": 2, "user_confirmed": 3}
OBSERVATION_ORIGINS = {
    "conversation": "observed",
    "knowledge_reference": "system_injected",
    "shared_lookup": "automatic",
    "user_confirmed_fact": "user_confirmed",
}
RELATION_HINTS = frozenset({"compatible", "contradiction", "correction"})
RELATION_TYPES = frozenset({"compatible", "possible_conflict", "conditional_difference", "supersedes"})
RETENTION_STATUSES = frozenset({"active", "cooling", "frozen"})
RETENTION_BANDS = frozenset({"low", "medium", "high"})
RETENTION_ACTIONS = frozenset({"keep", "cool", "freeze", "reconsolidate"})
CONFLICT_REASONS = frozenset({
    "compatible_evidence", "conditional_context", "newer_user_correction",
    "stronger_source_supersedes", "weak_source_cannot_override",
})
RETENTION_REASONS = frozenset({
    "protected_memory", "retention_below_cooling", "retention_below_frozen",
    "injection_cannot_recover", "confirmed_evidence_reconsolidates", "retention_stable",
})


@dataclass(frozen=True)
class FragmentBinding:
    fragment_id: str
    revision: str
    content_hash: str
    status: str
    enabled: bool
    sensitivity: str
    origin: str


def _binding_hash(row: dict) -> str:
    fields = (
        "enabled", "sensitivity", "status", "observation_source", "importance",
        "confidence", "recall_count", "last_recalled_at", "created_at", "updated_at",
        "cooling_since", "scope", "kind", "layer", "in_episode", "in_active_episode",
        "in_active_saga", "is_active_saga_anchor",
    )
    encoded = json.dumps({
        "content_sha256": hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest(),
        **{field: row.get(field) for field in fields},
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bindings_from_snapshots(
    ordered: list[str], snapshots: dict[str, dict],
) -> tuple[FragmentBinding, ...]:
    if set(snapshots) != set(ordered):
        raise cds.DecisionProtocolError("fragment_missing", "memory fragment binding is incomplete")
    return tuple(FragmentBinding(
        fragment_id=item["id"],
        revision=str(item["lifecycle_revision"]),
        content_hash=_binding_hash(item),
        status=str(item["status"]),
        enabled=bool(item["enabled"]),
        sensitivity=str(item["sensitivity"]),
        origin=OBSERVATION_ORIGINS[str(item["observation_source"])],
    ) for item in (snapshots[fragment_id] for fragment_id in ordered))


def _load_adapter_snapshot(
    fragment_ids: Iterable[str],
) -> tuple[tuple[FragmentBinding, ...], dict[str, dict]]:
    ordered = list(dict.fromkeys(str(value) for value in fragment_ids if value))
    if not ordered:
        return (), {}
    conn = db.connect()
    try:
        conn.execute("BEGIN")
        snapshots = archivist.load_fragment_snapshots_from_connection(conn, ordered)
        bindings = _bindings_from_snapshots(ordered, snapshots)
        conn.commit()
        return bindings, snapshots
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_fragment_bindings(fragment_ids: Iterable[str]) -> tuple[FragmentBinding, ...]:
    bindings, _ = _load_adapter_snapshot(fragment_ids)
    return bindings


def source_snapshots(bindings: tuple[FragmentBinding, ...]) -> tuple[cds.SourceSnapshot, ...]:
    return tuple(cds.SourceSnapshot(
        "memory_fragment", item.fragment_id, item.revision, item.content_hash,
    ) for item in bindings)


def candidate_refs(bindings: tuple[FragmentBinding, ...]) -> tuple[cds.CandidateRef, ...]:
    return tuple(cds.CandidateRef(
        item.fragment_id, "memory_fragment", item.content_hash,
    ) for item in bindings)


def build_conflict_input(
    older_id: str, newer_id: str, *, condition_changed: bool = False,
) -> MemoryConflictInput:
    bindings, snapshots = _load_adapter_snapshot((older_id, newer_id))
    projection = memory_conflicts.classify_projection(
        snapshots[older_id]["content"], snapshots[newer_id]["content"],
    )
    newer_kind = str(snapshots[newer_id].get("kind") or "")
    relation_hint = "correction" if newer_kind == "correction" else (
        "contradiction" if projection["relation_type"] in {"superseded", "possible_conflict"}
        else "compatible"
    )
    return MemoryConflictInput(
        candidate_ids=(older_id, newer_id), older_id=older_id, newer_id=newer_id,
        older_origin=bindings[0].origin, newer_origin=bindings[1].origin,
        relation_hint=relation_hint, condition_changed=condition_changed,
        fragment_bindings=bindings,
    )


def build_retention_input(fragment_id: str, *, now: float) -> MemoryRetentionInput:
    bindings, snapshots = _load_adapter_snapshot((fragment_id,))
    binding = bindings[0]
    snapshot = snapshots[fragment_id]
    projection = archivist.project_lifecycle(snapshot, now=now)
    score = projection["evaluation"]["score"]
    band = "low" if score < archivist.FROZEN_SCORE_THRESHOLD else (
        "medium" if score < archivist.COOLING_SCORE_THRESHOLD else "high"
    )
    action = {None: "keep", "cooling": "cool", "frozen": "freeze"}[
        projection["target_status"]
    ]
    if not binding.enabled or binding.sensitivity != "normal" or binding.status == "tombstone":
        action = "keep"
    return MemoryRetentionInput(
        candidate_ids=(fragment_id,), fragment_id=fragment_id, origin=binding.origin,
        status=binding.status, retention_band=band,
        protected=bool(projection["protection_reasons"]),
        injection_only=binding.origin == "system_injected",
        fragment_bindings=(binding,), projected_action=action,
    )


@dataclass(frozen=True)
class MemoryConflictInput:
    candidate_ids: tuple[str, ...]
    older_id: str
    newer_id: str
    older_origin: str
    newer_origin: str
    relation_hint: str
    condition_changed: bool
    fragment_bindings: tuple[FragmentBinding, ...] = ()


@dataclass(frozen=True)
class MemoryConflictProposal:
    action: str
    selected_ids: tuple[str, ...]
    relation_type: str
    superseded_id: str | None
    condition_difference: bool
    reason_codes: tuple[str, ...]
    confidence_band: str
    tombstone_allowed: bool
    advisory_only: bool


@dataclass(frozen=True)
class MemoryRetentionInput:
    candidate_ids: tuple[str, ...]
    fragment_id: str
    origin: str
    status: str
    retention_band: str
    protected: bool
    injection_only: bool
    fragment_bindings: tuple[FragmentBinding, ...] = ()
    projected_action: str | None = None


@dataclass(frozen=True)
class MemoryRetentionProposal:
    action: str
    selected_ids: tuple[str, ...]
    proposed_action: str
    recovery_allowed: bool
    reason_codes: tuple[str, ...]
    confidence_band: str
    tombstone_allowed: bool
    advisory_only: bool


def conflict_fallback(payload: MemoryConflictInput) -> MemoryConflictProposal:
    relation_type = "compatible"
    superseded_id = None
    reason = "compatible_evidence"
    confidence = cds.ConfidenceBand.MEDIUM.value
    if payload.condition_changed:
        relation_type = "conditional_difference"
        reason = "conditional_context"
    elif payload.relation_hint == "correction" and payload.newer_origin == "user_confirmed":
        relation_type = "supersedes"
        superseded_id = payload.older_id
        reason = "newer_user_correction"
        confidence = cds.ConfidenceBand.HIGH.value
    elif payload.relation_hint == "contradiction":
        if ORIGIN_RANK.get(payload.newer_origin, -1) >= ORIGIN_RANK.get(payload.older_origin, -1):
            relation_type = "supersedes"
            superseded_id = payload.older_id
            reason = "stronger_source_supersedes"
            confidence = cds.ConfidenceBand.HIGH.value
        else:
            relation_type = "possible_conflict"
            reason = "weak_source_cannot_override"
            confidence = cds.ConfidenceBand.LOW.value
    selected = (payload.older_id, payload.newer_id)
    return MemoryConflictProposal(
        action=cds.DecisionAction.SELECT.value,
        selected_ids=selected,
        relation_type=relation_type,
        superseded_id=superseded_id,
        condition_difference=payload.condition_changed,
        reason_codes=(reason,),
        confidence_band=confidence,
        tombstone_allowed=False,
        advisory_only=True,
    )


def retention_fallback(payload: MemoryRetentionInput) -> MemoryRetentionProposal:
    proposed_action = "keep"
    recovery_allowed = False
    reason = "retention_stable"
    confidence = cds.ConfidenceBand.MEDIUM.value
    if payload.projected_action in {"cool", "freeze"}:
        proposed_action = payload.projected_action
        reason = "retention_below_cooling" if proposed_action == "cool" else "retention_below_frozen"
    elif payload.protected:
        reason = "protected_memory"
        confidence = cds.ConfidenceBand.HIGH.value
    elif payload.status == "active" and payload.retention_band == "low":
        proposed_action = "cool"
        reason = "retention_below_cooling"
    elif payload.status == "cooling" and payload.retention_band == "low":
        proposed_action = "freeze"
        reason = "retention_below_frozen"
    elif payload.status == "frozen" and payload.injection_only:
        reason = "injection_cannot_recover"
        confidence = cds.ConfidenceBand.HIGH.value
    elif (
        payload.status == "frozen"
        and payload.retention_band == "high"
        and payload.origin == "user_confirmed"
    ):
        proposed_action = "reconsolidate"
        recovery_allowed = True
        reason = "confirmed_evidence_reconsolidates"
        confidence = cds.ConfidenceBand.HIGH.value
    return MemoryRetentionProposal(
        action=cds.DecisionAction.SELECT.value,
        selected_ids=(payload.fragment_id,),
        proposed_action=proposed_action,
        recovery_allowed=recovery_allowed,
        reason_codes=(reason,),
        confidence_band=confidence,
        tombstone_allowed=False,
        advisory_only=True,
    )


def validate_conflict(payload: MemoryConflictInput, result: MemoryConflictProposal) -> None:
    if payload.candidate_ids != (payload.older_id, payload.newer_id):
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "memory conflict candidates changed")
    if payload.older_id == payload.newer_id or any(not item for item in payload.candidate_ids):
        raise cds.DecisionProtocolError("candidate_identity_invalid", "memory conflict candidates are invalid")
    if payload.older_origin not in ORIGINS or payload.newer_origin not in ORIGINS:
        raise cds.DecisionProtocolError("source_origin_invalid", "memory source origin is invalid")
    if payload.relation_hint not in RELATION_HINTS or not isinstance(payload.condition_changed, bool):
        raise cds.DecisionProtocolError("relation_hint_invalid", "memory relation hint is invalid")
    _validate_bindings(payload.candidate_ids, payload.fragment_bindings)
    if payload.fragment_bindings and (
        payload.fragment_bindings[0].origin != payload.older_origin
        or payload.fragment_bindings[1].origin != payload.newer_origin
    ):
        raise cds.DecisionProtocolError("fragment_binding_mismatch", "fragment origins changed")
    if result.action != cds.DecisionAction.SELECT.value or result.selected_ids != payload.candidate_ids:
        raise cds.DecisionProtocolError("candidate_not_allowed", "conflict proposal must bind both candidates")
    if result.relation_type not in RELATION_TYPES:
        raise cds.DecisionProtocolError("relation_type_invalid", "memory relation type is invalid")
    if result.superseded_id is not None and (
        result.relation_type != "supersedes" or result.superseded_id != payload.older_id
    ):
        raise cds.DecisionProtocolError("supersedes_invalid", "supersedes must target the older candidate")
    if result.relation_type == "supersedes" and result.superseded_id != payload.older_id:
        raise cds.DecisionProtocolError("supersedes_invalid", "supersedes requires the older candidate")
    if result.relation_type == "supersedes" and ORIGIN_RANK[payload.newer_origin] < ORIGIN_RANK[payload.older_origin]:
        raise cds.DecisionProtocolError("weak_source_override", "weaker memory source cannot supersede")
    if result.condition_difference is not payload.condition_changed:
        raise cds.DecisionProtocolError("condition_difference_invalid", "condition difference changed")
    expected = conflict_fallback(payload)
    if (
        result.relation_type, result.superseded_id, result.condition_difference,
        result.reason_codes, result.confidence_band,
    ) != (
        expected.relation_type, expected.superseded_id, expected.condition_difference,
        expected.reason_codes, expected.confidence_band,
    ):
        raise cds.DecisionProtocolError(
            "conflict_action_matrix_invalid", "conflict proposal combination is not allowed"
        )
    _validate_common(result.reason_codes, result.confidence_band, result.tombstone_allowed,
                     result.advisory_only, CONFLICT_REASONS)


def validate_retention(payload: MemoryRetentionInput, result: MemoryRetentionProposal) -> None:
    if payload.candidate_ids != (payload.fragment_id,) or not payload.fragment_id:
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "memory retention candidate changed")
    if payload.origin not in ORIGINS or payload.status not in RETENTION_STATUSES:
        raise cds.DecisionProtocolError("retention_input_invalid", "memory retention input is invalid")
    if payload.retention_band not in RETENTION_BANDS or not isinstance(payload.protected, bool) or not isinstance(payload.injection_only, bool):
        raise cds.DecisionProtocolError("retention_input_invalid", "memory retention input is invalid")
    if payload.projected_action is not None and payload.projected_action not in RETENTION_ACTIONS:
        raise cds.DecisionProtocolError("retention_input_invalid", "memory projection is invalid")
    _validate_bindings(payload.candidate_ids, payload.fragment_bindings)
    if payload.fragment_bindings and (
        payload.fragment_bindings[0].origin != payload.origin
        or payload.fragment_bindings[0].status != payload.status
        or payload.fragment_bindings[0].enabled is False and payload.projected_action != "keep"
        or payload.fragment_bindings[0].sensitivity == "sensitive" and payload.projected_action != "keep"
    ):
        raise cds.DecisionProtocolError("fragment_binding_mismatch", "fragment state changed")
    if result.action != cds.DecisionAction.SELECT.value or result.selected_ids != payload.candidate_ids:
        raise cds.DecisionProtocolError("candidate_not_allowed", "retention proposal must bind its candidate")
    if result.proposed_action not in RETENTION_ACTIONS:
        raise cds.DecisionProtocolError("retention_action_invalid", "memory retention action is invalid")
    if result.recovery_allowed and not (
        result.proposed_action == "reconsolidate"
        and payload.status == "frozen"
        and payload.origin == "user_confirmed"
        and not payload.injection_only
    ):
        raise cds.DecisionProtocolError("recovery_source_invalid", "memory recovery requires user confirmation")
    if payload.injection_only and (
        result.recovery_allowed or result.proposed_action == "reconsolidate"
    ):
        raise cds.DecisionProtocolError("injection_recovery_forbidden", "injection cannot recover memory")
    expected = retention_fallback(payload)
    if (
        result.proposed_action, result.recovery_allowed,
        result.reason_codes, result.confidence_band,
    ) != (
        expected.proposed_action, expected.recovery_allowed,
        expected.reason_codes, expected.confidence_band,
    ):
        raise cds.DecisionProtocolError(
            "retention_action_matrix_invalid", "retention proposal combination is not allowed"
        )
    _validate_common(result.reason_codes, result.confidence_band, result.tombstone_allowed,
                     result.advisory_only, RETENTION_REASONS)


def _validate_common(
    reason_codes: tuple[str, ...], confidence_band: str, tombstone_allowed: bool,
    advisory_only: bool, allowed_reasons: frozenset[str],
) -> None:
    if tombstone_allowed is not False:
        raise cds.DecisionProtocolError("tombstone_forbidden", "CDS memory proposals cannot tombstone")
    if advisory_only is not True:
        raise cds.DecisionProtocolError("application_boundary_invalid", "CDS memory proposals cannot apply directly")
    if not isinstance(reason_codes, tuple) or not reason_codes or not set(reason_codes) <= allowed_reasons:
        raise cds.DecisionProtocolError("reason_code_not_allowed", "memory proposal reason is invalid")
    if confidence_band not in {item.value for item in cds.ConfidenceBand}:
        raise cds.DecisionProtocolError("confidence_invalid", "confidence band is invalid")


def _validate_bindings(
    candidate_ids: tuple[str, ...], bindings: tuple[FragmentBinding, ...],
) -> None:
    if not bindings:
        return
    if tuple(item.fragment_id for item in bindings) != candidate_ids:
        raise cds.DecisionProtocolError("fragment_binding_mismatch", "fragment bindings changed")
    if any(
        not item.revision or len(item.content_hash) != 64
        or item.status not in {"active", "cooling", "frozen", "tombstone"}
        or item.sensitivity not in {"normal", "sensitive"}
        or item.origin not in ORIGINS
        for item in bindings
    ):
        raise cds.DecisionProtocolError("fragment_binding_invalid", "fragment binding is invalid")


cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=CONFLICT_DECISION_KIND,
    input_type=MemoryConflictInput,
    result_type=MemoryConflictProposal,
    input_schema_version="memory-conflict-input-v2",
    output_schema_version=CONFLICT_POLICY_VERSION,
    validator=validate_conflict,
    validator_version="memory-conflict-validator-v2",
    fallback=conflict_fallback,
    fallback_version="memory-conflict-pure-fallback-v2",
    fallback_owner="mem",
    application_owner="mem",
    privacy_class="user_private_body_free",
    max_candidates=2,
    timeout_seconds=8.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash(CONFLICT_POLICY_VERSION),
))

cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=RETENTION_DECISION_KIND,
    input_type=MemoryRetentionInput,
    result_type=MemoryRetentionProposal,
    input_schema_version="memory-retention-input-v2",
    output_schema_version=RETENTION_POLICY_VERSION,
    validator=validate_retention,
    validator_version="memory-retention-validator-v2",
    fallback=retention_fallback,
    fallback_version="memory-retention-pure-fallback-v2",
    fallback_owner="mem",
    application_owner="mem",
    privacy_class="user_private_body_free",
    max_candidates=1,
    timeout_seconds=8.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash(RETENTION_POLICY_VERSION),
))

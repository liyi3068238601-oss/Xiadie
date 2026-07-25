from __future__ import annotations

from dataclasses import dataclass

from . import cognitive_decision as cds

DECISION_KIND = "context_planner"
POLICY_VERSION = "context-priority-proposal-v1"
COMPONENTS = (
    "attachment", "rolling_summary", "cross_session_recall",
    "existing_memory_digest", "knowledge", "lore",
)
FIXED_RATIO_PRIORITY = COMPONENTS
PROTECTED_REGIONS = ("current_question", "recent_complete_turns", "output_reserve")
IMPORTANCE_VALUES = frozenset({"none", "low", "medium", "high", "critical"})
TASK_PROFILES = {
    "document": {
        "knowledge": "critical", "attachment": "high", "rolling_summary": "medium",
        "cross_session_recall": "low", "existing_memory_digest": "low", "lore": "low",
    },
    "history": {
        "cross_session_recall": "critical", "rolling_summary": "high",
        "existing_memory_digest": "medium", "attachment": "low",
        "knowledge": "low", "lore": "low",
    },
    "relationship": {
        "existing_memory_digest": "critical", "cross_session_recall": "high",
        "rolling_summary": "high", "lore": "low", "attachment": "low",
        "knowledge": "low",
    },
    "lore": {
        "lore": "critical", "rolling_summary": "medium",
        "existing_memory_digest": "low", "attachment": "low",
        "cross_session_recall": "low", "knowledge": "low",
    },
}
_IMPORTANCE_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "none": 0}
REASON_CODES = frozenset({"semantic_priority", "fixed_ratio_fallback"})


@dataclass(frozen=True)
class ContextPlannerInput:
    candidate_ids: tuple[str, ...]
    source_message_id: str
    valid_message_ids: tuple[str, ...]
    task_type: str
    available_components: tuple[str, ...]


@dataclass(frozen=True)
class ContextPriorityProposal:
    action: str
    selected_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str
    evidence_message_ids: tuple[str, ...]
    task_type: str
    allocation_rank: tuple[str, ...]
    importance_by_component: dict[str, str]
    must_include: tuple[str, ...]
    may_drop: tuple[str, ...]
    protected_regions: tuple[str, ...]
    advisory_only: bool


def component_ids() -> tuple[str, ...]:
    return COMPONENTS


def _proposal(
    payload: ContextPlannerInput, importance: dict[str, str], *, reason: str,
) -> ContextPriorityProposal:
    available = set(payload.available_components)
    priority = tuple(sorted(
        available,
        key=lambda name: (-_IMPORTANCE_RANK[importance[name]], COMPONENTS.index(name)),
    ))
    must_include = tuple(name for name in priority if importance[name] == "critical")
    may_drop = tuple(name for name in reversed(priority) if importance[name] == "low")
    return ContextPriorityProposal(
        action=cds.DecisionAction.SELECT.value,
        selected_ids=priority,
        reason_codes=(reason,),
        confidence_band=cds.ConfidenceBand.HIGH.value if reason == "semantic_priority" else cds.ConfidenceBand.LOW.value,
        evidence_message_ids=(payload.source_message_id,),
        task_type=payload.task_type,
        allocation_rank=priority,
        importance_by_component=importance,
        must_include=must_include,
        may_drop=may_drop,
        protected_regions=PROTECTED_REGIONS,
        advisory_only=True,
    )


def plan_shadow(payload: ContextPlannerInput) -> ContextPriorityProposal:
    profile = TASK_PROFILES.get(payload.task_type)
    if profile is None:
        return fixed_ratio_fallback(payload)
    importance = {
        name: profile[name] if name in payload.available_components else "none"
        for name in COMPONENTS
    }
    return _proposal(payload, importance, reason="semantic_priority")


def fixed_ratio_fallback(payload: ContextPlannerInput) -> ContextPriorityProposal:
    available = set(payload.available_components)
    importance = {name: "medium" if name in available else "none" for name in COMPONENTS}
    result = _proposal(payload, importance, reason="fixed_ratio_fallback")
    priority = tuple(name for name in FIXED_RATIO_PRIORITY if name in available)
    return ContextPriorityProposal(**{
        **result.__dict__, "selected_ids": priority, "allocation_rank": priority,
    })


def validate(payload: ContextPlannerInput, result: ContextPriorityProposal) -> None:
    if payload.candidate_ids != component_ids():
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "context component candidates changed")
    if payload.source_message_id not in payload.valid_message_ids:
        raise cds.DecisionProtocolError("source_message_invalid", "source message is not valid")
    if payload.task_type not in TASK_PROFILES:
        raise cds.DecisionProtocolError("task_type_invalid", "context task type is unsupported")
    if len(payload.available_components) != len(set(payload.available_components)) or not set(payload.available_components) <= set(COMPONENTS):
        raise cds.DecisionProtocolError("available_component_invalid", "available context components are invalid")
    importance = result.importance_by_component
    if not isinstance(importance, dict) or set(importance) != set(COMPONENTS) or any(
        not isinstance(value, str) or value not in IMPORTANCE_VALUES for value in importance.values()
    ):
        raise cds.DecisionProtocolError("component_priority_invalid", "component priorities are invalid")
    available = set(payload.available_components)
    if any(importance[name] == "none" for name in available) or any(
        importance[name] != "none" for name in set(COMPONENTS) - available
    ):
        raise cds.DecisionProtocolError("component_availability_mismatch", "proposal changed component availability")
    if result.task_type != payload.task_type:
        raise cds.DecisionProtocolError("task_type_mismatch", "proposal changed task type")
    if set(result.allocation_rank) != available or len(result.allocation_rank) != len(available):
        raise cds.DecisionProtocolError("allocation_rank_invalid", "allocation rank must cover available components once")
    if result.selected_ids != result.allocation_rank:
        raise cds.DecisionProtocolError("candidate_not_allowed", "selected components must match allocation rank")
    if not set(result.must_include) <= available or not set(result.may_drop) <= available:
        raise cds.DecisionProtocolError("component_set_invalid", "proposal references an unavailable component")
    if set(result.must_include) & set(result.may_drop):
        raise cds.DecisionProtocolError("component_set_overlap", "must include and may drop must be disjoint")
    expected_must = tuple(name for name in result.allocation_rank if importance[name] == "critical")
    expected_drop = tuple(name for name in reversed(result.allocation_rank) if importance[name] == "low")
    if result.must_include != expected_must or result.may_drop != expected_drop:
        raise cds.DecisionProtocolError("component_set_invalid", "priority sets must match component importance")
    if result.protected_regions != PROTECTED_REGIONS:
        raise cds.DecisionProtocolError("protected_region_invalid", "protected context regions cannot change")
    if result.evidence_message_ids != (payload.source_message_id,):
        raise cds.DecisionProtocolError("evidence_message_invalid", "planner must cite the source message")
    if result.action != cds.DecisionAction.SELECT.value or result.advisory_only is not True:
        raise cds.DecisionProtocolError("application_boundary_invalid", "context proposal cannot apply directly")
    if not set(result.reason_codes) <= REASON_CODES or not result.reason_codes:
        raise cds.DecisionProtocolError("reason_code_not_allowed", "context proposal reason is invalid")
    if result.confidence_band not in {item.value for item in cds.ConfidenceBand}:
        raise cds.DecisionProtocolError("confidence_invalid", "confidence band is invalid")


cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=DECISION_KIND,
    input_type=ContextPlannerInput,
    result_type=ContextPriorityProposal,
    input_schema_version="context-planner-input-v1",
    output_schema_version=POLICY_VERSION,
    validator=validate,
    validator_version="context-priority-validator-v1",
    fallback=fixed_ratio_fallback,
    fallback_version="context-fixed-ratio-fallback-v1",
    fallback_owner="ctx",
    application_owner="ctx",
    privacy_class="user_private_body_free",
    max_candidates=len(COMPONENTS),
    timeout_seconds=8.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash("context-priority-proposal-v1"),
))

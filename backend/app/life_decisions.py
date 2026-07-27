"""LIFE.1 domain contracts on the CDS-owned cognitive DecisionRun runtime.

This module registers LIFE's bounded proposal tasks.  It deliberately owns no
generic run table and grants no application authority: source revisions are
re-read from the LIFE owner before a result can leave Shadow mode.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from . import cognitive_decision as cds
from . import specialty_contracts

POLICY_VERSION = "life-decision-policy-v1"
INPUT_VERSION = "life-decision-input-v1"
OUTPUT_VERSION = "life-decision-result-v1"
VALIDATOR_VERSION = "life-decision-validator-v1"
FALLBACK_VERSION = "life-decision-skip-v1"
MAX_TRANSIENT_SUMMARY_CHARS = 2_000
MAX_UNTRUSTED_JSON_CHARS = 4_000

LIFE_DECISION_KINDS = (
    "life_schedule_coarse",
    "life_schedule_detail",
    "life_schedule_replan",
    "life_important_date_interpretation",
    "life_diary_reflection",
    "life_event_meaning",
)

_REASON_CODES = frozenset({
    "bounded_candidate_selected", "insufficient_evidence", "source_ambiguous",
    "deterministic_fallback", "user_confirmation_required", "no_safe_candidate",
})


@dataclass(frozen=True)
class LifeDecisionInput:
    candidate_ids: tuple[str, ...]
    source_kinds: tuple[str, ...]
    # Transient, necessary summaries. CDS persists only their schema/run metadata.
    summary_fragments: tuple[str, ...] = ()
    # Each entry is parsed as an untrusted JSON object, never as instructions.
    untrusted_json: tuple[str, ...] = ()
    max_selected: int = 1


@dataclass(frozen=True)
class LifeDecisionResult:
    action: str
    selected_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str


def deterministic_fallback(_: LifeDecisionInput) -> LifeDecisionResult:
    return LifeDecisionResult(
        action=cds.DecisionAction.SKIP.value,
        selected_ids=(),
        reason_codes=("deterministic_fallback",),
        confidence_band=cds.ConfidenceBand.LOW.value,
    )


def validate(payload: LifeDecisionInput, result: LifeDecisionResult) -> None:
    if not payload.candidate_ids or len(payload.candidate_ids) != len(set(payload.candidate_ids)):
        raise cds.DecisionProtocolError("candidate_duplicate", "LIFE candidate IDs must be unique")
    if any(not isinstance(item, str) or not item for item in payload.candidate_ids):
        raise cds.DecisionProtocolError("input_schema_invalid", "candidate IDs must be non-empty strings")
    if not payload.source_kinds or any(
        item not in specialty_contracts.LIFE_SOURCE_KINDS for item in payload.source_kinds
    ):
        raise cds.DecisionProtocolError("source_kind_invalid", "LIFE source kind is not registered")
    if payload.max_selected < 1 or payload.max_selected > min(3, len(payload.candidate_ids)):
        raise cds.DecisionProtocolError("selection_limit_invalid", "LIFE selection limit is invalid")
    if any(
        not isinstance(item, str) or len(item) > MAX_TRANSIENT_SUMMARY_CHARS
        for item in payload.summary_fragments
    ):
        raise cds.DecisionProtocolError("summary_invalid", "transient summary is invalid")
    for item in payload.untrusted_json:
        if not isinstance(item, str) or len(item) > MAX_UNTRUSTED_JSON_CHARS:
            raise cds.DecisionProtocolError("untrusted_json_invalid", "untrusted JSON is invalid")
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError as exc:
            raise cds.DecisionProtocolError("untrusted_json_invalid", "untrusted JSON is invalid") from exc
        if not isinstance(parsed, dict):
            raise cds.DecisionProtocolError("untrusted_json_invalid", "untrusted JSON must be an object")
    if result.action not in {item.value for item in cds.DecisionAction}:
        raise cds.DecisionProtocolError("action_not_allowed", "LIFE action is invalid")
    if not isinstance(result.selected_ids, tuple) or not isinstance(result.reason_codes, tuple):
        raise cds.DecisionProtocolError("output_schema_invalid", "LIFE result collections must be tuples")
    if len(result.selected_ids) > payload.max_selected:
        raise cds.DecisionProtocolError("selection_limit_exceeded", "too many LIFE candidates selected")
    if not set(result.selected_ids).issubset(payload.candidate_ids):
        raise cds.DecisionProtocolError("candidate_not_allowed", "result selected a foreign candidate")
    if result.action == cds.DecisionAction.SELECT.value and not result.selected_ids:
        raise cds.DecisionProtocolError("selection_empty", "select requires a LIFE candidate")
    if result.action != cds.DecisionAction.SELECT.value and result.selected_ids:
        raise cds.DecisionProtocolError("selection_action_mismatch", "non-select cannot select candidates")
    if not result.reason_codes or not set(result.reason_codes).issubset(_REASON_CODES):
        raise cds.DecisionProtocolError("reason_code_not_allowed", "unknown LIFE reason code")
    if result.confidence_band not in {item.value for item in cds.ConfidenceBand}:
        raise cds.DecisionProtocolError("confidence_invalid", "LIFE confidence is invalid")


def _register() -> None:
    for decision_kind in LIFE_DECISION_KINDS:
        cds.REGISTRY.register(cds.DecisionKindDefinition(
            decision_kind=decision_kind,
            input_type=LifeDecisionInput,
            result_type=LifeDecisionResult,
            input_schema_version=INPUT_VERSION,
            output_schema_version=OUTPUT_VERSION,
            validator=validate,
            validator_version=VALIDATOR_VERSION,
            fallback=deterministic_fallback,
            fallback_version=FALLBACK_VERSION,
            fallback_owner="life",
            application_owner="life",
            privacy_class="user_private_transient_body_free_diagnostics",
            max_candidates=12,
            timeout_seconds=8.0,
            result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
            model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
            mode=cds.DecisionMode.SHADOW,
            prompt_template_hash=cds._canonical_hash(f"{decision_kind}-shadow-v1"),  # noqa: SLF001
        ))


def snapshots_from_refs(refs: tuple[specialty_contracts.RevisionRef, ...]) -> tuple[cds.SourceSnapshot, ...]:
    snapshots: list[cds.SourceSnapshot] = []
    for ref in refs:
        specialty_contracts.validate_revision_ref(ref)
        if ref["kind"] not in specialty_contracts.LIFE_SOURCE_KINDS:
            raise cds.DecisionProtocolError("source_kind_invalid", "source is not LIFE-owned")
        snapshots.append(cds.SourceSnapshot(
            kind=ref["kind"], id=ref["id"], revision=ref["revision"],
            content_hash=ref["content_hash"],
        ))
    return tuple(snapshots)


def reread_snapshots(
    expected: tuple[cds.SourceSnapshot, ...],
    reader: Callable[[str, str], specialty_contracts.RevisionRef | None],
) -> tuple[cds.SourceSnapshot, ...]:
    current: list[cds.SourceSnapshot] = []
    for source in expected:
        ref = reader(source.kind, source.id)
        if ref is None:
            # Preserve identity while guaranteeing a recheck failure.
            current.append(cds.SourceSnapshot(source.kind, source.id, "deleted", "0" * 64))
            continue
        current.extend(snapshots_from_refs((ref,)))
    return tuple(current)


def evaluate_output(
    run_id: str,
    header: cds.CommonDecisionHeader,
    payload: LifeDecisionInput,
    raw_output: str,
    *,
    reader: Callable[[str, str], specialty_contracts.RevisionRef | None],
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    """Re-read LIFE revisions and fail closed; Shadow can never authorize writes."""
    return cds.evaluate_output(
        run_id, header, payload, raw_output,
        current_snapshot=reread_snapshots(header.source_snapshot, reader),
        allow_active_application=False, latency_ms=latency_ms,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )


_register()

"""KIG.4 optional LLM boundary proposal constrained to deterministic cut candidates."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from . import cognitive_decision as cds

DECISION_KIND = "knowledge_boundary_proposal"
POLICY_VERSION = "knowledge-boundary-policy-v1"
MAX_CUTS = 64


@dataclass(frozen=True)
class BoundaryProposalInput:
    candidate_ids: tuple[str, ...]
    source_id: str
    source_revision: str
    source_hash: str
    raw_text_length: int
    deterministic_cut_offsets: tuple[int, ...]


@dataclass(frozen=True)
class BoundaryProposalResult:
    action: str
    selected_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str
    cut_offsets: tuple[int, ...]
    proposal_only: bool
    rewrites_raw_text: bool


def candidate_ids(offsets: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(f"cut:{offset}" for offset in offsets)


def deterministic_fallback(payload: BoundaryProposalInput) -> BoundaryProposalResult:
    return BoundaryProposalResult(
        action=cds.DecisionAction.SELECT.value,
        selected_ids=payload.candidate_ids,
        reason_codes=("deterministic_boundaries",),
        confidence_band=cds.ConfidenceBand.HIGH.value,
        cut_offsets=payload.deterministic_cut_offsets,
        proposal_only=True,
        rewrites_raw_text=False,
    )


def validate(payload: BoundaryProposalInput, result: BoundaryProposalResult) -> None:
    offsets = payload.deterministic_cut_offsets
    if (
        not payload.source_id or not payload.source_revision
        or not re.fullmatch(r"[0-9a-f]{64}", payload.source_hash)
        or payload.raw_text_length < 1
    ):
        raise cds.DecisionProtocolError("source_invalid", "boundary source is invalid")
    if not offsets or len(offsets) > MAX_CUTS or tuple(sorted(set(offsets))) != offsets:
        raise cds.DecisionProtocolError("cut_candidates_invalid", "cut candidates are invalid")
    if offsets[-1] >= payload.raw_text_length or offsets[0] <= 0:
        raise cds.DecisionProtocolError("cut_candidates_invalid", "cut offsets exceed raw text")
    if payload.candidate_ids != candidate_ids(offsets):
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "boundary candidates changed")
    if tuple(f"cut:{offset}" for offset in result.cut_offsets) != result.selected_ids:
        raise cds.DecisionProtocolError("boundary_selection_mismatch", "selected cuts disagree")
    if not set(result.cut_offsets) <= set(offsets) or tuple(sorted(set(result.cut_offsets))) != result.cut_offsets:
        raise cds.DecisionProtocolError("invented_boundary", "model invented a boundary")
    if result.action != cds.DecisionAction.SELECT.value or not result.selected_ids:
        raise cds.DecisionProtocolError("action_not_allowed", "boundary proposal must select safe cuts")
    if result.reason_codes not in {("deterministic_boundaries",), ("model_boundary_subset",)}:
        raise cds.DecisionProtocolError("reason_code_not_allowed", "boundary reason is invalid")
    if result.confidence_band not in {item.value for item in cds.ConfidenceBand}:
        raise cds.DecisionProtocolError("confidence_invalid", "boundary confidence is invalid")
    if result.proposal_only is not True or result.rewrites_raw_text is not False:
        raise cds.DecisionProtocolError("raw_rewrite_forbidden", "boundary proposal cannot rewrite raw text")


def apply_exact_slices(text: str, result: BoundaryProposalResult) -> tuple[str, ...]:
    if result.rewrites_raw_text or any(offset <= 0 or offset >= len(text) for offset in result.cut_offsets):
        raise ValueError("boundary proposal cannot be applied")
    boundaries = (0, *result.cut_offsets, len(text))
    pieces = tuple(text[boundaries[index]:boundaries[index + 1]] for index in range(len(boundaries) - 1))
    if "".join(pieces) != text:
        raise ValueError("raw text integrity check failed")
    return pieces


def candidates(payload: BoundaryProposalInput) -> tuple[cds.CandidateRef, ...]:
    return tuple(
        cds.CandidateRef(item, "safe_boundary", hashlib.sha256(item.encode()).hexdigest())
        for item in payload.candidate_ids
    )


cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=DECISION_KIND,
    input_type=BoundaryProposalInput,
    result_type=BoundaryProposalResult,
    input_schema_version="knowledge-boundary-input-v1",
    output_schema_version="knowledge-boundary-result-v1",
    validator=validate,
    validator_version="knowledge-boundary-validator-v1",
    fallback=deterministic_fallback,
    fallback_version="knowledge-boundary-deterministic-v1",
    fallback_owner="knowledge",
    application_owner="knowledge",
    privacy_class="user_private_transient_body_free_diagnostics",
    max_candidates=MAX_CUTS,
    timeout_seconds=8.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash("knowledge-boundary-shadow-v1"),  # noqa: SLF001
))

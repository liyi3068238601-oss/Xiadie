"""CDS.1 shared, body-free cognitive decision protocol foundation.

This module owns protocol validation and audit metadata only. Domain owners still
generate candidates and apply effects. No raw prompt, candidate body, user text or
model output is persisted here.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Callable, Generic, TypeVar, get_origin, get_type_hints

from . import db
from .proactive import run_ledger

PROTOCOL_VERSION = "cognitive-decision-v1"
SNAPSHOT_VERSION = "decision-source-snapshot-v1"
REGISTRY_VERSION = "decision-kind-registry-v1"
MODEL_BINDING_POLICY_VERSION = "cognition-binding-v1"
DIAGNOSTIC_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_RAW_OUTPUT_CHARS = 16_000
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PROBE_REASON_CODES = frozenset({"directly_relevant", "structured_fallback"})


class DecisionMode(str, Enum):
    SHADOW = "shadow"
    ADVISORY = "advisory"
    ACTIVE = "active"


_MODE_RANK = {
    DecisionMode.SHADOW: 0,
    DecisionMode.ADVISORY: 1,
    DecisionMode.ACTIVE: 2,
}


class DecisionAction(str, Enum):
    SELECT = "select"
    SKIP = "skip"
    ASK = "ask"


class ConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceSnapshot:
    kind: str
    id: str
    revision: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.kind or not self.id or not self.revision:
            raise DecisionProtocolError("source_identity_invalid", "source identity is incomplete")
        if not _HEX64.fullmatch(self.content_hash):
            raise DecisionProtocolError("source_hash_invalid", "source content_hash must be sha256")

    def public_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind, "id": self.id, "revision": self.revision,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class CandidateRef:
    id: str
    source_kind: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.id or not self.source_kind or not _HEX64.fullmatch(self.content_hash):
            raise DecisionProtocolError("candidate_identity_invalid", "candidate identity is invalid")

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "source_kind": self.source_kind, "content_hash": self.content_hash}


@dataclass(frozen=True)
class CommonDecisionHeader:
    decision_kind: str
    policy_version: str
    request_id: str
    mode: DecisionMode
    source_snapshot: tuple[SourceSnapshot, ...]
    snapshot_hash: str
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise DecisionProtocolError("protocol_mismatch", "unsupported cognitive decision protocol")
        if not self.decision_kind or not self.policy_version or not self.request_id:
            raise DecisionProtocolError("header_identity_invalid", "header identity is incomplete")
        if not isinstance(self.mode, DecisionMode):
            raise DecisionProtocolError("mode_invalid", "header mode is invalid")
        if not self.source_snapshot:
            raise DecisionProtocolError("source_snapshot_empty", "at least one source is required")
        if aggregate_snapshot_hash(self.source_snapshot) != self.snapshot_hash:
            raise DecisionProtocolError("snapshot_hash_mismatch", "aggregate source hash mismatch")


@dataclass(frozen=True)
class ProtocolProbeInput:
    candidate_ids: tuple[str, ...]
    allowed_actions: tuple[str, ...] = (DecisionAction.SELECT.value, DecisionAction.SKIP.value)
    max_selected: int = 1


@dataclass(frozen=True)
class ProtocolProbeResult:
    action: str
    selected_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str


InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class DecisionKindDefinition(Generic[InputT, ResultT]):
    decision_kind: str
    input_type: type[InputT]
    result_type: type[ResultT]
    input_schema_version: str
    output_schema_version: str
    validator: Callable[[InputT, ResultT], None]
    validator_version: str
    fallback: Callable[[InputT], ResultT]
    fallback_version: str
    fallback_owner: str
    application_owner: str
    privacy_class: str
    max_candidates: int
    timeout_seconds: float
    result_ttl_seconds: float
    model_binding_revision: str
    mode: DecisionMode
    prompt_template_hash: str

    @property
    def input_schema_hash(self) -> str:
        return _schema_hash(self.input_type, self.input_schema_version)

    @property
    def output_schema_hash(self) -> str:
        return _schema_hash(self.result_type, self.output_schema_version)


class DecisionKindRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, DecisionKindDefinition[Any, Any]] = {}

    def register(self, definition: DecisionKindDefinition[Any, Any]) -> None:
        if not definition.decision_kind or definition.decision_kind in self._definitions:
            raise DecisionProtocolError("decision_kind_duplicate", "decision kind already registered")
        if definition.max_candidates < 1 or definition.timeout_seconds <= 0:
            raise DecisionProtocolError("decision_kind_limits_invalid", "registry limits are invalid")
        if definition.result_ttl_seconds <= 0 or not _HEX64.fullmatch(definition.prompt_template_hash):
            raise DecisionProtocolError("decision_kind_metadata_invalid", "registry metadata is invalid")
        self._definitions[definition.decision_kind] = definition

    def get(self, decision_kind: str) -> DecisionKindDefinition[Any, Any]:
        try:
            return self._definitions[decision_kind]
        except KeyError as exc:
            raise DecisionProtocolError("decision_kind_unknown", "decision kind is not registered") from exc

    def public_snapshot(self) -> list[dict[str, Any]]:
        return [{
            "decision_kind": item.decision_kind,
            "input_schema_version": item.input_schema_version,
            "input_schema_hash": item.input_schema_hash,
            "output_schema_version": item.output_schema_version,
            "output_schema_hash": item.output_schema_hash,
            "validator_version": item.validator_version,
            "fallback_version": item.fallback_version,
            "fallback_owner": item.fallback_owner,
            "application_owner": item.application_owner,
            "privacy_class": item.privacy_class,
            "max_candidates": item.max_candidates,
            "timeout_seconds": item.timeout_seconds,
            "result_ttl_seconds": item.result_ttl_seconds,
            "model_binding_revision": item.model_binding_revision,
            "mode": item.mode.value,
        } for item in sorted(self._definitions.values(), key=lambda value: value.decision_kind)]


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _schema_hash(schema_type: type[Any], version: str) -> str:
    return _canonical_hash({
        "version": version,
        "fields": [{"name": item.name, "type": str(item.type)} for item in fields(schema_type)],
    })


def aggregate_snapshot_hash(snapshots: tuple[SourceSnapshot, ...]) -> str:
    identities = [(item.kind, item.id) for item in snapshots]
    if len(identities) != len(set(identities)):
        raise DecisionProtocolError("source_snapshot_duplicate", "source identities must be unique")
    return _canonical_hash({
        "version": SNAPSHOT_VERSION,
        "sources": [item.public_dict() for item in sorted(snapshots, key=lambda x: (x.kind, x.id))],
    })


def candidate_snapshot_hash(candidates: tuple[CandidateRef, ...]) -> str:
    ids = [item.id for item in candidates]
    if len(ids) != len(set(ids)):
        raise DecisionProtocolError("candidate_duplicate", "candidate IDs must be unique")
    return _canonical_hash([item.public_dict() for item in sorted(candidates, key=lambda x: x.id)])


def verify_source_snapshot(
    expected: tuple[SourceSnapshot, ...], current: tuple[SourceSnapshot, ...], snapshot_hash: str,
) -> None:
    expected_map = {(item.kind, item.id): item for item in expected}
    current_map = {(item.kind, item.id): item for item in current}
    if expected_map.keys() != current_map.keys():
        raise DecisionProtocolError("source_set_changed", "source set changed before application")
    for identity, expected_item in expected_map.items():
        if current_map[identity] != expected_item:
            raise DecisionProtocolError("source_revision_changed", "source revision or hash changed")
    if aggregate_snapshot_hash(current) != snapshot_hash:
        raise DecisionProtocolError("snapshot_hash_mismatch", "aggregate source hash changed")


def _probe_fallback(_: ProtocolProbeInput) -> ProtocolProbeResult:
    return ProtocolProbeResult(
        action=DecisionAction.SKIP.value, selected_ids=(),
        reason_codes=("structured_fallback",), confidence_band=ConfidenceBand.LOW.value,
    )


def _validate_probe(payload: ProtocolProbeInput, result: ProtocolProbeResult) -> None:
    if (
        not isinstance(payload.candidate_ids, tuple)
        or any(not isinstance(item, str) or not item for item in payload.candidate_ids)
        or not isinstance(payload.allowed_actions, tuple)
        or any(not isinstance(item, str) for item in payload.allowed_actions)
    ):
        raise DecisionProtocolError("input_schema_invalid", "probe input values are invalid")
    candidate_ids = set(payload.candidate_ids)
    if not candidate_ids or len(candidate_ids) != len(payload.candidate_ids):
        raise DecisionProtocolError("candidate_duplicate", "candidate IDs must be unique")
    if not set(payload.allowed_actions).issubset({item.value for item in DecisionAction}):
        raise DecisionProtocolError("allowed_actions_invalid", "input contains an invalid action")
    if payload.max_selected < 1 or payload.max_selected > len(candidate_ids):
        raise DecisionProtocolError("selection_limit_invalid", "input selection limit is invalid")
    if result.action not in payload.allowed_actions:
        raise DecisionProtocolError("action_not_allowed", "result action is not allowed")
    if (
        not isinstance(result.selected_ids, tuple)
        or any(not isinstance(item, str) for item in result.selected_ids)
        or not isinstance(result.reason_codes, tuple)
        or any(not isinstance(item, str) for item in result.reason_codes)
    ):
        raise DecisionProtocolError("output_schema_invalid", "probe result values are invalid")
    if not set(result.selected_ids).issubset(candidate_ids):
        raise DecisionProtocolError("candidate_not_allowed", "result contains a non-candidate ID")
    if len(result.selected_ids) > min(payload.max_selected, 1):
        raise DecisionProtocolError("selection_limit_exceeded", "too many candidates selected")
    if result.action == DecisionAction.SELECT.value and not result.selected_ids:
        raise DecisionProtocolError("selection_empty", "select action requires a candidate")
    if result.action != DecisionAction.SELECT.value and result.selected_ids:
        raise DecisionProtocolError("selection_action_mismatch", "non-select action cannot select IDs")
    if result.confidence_band not in {item.value for item in ConfidenceBand}:
        raise DecisionProtocolError("confidence_invalid", "confidence band is invalid")
    if not set(result.reason_codes).issubset(_PROBE_REASON_CODES):
        raise DecisionProtocolError("reason_code_not_allowed", "result contains an unknown reason code")


REGISTRY = DecisionKindRegistry()
REGISTRY.register(DecisionKindDefinition(
    decision_kind="protocol_probe",
    input_type=ProtocolProbeInput,
    result_type=ProtocolProbeResult,
    input_schema_version="protocol-probe-input-v1",
    output_schema_version="protocol-probe-result-v1",
    validator=_validate_probe,
    validator_version="protocol-probe-validator-v1",
    fallback=_probe_fallback,
    fallback_version="protocol-probe-fallback-v1",
    fallback_owner="cds",
    application_owner="cds",
    privacy_class="synthetic_body_free",
    max_candidates=8,
    timeout_seconds=2.0,
    result_ttl_seconds=DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=MODEL_BINDING_POLICY_VERSION,
    mode=DecisionMode.SHADOW,
    prompt_template_hash=_canonical_hash("cds-protocol-probe-v1"),
))


def build_header(
    *, decision_kind: str, policy_version: str, request_id: str, mode: DecisionMode,
    source_snapshot: tuple[SourceSnapshot, ...],
) -> CommonDecisionHeader:
    return CommonDecisionHeader(
        decision_kind=decision_kind, policy_version=policy_version, request_id=request_id,
        mode=mode, source_snapshot=source_snapshot,
        snapshot_hash=aggregate_snapshot_hash(source_snapshot),
    )


def create_run(
    header: CommonDecisionHeader, payload: Any, candidates: tuple[CandidateRef, ...], *,
    provider_id: str | None = None, model_id: str | None = None,
    provider_location: str | None = None, temperature: float | None = None,
    provider_location_revision: int | None = None, logical_role: str = "legacy",
    certification_level: str = "unverified", top_p: float | None = None,
    model_binding_revision: str | None = None, now: float | None = None,
) -> tuple[run_ledger.DecisionRun, bool]:
    definition = REGISTRY.get(header.decision_kind)
    if not isinstance(payload, definition.input_type):
        raise DecisionProtocolError("input_schema_invalid", "input does not match registered schema")
    definition.validator(payload, definition.fallback(payload))
    if _MODE_RANK[header.mode] > _MODE_RANK[definition.mode]:
        raise DecisionProtocolError("mode_not_authorized", "requested mode is not registry-authorized")
    if len(candidates) > definition.max_candidates:
        raise DecisionProtocolError("candidate_limit_exceeded", "candidate limit exceeded")
    payload_candidate_ids = tuple(getattr(payload, "candidate_ids", ()))
    if payload_candidate_ids and payload_candidate_ids != tuple(item.id for item in candidates):
        raise DecisionProtocolError("candidate_snapshot_mismatch", "input and candidate snapshot differ")
    payload_candidate_refs = tuple(getattr(payload, "candidate_refs", ()))
    if payload_candidate_refs and payload_candidate_refs != candidates:
        raise DecisionProtocolError("candidate_snapshot_mismatch", "domain envelopes and candidate snapshot differ")
    now = db.now() if now is None else now
    candidates_hash = candidate_snapshot_hash(candidates)
    return run_ledger.create_or_get_run(
        task_kind=header.decision_kind, protocol_version=header.protocol_version,
        policy_version=header.policy_version, mode=header.mode.value,
        source_type="multi_source" if len(header.source_snapshot) > 1 else header.source_snapshot[0].kind,
        source_id=header.request_id, source_revision=header.snapshot_hash,
        source_hash=header.snapshot_hash,
        source_snapshot=(item.public_dict() for item in header.source_snapshot),
        snapshot_hash=header.snapshot_hash, candidate_snapshot_hash=candidates_hash,
        candidate_count=len(candidates),
        idempotency_key=run_ledger.make_idempotency_key(
            header.protocol_version, header.decision_kind, header.policy_version,
            header.request_id, header.snapshot_hash, candidates_hash,
        ),
        provider_id=provider_id, model_id=model_id, provider_location=provider_location,
        provider_location_revision=provider_location_revision, logical_role=logical_role,
        certification_level=certification_level,
        prompt_template_hash=definition.prompt_template_hash,
        input_schema_hash=definition.input_schema_hash,
        output_schema_hash=definition.output_schema_hash,
        validator_version=definition.validator_version,
        fallback_version=definition.fallback_version,
        model_binding_revision=model_binding_revision or definition.model_binding_revision,
        temperature=temperature, top_p=top_p,
        retention_class="short_diagnostic", expires_at=now + definition.result_ttl_seconds,
        privacy_scope=definition.privacy_class, aggregate_after_expiry=True, now=now,
    )


def _decode_result_once(raw_output: str, result_type: type[ResultT]) -> tuple[ResultT, bool]:
    repaired = False
    if not isinstance(raw_output, str) or len(raw_output) > MAX_RAW_OUTPUT_CHARS:
        raise DecisionProtocolError("output_size_invalid", "model result exceeds the bounded size")
    try:
        payload = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        repaired = True
        text = str(raw_output).strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]) if len(lines) >= 3 else text
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise DecisionProtocolError("json_repair_failed", "one JSON repair attempt failed")
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise DecisionProtocolError("json_repair_failed", "one JSON repair attempt failed") from exc
    if not isinstance(payload, dict):
        raise DecisionProtocolError("output_schema_invalid", "model result must be an object")
    allowed = {item.name for item in fields(result_type)}
    if set(payload) != allowed:
        raise DecisionProtocolError("output_schema_invalid", "model result fields do not match schema")
    try:
        resolved_types = get_type_hints(result_type)
        for item in fields(result_type):
            if get_origin(resolved_types.get(item.name)) is tuple and isinstance(payload[item.name], list):
                payload[item.name] = tuple(payload[item.name])
        return result_type(**payload), repaired
    except (TypeError, ValueError) as exc:
        raise DecisionProtocolError("output_schema_invalid", "model result values are invalid") from exc


def evaluate_output(
    run_id: str, header: CommonDecisionHeader, payload: Any, raw_output: str, *,
    current_snapshot: tuple[SourceSnapshot, ...], allow_active_application: bool = False,
    latency_ms: int | None = None, input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    """Validate one structured result and return an application gate, never raw output."""
    definition = REGISTRY.get(header.decision_kind)
    run = run_ledger.get_run(run_id)
    if not run or run.task_kind != header.decision_kind or run.snapshot_hash != header.snapshot_hash:
        raise DecisionProtocolError("run_header_mismatch", "run is not bound to this header")
    if run.status != run_ledger.RunStatus.QUEUED:
        raise DecisionProtocolError("run_not_claimable", "decision run is already claimed or terminal")
    try:
        run_ledger.transition_run(run_id, run_ledger.RunStatus.RUNNING)
    except ValueError as exc:
        raise DecisionProtocolError("run_not_claimable", "decision run changed concurrently") from exc
    error_code: str | None = None
    repaired = False
    fallback_used = False
    try:
        verify_source_snapshot(header.source_snapshot, current_snapshot, header.snapshot_hash)
        result, repaired = _decode_result_once(raw_output, definition.result_type)
        definition.validator(payload, result)
    except DecisionProtocolError as exc:
        error_code = exc.code
        fallback_used = True
        result = definition.fallback(payload)
        definition.validator(payload, result)
    except Exception:  # A domain validator bug or unexpected type must still fail closed.
        error_code = "validator_failed"
        fallback_used = True
        result = definition.fallback(payload)
        definition.validator(payload, result)
    reason_codes = tuple(getattr(result, "reason_codes", ()))
    selected_ids = tuple(getattr(result, "selected_ids", ()))
    run_ledger._record_validated_decision_outcome(  # noqa: SLF001 - shared runtime boundary
        run_id, action=getattr(result, "action"), selected_count=len(selected_ids),
        confidence_band=getattr(result, "confidence_band"), reason_codes=reason_codes,
        fallback_used=fallback_used,
        validated_candidate_snapshot_hash=run.candidate_snapshot_hash,
    )
    warnings = ["json_repaired_once"] if repaired else []
    if error_code:
        warnings.append(error_code)
    source_valid = error_code not in {"source_set_changed", "source_revision_changed", "snapshot_hash_mismatch"}
    if not source_valid:
        run_ledger.transition_run(
            run_id, run_ledger.RunStatus.SKIPPED, error_code=error_code, warnings=warnings,
            latency_ms=latency_ms, input_tokens=input_tokens, output_tokens=output_tokens,
        )
    else:
        run_ledger.transition_run(
            run_id, run_ledger.RunStatus.APPLIED, error_code=error_code, warnings=warnings,
            latency_ms=latency_ms, input_tokens=input_tokens, output_tokens=output_tokens,
        )
    application_allowed = bool(
        source_valid and not fallback_used and header.mode is DecisionMode.ACTIVE
        and definition.mode is DecisionMode.ACTIVE and allow_active_application
    )
    return {
        "run_id": run_id, "decision_kind": header.decision_kind,
        "mode": header.mode.value, "action": getattr(result, "action"),
        "selected_ids": list(selected_ids), "reason_codes": list(reason_codes),
        "confidence_band": getattr(result, "confidence_band"),
        "fallback_used": fallback_used, "json_repaired_once": repaired,
        "error_code": error_code, "application_allowed": application_allowed,
    }


def evaluate_failure(
    run_id: str, header: CommonDecisionHeader, payload: Any, *, error_code: str,
    latency_ms: int | None = None, input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    """Finalize an unavailable model through the registered deterministic fallback."""
    definition = REGISTRY.get(header.decision_kind)
    run = run_ledger.get_run(run_id)
    if not run or run.task_kind != header.decision_kind or run.snapshot_hash != header.snapshot_hash:
        raise DecisionProtocolError("run_header_mismatch", "run is not bound to this header")
    if run.status != run_ledger.RunStatus.QUEUED:
        raise DecisionProtocolError("run_not_claimable", "decision run is already claimed or terminal")
    run_ledger.transition_run(run_id, run_ledger.RunStatus.RUNNING)
    result = definition.fallback(payload)
    definition.validator(payload, result)
    selected_ids = tuple(getattr(result, "selected_ids", ()))
    reason_codes = tuple(getattr(result, "reason_codes", ()))
    run_ledger._record_validated_decision_outcome(  # noqa: SLF001
        run_id, action=getattr(result, "action"), selected_count=len(selected_ids),
        confidence_band=getattr(result, "confidence_band"), reason_codes=reason_codes,
        fallback_used=True, validated_candidate_snapshot_hash=run.candidate_snapshot_hash,
    )
    run_ledger.transition_run(
        run_id, run_ledger.RunStatus.APPLIED, error_code=error_code,
        warnings=(error_code,), latency_ms=latency_ms, input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return {
        "run_id": run_id, "decision_kind": header.decision_kind,
        "mode": header.mode.value, "action": getattr(result, "action"),
        "selected_ids": list(selected_ids), "reason_codes": list(reason_codes),
        "confidence_band": getattr(result, "confidence_band"), "fallback_used": True,
        "json_repaired_once": False, "error_code": error_code,
        "application_allowed": False,
    }


def diagnostics(*, decision_kind: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "registry_version": REGISTRY_VERSION,
        "registry": REGISTRY.public_snapshot(),
        "runs": run_ledger.list_diagnostics(decision_kind=decision_kind, limit=limit),
        "events": run_ledger.list_diagnostic_events(
            decision_kind=decision_kind, limit=min(max(int(limit) * 3, 1), 400),
        ),
    }

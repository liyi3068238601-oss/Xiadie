"""Minimal CDS adapter contracts for future LIFE and KIG implementations.

The contracts are deliberately body-free and contain no domain implementation.
LIFE remains the owner of life sources; KIG remains the owner of validated
knowledge/PWM candidates; domain owners remain the only effect writers.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Protocol, TypedDict, runtime_checkable

CONTRACT_VERSION = "specialty-adapter-contract-v1"

SourceKind = Literal[
    "life_event",
    "diary_entry",
    "important_date",
    "personal_goal",
    "self_timeline",
    "knowledge_object",
    "pwm_projection",
]

LIFE_SOURCE_KINDS = frozenset({
    "life_event", "diary_entry", "important_date", "personal_goal", "self_timeline",
})
KIG_CANDIDATE_SOURCE_KINDS = frozenset({"knowledge_object", "pwm_projection"})
PROGRAMMATIC_ONLY_SIGNALS = frozenset({"unanswered_pressure"})
BACKGROUND_NARRATIVE_PRIORITY = "background"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class RevisionRef(TypedDict):
    kind: SourceKind
    id: str
    revision: str
    content_hash: str


class CandidateEnvelope(TypedDict):
    id: str
    source: RevisionRef
    candidate_kind: str
    candidate_revision: str
    content_hash: str


class DecisionResult(TypedDict):
    protocol_version: str
    run_id: str
    decision_kind: str
    mode: str
    action: str
    selected_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str
    fallback_used: bool
    application_allowed: bool
    source_snapshot_hash: str


@runtime_checkable
class LifeSourceProvider(Protocol):
    """Future LIFE implementation exposes revisions, never CDS-owned records."""

    def read_source(self, source_id: str) -> RevisionRef | None: ...


@runtime_checkable
class KigCandidateProvider(Protocol):
    """Future KIG implementation exposes already-authorized finite candidates."""

    def list_candidates(self, request_id: str) -> tuple[CandidateEnvelope, ...]: ...


def validate_revision_ref(value: RevisionRef) -> None:
    expected = {"kind", "id", "revision", "content_hash"}
    if set(value) != expected:
        raise ValueError("revision ref fields do not match the frozen contract")
    if value["kind"] not in LIFE_SOURCE_KINDS | KIG_CANDIDATE_SOURCE_KINDS:
        raise ValueError("source kind is not registered")
    if not value["id"] or not value["revision"]:
        raise ValueError("source identity and revision must be non-empty")
    if not _HEX64.fullmatch(value["content_hash"]):
        raise ValueError("source content_hash must be sha256")


def validate_candidate_envelope(value: CandidateEnvelope) -> None:
    expected = {"id", "source", "candidate_kind", "candidate_revision", "content_hash"}
    if set(value) != expected:
        raise ValueError("candidate fields do not match the frozen contract")
    validate_revision_ref(value["source"])
    if value["source"]["kind"] not in KIG_CANDIDATE_SOURCE_KINDS:
        raise ValueError("LIFE objects are sources, not shared candidates")
    if not value["id"] or not value["candidate_kind"] or not value["candidate_revision"]:
        raise ValueError("candidate identity and revision must be non-empty")
    if not _HEX64.fullmatch(value["content_hash"]):
        raise ValueError("candidate content_hash must be sha256")


def validate_decision_result(
    value: DecisionResult, *, candidate_ids: tuple[str, ...], source_snapshot_hash: str,
) -> None:
    expected = {
        "protocol_version", "run_id", "decision_kind", "mode", "action", "selected_ids",
        "reason_codes", "confidence_band", "fallback_used", "application_allowed",
        "source_snapshot_hash",
    }
    if set(value) != expected:
        raise ValueError("decision result fields do not match the frozen contract")
    if value["protocol_version"] != "cognitive-decision-v1":
        raise ValueError("decision protocol is not supported")
    if not value["run_id"] or not value["decision_kind"]:
        raise ValueError("decision identity must be non-empty")
    if value["mode"] not in {"shadow", "advisory", "active"}:
        raise ValueError("decision mode is invalid")
    if value["action"] not in {"select", "skip", "ask"}:
        raise ValueError("decision action is invalid")
    if value["confidence_band"] not in {"low", "medium", "high"}:
        raise ValueError("decision confidence is invalid")
    if (
        not isinstance(value["selected_ids"], tuple)
        or not isinstance(value["reason_codes"], tuple)
        or any(not isinstance(item, str) or not item for item in value["reason_codes"])
    ):
        raise ValueError("decision collections are invalid")
    if value["action"] == "select" and not value["selected_ids"]:
        raise ValueError("select action requires a candidate")
    if value["action"] != "select" and value["selected_ids"]:
        raise ValueError("non-select action cannot select candidates")
    if value["source_snapshot_hash"] != source_snapshot_hash:
        raise ValueError("decision result source revision changed")
    if not _HEX64.fullmatch(value["source_snapshot_hash"]):
        raise ValueError("source snapshot hash must be sha256")
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidate IDs must be unique")
    if not set(value["selected_ids"]).issubset(candidate_ids):
        raise ValueError("decision selected a non-candidate ID")
    if value["application_allowed"]:
        raise ValueError("shared LIFE/KIG contract cannot grant domain application")


def event_idempotency_key(*, event_kind: Literal["life_event", "contact_event"],
                          event_id: str, revision: str) -> str:
    """Freeze stable identity for future LIFE events and existing EAP contact events."""
    if event_kind not in {"life_event", "contact_event"}:
        raise ValueError("unsupported event kind")
    if not event_id or not revision:
        raise ValueError("event identity and revision must be non-empty")
    encoded = json.dumps(
        {"event_kind": event_kind, "event_id": event_id, "revision": revision},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{CONTRACT_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


def narrative_planning_allowed(*, network_online: bool, shutting_down: bool,
                               priority: str) -> bool:
    """Narrative planning is background-only and never runs during offline exit."""
    return bool(
        network_online and not shutting_down and priority == BACKGROUND_NARRATIVE_PRIORITY
    )

"""Read-only EAP projection over CDS-owned DecisionRun diagnostics."""
from __future__ import annotations

from typing import TypedDict

from .. import cognitive_decision as cds
from . import run_ledger

ADAPTER_VERSION = "eap-decision-run-adapter-v1"
DIAGNOSTIC_ADAPTER_VERSION = "eap-decision-run-diagnostic-v2"


class EapDecisionRunView(TypedDict):
    adapter_version: str
    run_id: str
    decision_kind: str
    protocol_version: str
    mode: str
    status: str
    source_revision: str
    snapshot_hash: str
    candidate_snapshot_hash: str
    action: str | None
    selected_count: int
    reason_codes: tuple[str, ...]
    confidence_band: str | None
    fallback_used: bool
    application_allowed: bool


class EapDecisionRunDiagnosticV2(EapDecisionRunView):
    error_code: str | None
    latency_ms: int | None


def read_eap_decision_run(run_id: str) -> EapDecisionRunView | None:
    """Read an EAP-owned CDS run without acquiring or applying any domain write right.

    Selected candidate IDs are intentionally absent because the shared ledger is
    body-free. EAP's existing validator/reducer remains the only path that can
    create candidates, authorize intensity, deliver, or persist feedback.
    """
    run = run_ledger.get_run(run_id)
    if run is None:
        return None
    try:
        definition = cds.REGISTRY.get(run.task_kind)
    except cds.DecisionProtocolError as exc:
        raise cds.DecisionProtocolError(
            "eap_decision_kind_unregistered", "DecisionRun is not a registered CDS task",
        ) from exc
    if definition.application_owner != "eap":
        raise cds.DecisionProtocolError(
            "eap_application_owner_mismatch", "DecisionRun does not belong to EAP",
        )
    return {
        "adapter_version": ADAPTER_VERSION,
        "run_id": run.id,
        "decision_kind": run.task_kind,
        "protocol_version": run.protocol_version,
        "mode": run.mode,
        "status": run.status,
        "source_revision": run.source_revision,
        "snapshot_hash": run.snapshot_hash,
        "candidate_snapshot_hash": run.candidate_snapshot_hash,
        "action": run.action,
        "selected_count": run.selected_count,
        "reason_codes": tuple(run.reason_codes),
        "confidence_band": run.confidence_band,
        "fallback_used": run.fallback_used,
        "application_allowed": False,
    }


def read_eap_decision_run_v2(run_id: str) -> EapDecisionRunDiagnosticV2 | None:
    """Versioned CDS.13 extension; v1 remains unchanged for compatibility."""
    view = read_eap_decision_run(run_id)
    if view is None:
        return None
    run = run_ledger.get_run(run_id)
    if run is None:  # fail closed if the diagnostic expired between the two reads
        return None
    return {
        **view,
        "adapter_version": DIAGNOSTIC_ADAPTER_VERSION,
        "error_code": run.error_code,
        "latency_ms": run.latency_ms,
    }

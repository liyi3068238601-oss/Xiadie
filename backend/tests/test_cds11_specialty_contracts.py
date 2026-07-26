"""CDS.11 freezes minimal LIFE/KIG contracts and the read-only EAP adapter."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import cognitive_decision as cds
from app import db, presence_thread_shadow as observer
from app import specialty_contracts as contracts
from app.proactive import decision_run_adapter
from app.proactive import presence


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _domain_snapshot() -> dict[str, list[tuple]]:
    tables = (
        "conversation_presence", "proactive_candidates", "proactive_decisions",
        "proactive_deliveries", "proactive_feedback", "life_proactive_seeds",
    )
    conn = db.connect()
    try:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]
            for table in tables
        }
    finally:
        conn.close()


def _completed_presence_run():
    text = "我去测试一下"
    legacy = presence.detect_presence_signals(text)
    payload = observer.PresenceThreadInput(
        candidate_ids=observer.candidate_ids(), source_message_id="m-cds11",
        valid_message_ids=("m-cds11",), text=text, silence_observed=False,
        legacy_presence_state=legacy.user_status, legacy_open_thread=legacy.open_thread,
        legacy_open_thread_topic=legacy.open_thread_topic,
    )
    source = (cds.SourceSnapshot("message", "m-cds11", "1", _sha(text)),)
    header = cds.build_header(
        decision_kind=observer.DECISION_KIND, policy_version=observer.POLICY_VERSION,
        request_id="cds11-eap-adapter", mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    candidates = tuple(
        cds.CandidateRef(item, "presence_semantic", _sha(item))
        for item in observer.candidate_ids()
    )
    result = observer.observe_shadow(payload)
    raw = json.dumps({
        **result.__dict__,
        "selected_ids": list(result.selected_ids),
        "reason_codes": list(result.reason_codes),
        "evidence_message_ids": list(result.evidence_message_ids),
        "open_threads": list(result.open_threads),
    })
    run, _ = cds.create_run(header, payload, candidates)
    cds.evaluate_output(run.id, header, payload, raw, current_snapshot=source)
    return run.id


def test_life_revisions_are_sources_and_kig_objects_are_validated_candidates():
    life_source: contracts.RevisionRef = {
        "kind": "life_event", "id": "life-1", "revision": "3", "content_hash": _sha("life"),
    }
    contracts.validate_revision_ref(life_source)
    with pytest.raises(ValueError, match="sources, not shared candidates"):
        contracts.validate_candidate_envelope({
            "id": "candidate-life", "source": life_source,
            "candidate_kind": "life_share", "candidate_revision": "1",
            "content_hash": _sha("candidate-life"),
        })

    knowledge_source: contracts.RevisionRef = {
        "kind": "knowledge_object", "id": "knowledge-1", "revision": "7",
        "content_hash": _sha("knowledge"),
    }
    contracts.validate_candidate_envelope({
        "id": "candidate-knowledge", "source": knowledge_source,
        "candidate_kind": "retrieval_evidence", "candidate_revision": "2",
        "content_hash": _sha("candidate-knowledge"),
    })


def test_contracts_reject_bodies_unbound_candidates_and_application_rights():
    source: contracts.RevisionRef = {
        "kind": "pwm_projection", "id": "pwm-1", "revision": "1",
        "content_hash": _sha("pwm"),
    }
    with pytest.raises(ValueError, match="fields"):
        contracts.validate_revision_ref({**source, "body": "must not cross adapter"})  # type: ignore[typeddict-item]

    result: contracts.DecisionResult = {
        "protocol_version": "cognitive-decision-v1", "run_id": "run-1",
        "decision_kind": "future-kig-rerank", "mode": "shadow", "action": "select",
        "selected_ids": ("not-a-candidate",), "reason_codes": ("relevant",),
        "confidence_band": "high", "fallback_used": False,
        "application_allowed": False, "source_snapshot_hash": _sha("snapshot"),
    }
    with pytest.raises(ValueError, match="non-candidate"):
        contracts.validate_decision_result(
            result, candidate_ids=("candidate-1",), source_snapshot_hash=_sha("snapshot"),
        )
    result["selected_ids"] = ("candidate-1",)
    result["application_allowed"] = True
    with pytest.raises(ValueError, match="cannot grant"):
        contracts.validate_decision_result(
            result, candidate_ids=("candidate-1",), source_snapshot_hash=_sha("snapshot"),
        )


def test_narrative_planner_is_background_only_and_offline_exit_never_runs_it():
    assert contracts.PROGRAMMATIC_ONLY_SIGNALS == {"unanswered_pressure"}
    assert contracts.narrative_planning_allowed(
        network_online=True, shutting_down=False, priority="background",
    )
    assert not contracts.narrative_planning_allowed(
        network_online=False, shutting_down=False, priority="background",
    )
    assert not contracts.narrative_planning_allowed(
        network_online=True, shutting_down=True, priority="background",
    )
    assert not contracts.narrative_planning_allowed(
        network_online=True, shutting_down=False, priority="foreground",
    )


def test_life_and_contact_event_identity_is_revision_bound_and_idempotent():
    first = contracts.event_idempotency_key(
        event_kind="life_event", event_id="event-1", revision="4",
    )
    assert first == contracts.event_idempotency_key(
        event_kind="life_event", event_id="event-1", revision="4",
    )
    assert first != contracts.event_idempotency_key(
        event_kind="life_event", event_id="event-1", revision="5",
    )
    contact = contracts.event_idempotency_key(
        event_kind="contact_event", event_id="episode-1", revision="4",
    )
    assert contact != first
    assert contracts.event_idempotency_key(
        event_kind="life_event", event_id="event:1", revision="4",
    ) != contracts.event_idempotency_key(
        event_kind="life_event", event_id="event", revision="1:4",
    )


def test_eap_adapter_concurrent_reads_are_stable_and_never_write_domain_state():
    run_id = _completed_presence_run()
    before = _domain_snapshot()
    with ThreadPoolExecutor(max_workers=8) as pool:
        views = list(pool.map(decision_run_adapter.read_eap_decision_run, [run_id] * 32))
    after = _domain_snapshot()

    assert all(view == views[0] for view in views)
    assert views[0] is not None
    assert views[0]["adapter_version"] == "eap-decision-run-adapter-v1"
    assert views[0]["decision_kind"] == observer.DECISION_KIND
    assert views[0]["mode"] == "shadow"
    assert views[0]["application_allowed"] is False
    assert "selected_ids" not in views[0]
    assert before == after


def test_eap_adapter_rejects_a_run_owned_by_another_domain():
    payload = cds.ProtocolProbeInput(candidate_ids=("candidate-1",))
    source = (cds.SourceSnapshot("synthetic", "source-1", "1", _sha("source")),)
    header = cds.build_header(
        decision_kind="protocol_probe", policy_version="v1", request_id="cds11-not-eap",
        mode=cds.DecisionMode.SHADOW, source_snapshot=source,
    )
    candidates = (cds.CandidateRef("candidate-1", "synthetic", _sha("candidate")),)
    run, _ = cds.create_run(header, payload, candidates)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        decision_run_adapter.read_eap_decision_run(run.id)
    assert exc.value.code == "eap_application_owner_mismatch"

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from app import cognitive_decision as cds
from app import db, memory
from app import archivist, memory_conflicts
from app import memory_shadow_proposals as proposals

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cds9_memory_shadow_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-9-memory-shadow.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "cds-9-memory-shadow.md"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cds9_memory_shadow_fixture.py"
RUNNER_PATH = BACKEND_DIR / "scripts" / "run_cds9_memory_shadow.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _payload(case: dict):
    values = case["input"]
    if case["decision_kind"] == proposals.CONFLICT_DECISION_KIND:
        return proposals.MemoryConflictInput(
            candidate_ids=tuple(values["candidate_ids"]),
            older_id=values["older_id"],
            newer_id=values["newer_id"],
            older_origin=values["older_origin"],
            newer_origin=values["newer_origin"],
            relation_hint=values["relation_hint"],
            condition_changed=values["condition_changed"],
        )
    return proposals.MemoryRetentionInput(
        candidate_ids=tuple(values["candidate_ids"]),
        fragment_id=values["fragment_id"],
        origin=values["origin"],
        status=values["status"],
        retention_band=values["retention_band"],
        protected=values["protected"],
        injection_only=values["injection_only"],
    )


def test_fixture_is_deterministic_synthetic_and_balances_both_decision_kinds():
    fixture = _fixture()
    assert fixture == runpy.run_path(str(GENERATOR_PATH))["build_fixture"]()
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 280
    counts = {
        kind: sum(case["decision_kind"] == kind for case in fixture["cases"])
        for kind in {case["decision_kind"] for case in fixture["cases"]}
    }
    assert counts == {
        proposals.CONFLICT_DECISION_KIND: 160,
        proposals.RETENTION_DECISION_KIND: 120,
    }
    assert len({case["group"] for case in fixture["cases"]}) == 14
    semantic_inputs = {
        json.dumps({
            "protocol": {
                key: value for key, value in case["input"].items()
                if key not in {"candidate_ids", "older_id", "newer_id", "fragment_id"}
            },
            "scenario": case["scenario"],
        }, sort_keys=True, ensure_ascii=False)
        for case in fixture["cases"]
    }
    assert len(semantic_inputs) == 280


def test_oracle_checks_safety_without_fixture_labels_or_expected_outputs():
    oracle = __import__("app.memory_shadow_oracle", fromlist=["safety_violations"])
    for case in _fixture()["cases"]:
        payload = _payload(case)
        result = proposals.conflict_fallback(payload) if case["decision_kind"] == proposals.CONFLICT_DECISION_KIND else proposals.retention_fallback(payload)
        assert oracle.safety_violations(case["decision_kind"], payload, result) == ()


@pytest.mark.parametrize("case", _fixture()["cases"])
def test_pure_fallback_matches_all_synthetic_cases(case):
    payload = _payload(case)
    if case["decision_kind"] == proposals.CONFLICT_DECISION_KIND:
        result = proposals.conflict_fallback(payload)
        proposals.validate_conflict(payload, result)
        assert result.relation_type == case["expected"]["relation_type"]
        assert result.superseded_id == case["expected"]["superseded_id"]
        assert result.tombstone_allowed is False
    else:
        result = proposals.retention_fallback(payload)
        proposals.validate_retention(payload, result)
        assert result.proposed_action == case["expected"]["proposed_action"]
        assert result.recovery_allowed is case["expected"]["recovery_allowed"]
        assert result.tombstone_allowed is False
    assert result.advisory_only is True


def test_registry_has_two_distinct_shadow_only_schemas_and_mem_ownership():
    conflict = cds.REGISTRY.get(proposals.CONFLICT_DECISION_KIND)
    retention = cds.REGISTRY.get(proposals.RETENTION_DECISION_KIND)
    assert conflict.mode is retention.mode is cds.DecisionMode.SHADOW
    assert conflict.input_type is proposals.MemoryConflictInput
    assert conflict.result_type is proposals.MemoryConflictProposal
    assert retention.input_type is proposals.MemoryRetentionInput
    assert retention.result_type is proposals.MemoryRetentionProposal
    assert conflict.input_schema_hash != retention.input_schema_hash
    assert conflict.output_schema_hash != retention.output_schema_hash
    assert conflict.fallback_owner == retention.fallback_owner == "mem"
    assert conflict.application_owner == retention.application_owner == "mem"


def test_conflict_fallback_reuses_legacy_pure_projection():
    assert memory_conflicts.classify_projection("用户喜欢咖啡", "用户不喜欢咖啡") == {
        "relation_type": "superseded",
        "confidence": 0.98,
        "reason_code": "explicit_negation_newer_wins",
    }
    payload = proposals.MemoryConflictInput(
        candidate_ids=("older", "newer"),
        older_id="older",
        newer_id="newer",
        older_origin="user_confirmed",
        newer_origin="user_confirmed",
        relation_hint="contradiction",
        condition_changed=False,
    )
    result = proposals.conflict_fallback(payload)
    assert result.relation_type == "supersedes"
    assert result.superseded_id == "older"
    assert memory_conflicts.classify_projection("相同", "相同") == {
        "relation_type": None,
        "confidence": 0.0,
        "reason_code": "",
    }


def test_archivist_projection_matches_transition_without_writing():
    now = 2_300_000_000.0
    item = memory.create_memory("L1", f"cds9-projection-{db.new_id()}")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET importance=0,confidence=0,scope='world',"
            "kind='observation',created_at=?,updated_at=? WHERE id=?",
            (now - 200 * 86_400, now - 200 * 86_400, item["id"]),
        )
        conn.commit()
        snapshot = archivist.load_fragment_snapshots([item["id"]])[item["id"]]
    finally:
        conn.close()
    projected = archivist.project_lifecycle(snapshot, now=now)
    assert projected["target_status"] == "cooling"
    assert projected["reason_code"] == "retention_below_cooling"
    assert memory.get_memory(item["id"])["status"] == "active"
    applied = archivist.assess_and_transition(item["id"], now=now)
    assert applied["fragment"]["status"] == projected["target_status"]
    assert applied["reason_code"] == projected["reason_code"]


def test_real_fragment_adapter_binds_revision_hash_state_sensitivity_and_source():
    item = memory.create_memory("L1", f"cds9-adapter-{db.new_id()}")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET lifecycle_revision=7,status='cooling',enabled=0,"
            "sensitivity='sensitive',observation_source='knowledge_reference' WHERE id=?",
            (item["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    binding = proposals.load_fragment_bindings([item["id"]])[0]
    assert binding.fragment_id == item["id"]
    assert binding.revision == "7"
    assert len(binding.content_hash) == 64
    assert binding.status == "cooling" and binding.enabled is False
    assert binding.sensitivity == "sensitive"
    assert binding.origin == "system_injected"
    assert proposals.source_snapshots((binding,))[0].revision == "7"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("importance", 0.25),
        ("confidence", 0.25),
        ("recall_count", 3),
        ("last_recalled_at", 2_200_000_000.0),
        ("created_at", 2_100_000_000.0),
        ("updated_at", 2_200_000_001.0),
        ("cooling_since", 2_000_000_000.0),
        ("scope", "relationship"),
        ("kind", "correction"),
        ("layer", "L0"),
    ],
)
def test_projection_dependency_changes_invalidate_source_hash(column, value):
    item = memory.create_memory("L1", f"cds9-hash-{column}-{db.new_id()}")
    before = proposals.load_fragment_bindings((item["id"],))[0].content_hash
    conn = db.connect()
    try:
        conn.execute(f'UPDATE memory_fragments SET "{column}"=? WHERE id=?', (value, item["id"]))
        conn.commit()
    finally:
        conn.close()
    after = proposals.load_fragment_bindings((item["id"],))[0].content_hash
    assert after != before


def test_real_fragment_adapters_use_one_consistent_database_snapshot(monkeypatch):
    older = memory.create_memory("L1", f"cds9-snapshot-older-{db.new_id()}")
    newer = memory.create_memory("L1", f"cds9-snapshot-newer-{db.new_id()}")
    original_connect = db.connect
    calls = []

    def counted_connect():
        calls.append(None)
        return original_connect()

    monkeypatch.setattr(db, "connect", counted_connect)
    proposals.build_conflict_input(older["id"], newer["id"])
    assert len(calls) == 1
    calls.clear()
    proposals.build_retention_input(older["id"], now=db.now())
    assert len(calls) == 1


def test_real_fragment_change_invalidates_shadow_source_binding():
    item = memory.create_memory("L1", f"cds9-stale-{db.new_id()}")
    payload = proposals.build_retention_input(item["id"], now=db.now())
    source = proposals.source_snapshots(payload.fragment_bindings)
    candidates = proposals.candidate_refs(payload.fragment_bindings)
    definition = cds.REGISTRY.get(proposals.RETENTION_DECISION_KIND)
    header = cds.build_header(
        decision_kind=proposals.RETENTION_DECISION_KIND,
        policy_version=definition.output_schema_version,
        request_id=f"cds9-stale-{item['id']}", mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    run, _ = cds.create_run(header, payload, candidates)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET lifecycle_revision=lifecycle_revision+1,status='cooling'"
            " WHERE id=?", (item["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    current = proposals.source_snapshots(proposals.load_fragment_bindings((item["id"],)))
    result = proposals.retention_fallback(payload)
    outcome = cds.evaluate_output(
        run.id, header, payload, json.dumps(result.__dict__), current_snapshot=current,
    )
    assert outcome["error_code"] == "source_revision_changed"
    assert outcome["application_allowed"] is False


def test_action_matrix_rejects_semantically_inconsistent_combinations():
    case = next(case for case in _fixture()["cases"] if case["group"] == "compatible_pair")
    payload = _payload(case)
    result = proposals.conflict_fallback(payload)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        proposals.validate_conflict(payload, proposals.MemoryConflictProposal(
            **{**result.__dict__, "relation_type": "supersedes", "superseded_id": payload.older_id}
        ))
    assert exc.value.code == "conflict_action_matrix_invalid"
    retention_case = next(case for case in _fixture()["cases"] if case["group"] == "active_cool")
    retention_payload = _payload(retention_case)
    retention = proposals.retention_fallback(retention_payload)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        proposals.validate_retention(retention_payload, proposals.MemoryRetentionProposal(
            **{**retention.__dict__, "proposed_action": "freeze"}
        ))
    assert exc.value.code == "retention_action_matrix_invalid"


def test_fragment_binding_fields_cannot_be_relabelled_by_protocol_input():
    item = memory.create_memory("L1", f"cds9-relabel-{db.new_id()}")
    payload = proposals.build_retention_input(item["id"], now=db.now())
    forged = proposals.MemoryRetentionInput(**{**payload.__dict__, "origin": "user_confirmed"})
    with pytest.raises(cds.DecisionProtocolError) as exc:
        proposals.validate_retention(forged, proposals.retention_fallback(forged))
    assert exc.value.code == "fragment_binding_mismatch"


def test_report_distinguishes_shadow_ledger_writes_from_mem_domain_writes():
    report = runpy.run_path(str(RUNNER_PATH))["build_report"](_fixture())
    assert report["shadow_ledger_write_count"] > 0
    assert report["mem_domain_write_count"] == 0
    assert report["oracle_version"] == "cds9-memory-safety-oracle-v3"


def test_equal_sources_follow_newer_wins_for_explicit_negation():
    for origin in ("user_confirmed", "observed", "automatic", "system_injected"):
        payload = proposals.MemoryConflictInput(
            candidate_ids=("older", "newer"),
            older_id="older",
            newer_id="newer",
            older_origin=origin,
            newer_origin=origin,
            relation_hint="contradiction",
            condition_changed=False,
        )
        result = proposals.conflict_fallback(payload)
        proposals.validate_conflict(payload, result)
        assert result.relation_type == "supersedes"
        assert result.superseded_id == "older"


def test_weak_sources_never_supersede_user_confirmation():
    cases = [
        case for case in _fixture()["cases"]
        if case["group"] in {"automatic_cannot_supersede_user", "injection_cannot_supersede_user"}
    ]
    for case in cases:
        result = proposals.conflict_fallback(_payload(case))
        assert result.relation_type == "possible_conflict"
        assert result.superseded_id is None


def test_injection_only_evidence_never_recovers_frozen_memory():
    cases = [case for case in _fixture()["cases"] if case["group"] == "injection_no_recovery"]
    for case in cases:
        result = proposals.retention_fallback(_payload(case))
        assert result.proposed_action == "keep"
        assert result.recovery_allowed is False


def test_validators_reject_tombstone_and_direct_application():
    conflict_case = next(case for case in _fixture()["cases"] if case["decision_kind"] == proposals.CONFLICT_DECISION_KIND)
    conflict_payload = _payload(conflict_case)
    conflict = proposals.conflict_fallback(conflict_payload)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        proposals.validate_conflict(conflict_payload, proposals.MemoryConflictProposal(
            **{**conflict.__dict__, "tombstone_allowed": True}
        ))
    assert exc.value.code == "tombstone_forbidden"
    retention_case = next(case for case in _fixture()["cases"] if case["decision_kind"] == proposals.RETENTION_DECISION_KIND)
    retention_payload = _payload(retention_case)
    retention = proposals.retention_fallback(retention_payload)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        proposals.validate_retention(retention_payload, proposals.MemoryRetentionProposal(
            **{**retention.__dict__, "advisory_only": False}
        ))
    assert exc.value.code == "application_boundary_invalid"


def test_runtime_keeps_both_kinds_non_applicable():
    for case in (
        next(case for case in _fixture()["cases"] if case["decision_kind"] == proposals.CONFLICT_DECISION_KIND),
        next(case for case in _fixture()["cases"] if case["decision_kind"] == proposals.RETENTION_DECISION_KIND),
    ):
        payload = _payload(case)
        kind = case["decision_kind"]
        definition = cds.REGISTRY.get(kind)
        source = (cds.SourceSnapshot("memory_fragment", payload.candidate_ids[0], "1", "e" * 64),)
        header = cds.build_header(
            decision_kind=kind,
            policy_version=definition.output_schema_version,
            request_id=f"cds9-{kind}",
            mode=cds.DecisionMode.SHADOW,
            source_snapshot=source,
        )
        candidates = tuple(
            cds.CandidateRef(item, "memory_fragment", hashlib.sha256(item.encode()).hexdigest())
            for item in payload.candidate_ids
        )
        result = definition.fallback(payload)
        raw = json.dumps(result.__dict__, ensure_ascii=False)
        run, _ = cds.create_run(header, payload, candidates)
        outcome = cds.evaluate_output(run.id, header, payload, raw, current_snapshot=source)
        assert outcome["application_allowed"] is False


def test_main_import_registers_both_memory_shadow_kinds():
    completed = subprocess.run(
        [sys.executable, "-c", (
            "from app import main, cognitive_decision as c; "
            "print(c.REGISTRY.get('memory_conflict_proposal').decision_kind); "
            "print(c.REGISTRY.get('memory_retention_proposal').decision_kind)"
        )],
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.splitlines() == [
        proposals.CONFLICT_DECISION_KIND, proposals.RETENTION_DECISION_KIND,
    ]


def test_report_is_body_free_schema_stable_and_production_tables_unchanged():
    runner = runpy.run_path(str(RUNNER_PATH))
    dynamic = runner["build_report"](_fixture())
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report == dynamic
    assert MARKDOWN_PATH.read_text(encoding="utf-8") == runner["render_markdown"](dynamic)
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["sample_count"] == 280
    assert report["proposal_exact_rate"] == 1.0
    assert report["weak_source_override_rate"] == 0.0
    assert report["injection_recovery_rate"] == 0.0
    assert report["tombstone_proposal_rate"] == 0.0
    assert report["shadow_ledger_write_count"] == 2
    assert report["changed_shadow_ledger_tables"] == ["decision_run_events", "decision_runs"]
    assert report["mem_domain_write_count"] == 0
    assert report["changed_mem_domain_tables"] == []
    assert report["rule_template_count"] == 14
    assert report["safety_violation_count"] == 0
    assert report["oracle_version"] == "cds9-memory-safety-oracle-v3"
    assert report["schema_version"] == 81
    assert report["stage_schema_baseline"] == 62 and report["schema_changed"] is False
    encoded = json.dumps(report, ensure_ascii=False)
    assert "raw_model_output" not in encoded and '"input"' not in encoded

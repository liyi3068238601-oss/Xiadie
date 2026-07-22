"""CDS.1 shared protocol, source validation, ledger and privacy boundaries."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import cognitive_decision as cds
from app import db
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def clean_decision_runs():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM decision_runs")
        conn.commit()
    finally:
        conn.close()
    yield


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _request(*, mode: cds.DecisionMode = cds.DecisionMode.SHADOW, request_id: str = "req-1"):
    source = (cds.SourceSnapshot("message", "m1", "1", _hash("source-v1")),)
    candidates = (
        cds.CandidateRef("c1", "memory_fragment", _hash("candidate-1")),
        cds.CandidateRef("c2", "memory_fragment", _hash("candidate-2")),
    )
    payload = cds.ProtocolProbeInput(candidate_ids=("c1", "c2"))
    header = cds.build_header(
        decision_kind="protocol_probe", policy_version="probe-policy-v1",
        request_id=request_id, mode=mode, source_snapshot=source,
    )
    return header, payload, candidates, source


def _valid_output(candidate_id: str = "c1") -> str:
    return json.dumps({
        "action": "select", "selected_ids": [candidate_id],
        "reason_codes": ["directly_relevant"], "confidence_band": "high",
    })


def test_schema_62_extends_shared_ledger_without_parallel_run_table():
    conn = db.connect()
    try:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(decision_runs)")}
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    finally:
        conn.close()
    assert version == "62"
    assert {
        "policy_version", "mode", "source_snapshot_json", "snapshot_hash",
        "candidate_snapshot_hash", "candidate_count", "selected_count", "action",
        "confidence_band", "reason_codes_json", "fallback_used", "prompt_template_hash",
        "input_schema_hash", "output_schema_hash", "validator_version", "fallback_version",
        "model_binding_revision", "retention_class", "expires_at", "privacy_scope",
        "logical_role", "provider_location_revision", "certification_level",
    } <= columns
    assert "decision_run_events" in tables
    assert not ({"cognitive_decision_runs", "cds_decision_runs"} & tables)


def test_migration_61_preserves_schema_56_rows_as_legacy():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE decision_runs (
            id TEXT PRIMARY KEY, task_kind TEXT NOT NULL, protocol_version TEXT NOT NULL,
            source_type TEXT NOT NULL, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
            source_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL, attempt_count INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
            next_attempt_at REAL, provider_id TEXT, model_id TEXT, latency_ms INTEGER,
            input_tokens INTEGER, output_tokens INTEGER, error_code TEXT,
            warnings_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            completed_at REAL
        );
        INSERT INTO decision_runs VALUES(
            'legacy-run','companion_cognition','companion-cognition-v1','conversation_turn',
            'm1|m2','r1','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'legacy-key','applied',1,3,NULL,'mock','xiadie-mock',2,3,4,NULL,'[]',1,2,2
        );
    """)
    migration = next(sql for version, sql in db.MIGRATIONS if version == 61)
    conn.executescript(migration)
    row = conn.execute("SELECT * FROM decision_runs WHERE id='legacy-run'").fetchone()
    assert row["mode"] == "legacy" and row["policy_version"] == ""
    assert row["source_snapshot_json"] == "[]" and row["candidate_count"] == 0
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='decision_run_events'"
    ).fetchone()
    conn.close()


def test_registry_uses_kind_specific_schemas_and_rejects_duplicates():
    definition = cds.REGISTRY.get("protocol_probe")
    assert definition.input_type is cds.ProtocolProbeInput
    assert definition.result_type is cds.ProtocolProbeResult
    assert len(definition.input_schema_hash) == len(definition.output_schema_hash) == 64
    with pytest.raises(cds.DecisionProtocolError, match="already registered"):
        cds.REGISTRY.register(definition)


def test_create_run_is_idempotent_and_records_reproducible_metadata():
    header, payload, candidates, _ = _request()
    run, created = cds.create_run(
        header, payload, candidates, provider_id="mock", model_id="xiadie-mock",
        provider_location="local", temperature=0.0, top_p=1.0, now=100.0,
    )
    duplicate, duplicate_created = cds.create_run(
        header, payload, candidates, provider_id="mock", model_id="xiadie-mock", now=101.0,
    )
    assert created is True and duplicate_created is False and duplicate.id == run.id
    assert run.mode == "shadow" and run.policy_version == "probe-policy-v1"
    assert run.snapshot_hash == header.snapshot_hash
    assert run.candidate_count == 2 and len(run.candidate_snapshot_hash) == 64
    assert run.input_schema_hash and run.output_schema_hash and run.prompt_template_hash
    conn = db.connect()
    try:
        events = conn.execute(
            "SELECT event_type,to_status FROM decision_run_events WHERE run_id=?", (run.id,),
        ).fetchall()
    finally:
        conn.close()
    assert [(row["event_type"], row["to_status"]) for row in events] == [("created", "queued")]


def test_candidate_content_revision_changes_idempotency_identity():
    header, payload, candidates, _ = _request()
    first, _ = cds.create_run(header, payload, candidates)
    changed = (
        cds.CandidateRef("c1", "memory_fragment", _hash("candidate-1-revised")),
        candidates[1],
    )
    second, second_created = cds.create_run(header, payload, changed)
    assert second_created is True and second.id != first.id
    assert second.candidate_snapshot_hash != first.candidate_snapshot_hash


def test_non_candidate_id_falls_back_and_can_never_apply():
    header, payload, candidates, source = _request()
    run, _ = cds.create_run(header, payload, candidates)
    outcome = cds.evaluate_output(run.id, header, payload, _valid_output("invented"),
                                  current_snapshot=source)
    assert outcome["error_code"] == "candidate_not_allowed"
    assert outcome["fallback_used"] is True
    assert outcome["action"] == "skip" and outcome["selected_ids"] == []
    assert outcome["application_allowed"] is False
    persisted = cds.diagnostics()["runs"][0]
    assert persisted["selected_count"] == 0 and persisted["fallback_used"] is True


def test_duplicate_evaluation_cannot_reapply_or_overwrite_terminal_run():
    header, payload, candidates, source = _request()
    run, _ = cds.create_run(header, payload, candidates)
    first = cds.evaluate_output(run.id, header, payload, _valid_output(), current_snapshot=source)
    assert first["selected_ids"] == ["c1"]
    with pytest.raises(cds.DecisionProtocolError) as exc:
        cds.evaluate_output(run.id, header, payload, _valid_output("c2"), current_snapshot=source)
    assert exc.value.code == "run_not_claimable"
    persisted = cds.run_ledger.get_run(run.id)
    assert persisted.selected_count == 1 and persisted.reason_codes == ["directly_relevant"]


def test_source_revision_change_is_rechecked_and_skipped():
    header, payload, candidates, _ = _request()
    run, _ = cds.create_run(header, payload, candidates)
    changed = (cds.SourceSnapshot("message", "m1", "2", _hash("source-v2")),)
    outcome = cds.evaluate_output(
        run.id, header, payload, _valid_output(), current_snapshot=changed,
    )
    assert outcome["error_code"] == "source_revision_changed"
    assert outcome["application_allowed"] is False
    assert cds.run_ledger.get_run(run.id).status == cds.run_ledger.RunStatus.SKIPPED


def test_exactly_one_json_repair_is_allowed_and_audited():
    header, payload, candidates, source = _request()
    run, _ = cds.create_run(header, payload, candidates)
    fenced = "model preface\n```json\n" + _valid_output() + "\n```\nmodel suffix"
    outcome = cds.evaluate_output(run.id, header, payload, fenced, current_snapshot=source)
    assert outcome["json_repaired_once"] is True
    assert outcome["fallback_used"] is False
    assert outcome["application_allowed"] is False  # Shadow never changes real behavior.
    assert "json_repaired_once" in cds.run_ledger.get_run(run.id).warnings


def test_failed_repair_uses_registered_fallback_without_storing_raw_output():
    header, payload, candidates, source = _request()
    run, _ = cds.create_run(header, payload, candidates)
    secret_raw = "RAW_MODEL_SECRET_WITHOUT_JSON"
    outcome = cds.evaluate_output(run.id, header, payload, secret_raw, current_snapshot=source)
    assert outcome["error_code"] == "json_repair_failed" and outcome["fallback_used"] is True
    conn = db.connect()
    try:
        row = dict(conn.execute("SELECT * FROM decision_runs WHERE id=?", (run.id,)).fetchone())
        events = [dict(item) for item in conn.execute(
            "SELECT * FROM decision_run_events WHERE run_id=?", (run.id,),
        )]
    finally:
        conn.close()
    assert secret_raw not in json.dumps({"run": row, "events": events}, ensure_ascii=False)


def test_internal_outcome_writer_requires_the_validated_candidate_snapshot():
    header, payload, candidates, _ = _request()
    run, _ = cds.create_run(header, payload, candidates)
    with pytest.raises(ValueError, match="snapshot mismatch"):
        cds.run_ledger._record_validated_decision_outcome(  # noqa: SLF001
            run.id, action="skip", selected_count=0, confidence_band="low",
            reason_codes=("structured_fallback",), fallback_used=True,
            validated_candidate_snapshot_hash="0" * 64,
        )


def test_reason_codes_are_allowlisted_and_cannot_store_model_text():
    header, payload, candidates, source = _request()
    run, _ = cds.create_run(header, payload, candidates)
    secret_reason = "用户私密正文不应进入诊断"
    output = json.dumps({
        "action": "select", "selected_ids": ["c1"],
        "reason_codes": [secret_reason], "confidence_band": "high",
    }, ensure_ascii=False)
    outcome = cds.evaluate_output(run.id, header, payload, output, current_snapshot=source)
    assert outcome["error_code"] == "reason_code_not_allowed"
    encoded = json.dumps(cds.diagnostics(), ensure_ascii=False)
    assert secret_reason not in encoded


@pytest.mark.parametrize(
    ("mode", "allow_active", "application_allowed"),
    [
        (cds.DecisionMode.SHADOW, True, False),
        (cds.DecisionMode.ADVISORY, True, False),
        (cds.DecisionMode.ACTIVE, False, False),
        (cds.DecisionMode.ACTIVE, True, True),
    ],
)
def test_shadow_advisory_active_application_gate(
    monkeypatch, mode: cds.DecisionMode, allow_active: bool, application_allowed: bool,
):
    registry = cds.DecisionKindRegistry()
    registry.register(replace(
        cds.REGISTRY.get("protocol_probe"), mode=cds.DecisionMode.ACTIVE,
    ))
    monkeypatch.setattr(cds, "REGISTRY", registry)
    header, payload, candidates, source = _request(mode=mode, request_id=f"req-{mode.value}")
    run, _ = cds.create_run(header, payload, candidates)
    outcome = cds.evaluate_output(
        run.id, header, payload, _valid_output(), current_snapshot=source,
        allow_active_application=allow_active,
    )
    assert outcome["application_allowed"] is application_allowed


def test_read_only_diagnostics_are_body_free_and_expired_rows_are_hidden():
    header, payload, candidates, source = _request()
    run, _ = cds.create_run(header, payload, candidates)
    cds.evaluate_output(run.id, header, payload, _valid_output(), current_snapshot=source)
    response = client.get("/api/cognition/diagnostics")
    assert response.status_code == 200
    encoded = json.dumps(response.json(), ensure_ascii=False)
    for forbidden in (
        "source_snapshot", "snapshot_hash", "candidate_snapshot_hash", "raw_model_output",
        "candidate-1", "source-v1",
    ):
        assert forbidden not in encoded
    conn = db.connect()
    try:
        conn.execute("UPDATE decision_runs SET expires_at=? WHERE id=?", (db.now() - 1, run.id))
        conn.commit()
    finally:
        conn.close()
    assert all(item["id"] != run.id for item in cds.diagnostics()["runs"])

"""CDS.2 routing, certification, circuit breaker and budget boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3

import pytest

from app import cognitive_decision as cds
from app import cognition_runtime as runtime
from app import db, llm


@pytest.fixture(autouse=True)
def clean_runtime_tables():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM decision_run_events")
        conn.execute("DELETE FROM decision_runs")
        conn.execute("DELETE FROM cognition_model_certifications")
        conn.execute("DELETE FROM cognition_circuit_breakers")
        conn.execute("DELETE FROM cognition_budget_events")
        conn.execute("UPDATE providers SET enabled=1 WHERE id IN ('mock','deepseek')")
        conn.commit()
    finally:
        conn.close()
    db.set_setting("current_model", '{"provider_id":"mock","model":"xiadie-mock"}')
    db.set_setting("cognition_model_bindings", "{}")
    yield


def _binding(role: runtime.LogicalRole = runtime.LogicalRole.FAST):
    return runtime.resolve_model_binding(role)


def _request(request_id: str = "cds2-request"):
    digest = hashlib.sha256(b"synthetic-source").hexdigest()
    source = (cds.SourceSnapshot("synthetic", "source-1", "1", digest),)
    candidates = (cds.CandidateRef("synthetic-a", "synthetic", digest),)
    payload = cds.ProtocolProbeInput(candidate_ids=("synthetic-a",))
    header = cds.build_header(
        decision_kind="protocol_probe", policy_version="probe-policy-v1",
        request_id=request_id, mode=cds.DecisionMode.SHADOW, source_snapshot=source,
    )
    return header, payload, candidates, source


def test_migration_62_preserves_cds1_rows_and_adds_control_plane():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE decision_runs (
            id TEXT PRIMARY KEY, candidate_count INTEGER NOT NULL DEFAULT 0,
            selected_count INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO decision_runs(id) VALUES('cds1-row');
    """)
    migration = next(sql for version, sql in db.MIGRATIONS if version == 62)
    conn.executescript(migration)
    row = conn.execute("SELECT * FROM decision_runs").fetchone()
    tables = {item[0] for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert row["logical_role"] == "legacy"
    assert row["certification_level"] == "unverified"
    assert {"cognition_model_certifications", "cognition_circuit_breakers",
            "cognition_budget_events"} <= tables
    assert "cognitive_decision_runs" not in tables
    conn.close()


def test_role_override_reuses_provider_location_and_revision():
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE providers SET execution_location='remote',location_revision=7 WHERE id='deepseek'"
        )
        conn.commit()
    finally:
        conn.close()
    db.set_setting(
        "cognition_model_bindings",
        json.dumps({"reasoning": {"provider_id": "deepseek", "model": "deepseek-reasoner"}}),
    )
    binding = _binding(runtime.LogicalRole.REASONING)
    assert (binding.provider_id, binding.model_id) == ("deepseek", "deepseek-reasoner")
    assert (binding.location, binding.location_revision) == ("remote", 7)


def test_model_or_location_switch_never_inherits_certification():
    binding = _binding()
    runtime._record_certification(  # noqa: SLF001
        binding, "protocol_probe", runtime.CertificationLevel.DECISION_VERIFIED, None,
    )
    assert runtime.get_certification(binding, "protocol_probe") is runtime.CertificationLevel.DECISION_VERIFIED
    db.set_setting("current_model", '{"provider_id":"mock","model":"another-model"}')
    assert runtime.get_certification(_binding(), "protocol_probe") is runtime.CertificationLevel.UNVERIFIED


def test_body_bearing_privacy_fails_closed_by_location_and_certification():
    binding = _binding()
    assert runtime.privacy_error(
        binding, "user_private", runtime.CertificationLevel.STRUCTURED_CAPABLE,
    ) == "local_sensitive_model_not_certified"
    assert runtime.privacy_error(
        binding, "user_private", runtime.CertificationLevel.LOCAL_SENSITIVE_VERIFIED,
    ) is None
    remote = runtime.ModelBinding(
        provider={**binding.provider, "execution_location": "remote"},
        model_id=binding.model_id, logical_role=binding.logical_role, revision="remote-rev",
    )
    assert runtime.privacy_error(
        remote, "user_private", runtime.CertificationLevel.LOCAL_SENSITIVE_VERIFIED,
    ) == "remote_cognition_not_authorized"


def test_structured_probe_is_synthetic_and_requires_exact_json(monkeypatch):
    seen = []

    async def complete(_provider, _model, messages, **_kwargs):
        seen.extend(messages)
        return {"text": json.dumps({
            "action": "select", "selected_ids": ["synthetic-a"],
            "reason_codes": ["directly_relevant"], "confidence_band": "high",
        })}

    monkeypatch.setattr(llm, "complete_json", complete)
    binding = _binding()
    assert asyncio.run(runtime.run_structured_probe(binding, "protocol_probe")) is True
    assert "synthetic" in json.dumps(seen) and "user data" in json.dumps(seen)
    assert runtime.get_certification(binding, "protocol_probe") is runtime.CertificationLevel.STRUCTURED_CAPABLE


@pytest.mark.parametrize(
    ("role", "expected_timeout"),
    [(runtime.LogicalRole.FAST, 5.0), (runtime.LogicalRole.REASONING, 30.0),
     (runtime.LogicalRole.CREATIVE, 15.0)],
)
def test_structured_probe_timeout_is_role_specific(monkeypatch, role, expected_timeout):
    seen = []

    async def complete(_provider, _model, _messages, **kwargs):
        seen.append(kwargs["timeout_seconds"])
        raise llm.LLMError("synthetic failure")

    monkeypatch.setattr(llm, "complete_json", complete)
    assert asyncio.run(runtime.run_structured_probe(_binding(role), "protocol_probe")) is False
    assert seen == [expected_timeout]


def test_circuit_is_per_decision_kind_and_recovers_after_cooldown():
    binding = _binding()
    for _ in range(3):
        runtime.record_circuit_result(
            binding, "kind-a", success=False, error_code="timeout", now=100,
        )
    assert runtime.circuit_allows(binding, "kind-a", now=101) is False
    assert runtime.circuit_allows(binding, "kind-b", now=101) is True
    assert runtime.circuit_allows(binding, "kind-a", now=161) is True
    runtime.record_circuit_result(binding, "kind-a", success=True, now=162)
    assert runtime.circuit_allows(binding, "kind-a", now=163) is True


def test_governor_enforces_budget_environment_and_cancels_only_pending_background():
    governor = runtime.CognitionBudgetGovernor(runtime.BudgetPolicy(
        rolling_tokens=100, daily_tokens=100, local_concurrency=4, remote_concurrency=4,
    ))
    ok, error = governor.authorize(
        task_id="offline", decision_kind="diary", role=runtime.LogicalRole.FAST,
        location="remote", priority=runtime.TaskPriority.BACKGROUND, estimated_tokens=1,
        network_online=False,
    )
    assert ok is False and error == "cognition_network_offline"
    for task_id, kind in (("pending", "diary"), ("running", "pwm"), ("normal", "protocol_probe")):
        assert governor.authorize(
            task_id=task_id, decision_kind=kind, role=runtime.LogicalRole.FAST,
            location="local", priority=runtime.TaskPriority.BACKGROUND, estimated_tokens=10,
        )[0]
    governor.mark_started("running")
    assert governor.cancel_pending_for_user_message(now=200) == ["pending"]
    conn = db.connect()
    try:
        states = dict(conn.execute(
            "SELECT task_id,status FROM cognition_budget_events WHERE task_id IN ('pending','running','normal')"
        ).fetchall())
    finally:
        conn.close()
    assert states == {"pending": "cancelled", "running": "authorized", "normal": "authorized"}


def test_control_plane_recovery_releases_stale_authorizations_and_prunes_terminal_rows():
    governor = runtime.CognitionBudgetGovernor()
    assert governor.authorize(
        task_id="stale-auth", decision_kind="diary", role=runtime.LogicalRole.FAST,
        location="local", priority=runtime.TaskPriority.BACKGROUND, estimated_tokens=1, now=100,
    )[0]
    assert governor.authorize(
        task_id="old-terminal", decision_kind="diary", role=runtime.LogicalRole.FAST,
        location="local", priority=runtime.TaskPriority.BACKGROUND, estimated_tokens=1, now=100,
    )[0]
    governor.complete("old-terminal", now=101)
    result = runtime.recover_control_plane(
        now=200, stale_after_seconds=50, retention_seconds=50,
    )
    assert result == {"recovered": 1, "deleted": 1}


def test_provider_failure_returns_fallback_and_records_metrics(monkeypatch):
    header, payload, candidates, source = _request()
    binding = _binding()
    runtime._record_certification(  # noqa: SLF001
        binding, "protocol_probe", runtime.CertificationLevel.STRUCTURED_CAPABLE, None,
    )

    async def fail(*_args, **_kwargs):
        raise llm.LLMError("unavailable", code="provider_down")

    monkeypatch.setattr(llm, "complete_json", fail)
    result = asyncio.run(runtime.execute_registered_decision(
        header, payload, candidates, current_snapshot=source, role=runtime.LogicalRole.FAST,
    ))
    assert result["fallback_used"] is True and result["error_code"] == "provider_down"
    run = cds.run_ledger.get_run(result["run_id"])
    assert run.status == cds.run_ledger.RunStatus.APPLIED and run.logical_role == "fast"
    assert run.certification_level == "structured_capable"


def test_successful_execution_records_tokens_latency_and_never_applies_shadow(monkeypatch):
    header, payload, candidates, source = _request("success")
    binding = _binding()
    runtime._record_certification(  # noqa: SLF001
        binding, "protocol_probe", runtime.CertificationLevel.STRUCTURED_CAPABLE, None,
    )

    async def complete(*_args, **_kwargs):
        return {"text": json.dumps({
            "action": "select", "selected_ids": ["synthetic-a"],
            "reason_codes": ["directly_relevant"], "confidence_band": "high",
        }), "prompt_tokens": 11, "completion_tokens": 7, "latency_ms": 23}

    monkeypatch.setattr(llm, "complete_json", complete)
    result = asyncio.run(runtime.execute_registered_decision(
        header, payload, candidates, current_snapshot=source, role=runtime.LogicalRole.FAST,
    ))
    run = cds.run_ledger.get_run(result["run_id"])
    assert result["fallback_used"] is False and result["application_allowed"] is False
    assert (run.input_tokens, run.output_tokens, run.latency_ms) == (11, 7, 23)

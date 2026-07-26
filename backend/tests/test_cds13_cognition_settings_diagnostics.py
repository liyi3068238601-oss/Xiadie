"""CDS.13 settings, diagnostics and one-switch rollback boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app import cognitive_decision as cds
from app import cognition_diagnostics, cognition_runtime, cognition_settings, db, llm
from app import presence_thread_shadow as observer  # registers EAP-owned kind
from app.main import app
from app.proactive import decision_run_adapter, run_ledger

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def reset_settings():
    db.set_setting("cognition_control_config", "{}")
    db.set_setting("cognition_model_bindings", "{}")
    conn = db.connect()
    try:
        conn.execute("UPDATE providers SET enabled=1 WHERE id IN ('mock','deepseek')")
        conn.commit()
    finally:
        conn.close()
    yield
    db.set_setting("cognition_control_config", "{}")
    db.set_setting("cognition_model_bindings", "{}")


def _request():
    digest = hashlib.sha256(b"synthetic").hexdigest()
    source = (cds.SourceSnapshot("synthetic", "source", "1", digest),)
    candidates = (cds.CandidateRef("synthetic-a", "synthetic", digest),)
    payload = cds.ProtocolProbeInput(candidate_ids=("synthetic-a",))
    header = cds.build_header(
        decision_kind="protocol_probe", policy_version="v1", request_id="cds13-disabled",
        mode=cds.DecisionMode.SHADOW, source_snapshot=source,
    )
    return header, payload, candidates, source


def test_default_settings_expose_natural_capabilities_and_frozen_mode_ceilings():
    settings = cognition_settings.get_settings()
    assert settings["enabled"] is True
    assert settings["natural_capabilities"] == [
        "更稳妥地理解当前对话", "在需要时整理可用的回忆与资料", "从反馈中调整谨慎程度",
    ]
    assert settings["decision_modes"] == settings["mode_ceilings"]
    assert set(settings["decision_modes"]) == {
        item["decision_kind"] for item in cds.REGISTRY.public_snapshot()
    }
    assert set(settings["decision_modes"].values()) == {"shadow"}


def test_settings_reject_mode_escalation_and_unregistered_model():
    with pytest.raises(ValueError, match="ceiling"):
        cognition_settings.update_settings(decision_modes={"protocol_probe": "active"})
    with pytest.raises(ValueError, match="not registered"):
        cognition_settings.update_settings(model_bindings={
            "fast": {"provider_id": "deepseek", "model": "invented-model"},
        })


def test_role_binding_is_real_and_one_switch_rollback_disables_all_decisions():
    result = cognition_settings.update_settings(model_bindings={
        "reasoning": {"provider_id": "deepseek", "model": "deepseek-reasoner"},
    })
    assert result["model_bindings"]["reasoning"]["provider_id"] == "deepseek"
    following = cognition_settings.update_settings(model_bindings={"reasoning": None})
    assert "reasoning" not in following["model_bindings"]
    cognition_settings.update_settings(model_bindings={
        "reasoning": {"provider_id": "deepseek", "model": "deepseek-reasoner"},
    })
    rolled_back = cognition_settings.rollback_to_legacy()
    assert rolled_back["enabled"] is False
    assert set(rolled_back["decision_modes"].values()) == {"off"}
    assert rolled_back["model_bindings"] == {}


def test_disabled_decision_uses_fallback_without_calling_provider(monkeypatch):
    cognition_settings.rollback_to_legacy()
    called = False

    async def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not run while cognition is disabled")

    monkeypatch.setattr(llm, "complete_json", forbidden)
    header, payload, candidates, source = _request()
    result = asyncio.run(cognition_runtime.execute_registered_decision(
        header, payload, candidates, current_snapshot=source,
        role=cognition_runtime.LogicalRole.FAST,
    ))
    assert called is False
    assert result["fallback_used"] is True
    assert result["error_code"] == "cognition_decision_disabled"
    assert result["application_allowed"] is False


def test_diagnostics_v2_has_version_counts_latency_fallback_errors_and_no_body():
    run, _ = run_ledger.create_or_get_run(
        task_kind=observer.DECISION_KIND, protocol_version=cds.PROTOCOL_VERSION,
        policy_version="cds13", mode="shadow", source_type="message", source_id="m1",
        source_revision="r1", source_hash="a" * 64,
        source_snapshot=({"kind": "message", "id": "m1", "revision": "r1",
                          "content_hash": "a" * 64},),
        snapshot_hash="b" * 64, candidate_snapshot_hash="c" * 64,
        candidate_count=1, idempotency_key="cds13-diagnostic-run",
    )
    run_ledger.transition_run(run.id, run_ledger.RunStatus.RUNNING)
    run_ledger.transition_run(
        run.id, run_ledger.RunStatus.APPLIED, error_code="synthetic_error", latency_ms=37,
    )
    result = cognition_diagnostics.read()
    summary = next(item for item in result["summaries"]
                   if item["decision_kind"] == observer.DECISION_KIND)
    assert result["diagnostic_version"] == "cognition-diagnostics-v2"
    assert summary["run_count"] >= 1 and summary["latency_ms_max"] >= 37
    assert summary["error_codes"]["synthetic_error"] >= 1
    encoded = json.dumps(result, ensure_ascii=False)
    for forbidden in ("source_snapshot", "snapshot_hash", "candidate_snapshot_hash",
                      "prompt_template_hash", "selected_ids"):
        assert forbidden not in encoded
    assert result["privacy"]["raw_output_persisted"] is False

    v1 = decision_run_adapter.read_eap_decision_run(run.id)
    v2 = decision_run_adapter.read_eap_decision_run_v2(run.id)
    assert v1 is not None and v1["adapter_version"] == "eap-decision-run-adapter-v1"
    assert "error_code" not in v1 and "latency_ms" not in v1
    assert v2 is not None and v2["adapter_version"] == "eap-decision-run-diagnostic-v2"
    assert v2["error_code"] == "synthetic_error" and v2["latency_ms"] == 37


def test_settings_and_diagnostics_api_are_token_protected_and_body_free():
    assert TestClient(app).get("/api/cognition/settings").status_code == 401
    response = client.put("/api/cognition/settings", json={
        "enabled": True, "diagnostics_visible": True,
        "decision_modes": {"protocol_probe": "off"},
    })
    assert response.status_code == 200
    assert response.json()["decision_modes"]["protocol_probe"] == "off"
    diagnostics = client.get("/api/cognition/diagnostics/v2")
    assert diagnostics.status_code == 200
    assert diagnostics.json()["privacy"] == {
        "body_persisted": False, "prompt_persisted": False,
        "raw_output_persisted": False, "candidate_ids_exposed": False,
    }

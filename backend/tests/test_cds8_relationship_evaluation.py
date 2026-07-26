from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from app.proactive import cognition, protocols, relationship

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cds8_relationship_meaning_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-8-relationship-meaning-evaluation.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "cds-8-relationship-meaning-evaluation.md"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cds8_relationship_fixture.py"
RUNNER_PATH = BACKEND_DIR / "scripts" / "run_cds8_relationship_evaluation.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_is_deterministic_synthetic_and_covers_required_scenes():
    fixture = _fixture()
    assert fixture == runpy.run_path(str(GENERATOR_PATH))["build_fixture"]()
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 120
    assert {case["group"] for case in fixture["cases"]} == {
        "ordinary", "appreciation", "reliable_help", "success", "vulnerable",
        "boundary_respected", "boundary_repair", "reunion", "conflict", "silence",
    }
    message_cases = [case for case in fixture["cases"] if case["group"] != "silence"]
    silence_cases = [case for case in fixture["cases"] if case["group"] == "silence"]
    assert {case["expected_label"] for case in message_cases} == set(relationship.ALL_LABELS)
    assert all(0 < len(case["user_text"]) <= 160 for case in message_cases)
    assert all(0 < len(case["assistant_text"]) <= 160 for case in message_cases)
    assert all("user_text" not in case and "assistant_text" not in case for case in silence_cases)
    assert all(len({case["variant"] for case in fixture["cases"] if case["group"] == group}) >= 4
               for group in {case["group"] for case in fixture["cases"]})


@pytest.mark.parametrize("case", [case for case in _fixture()["cases"] if case["group"] != "silence"])
def test_deterministic_structured_substitute_passes_frozen_schema(case):
    parsed = cognition.parse_and_validate(
        case["structured_output"],
        user_text=case["user_text"],
        assistant_text=case["assistant_text"],
    )
    assert parsed["relationship_meaning"]["label"] == case["expected_label"]
    assert parsed["relationship_meaning"]["protocol_version"] == protocols.RELATIONSHIP_MEANING_V1


@pytest.mark.parametrize("case", [case for case in _fixture()["cases"] if case["group"] != "silence"])
def test_all_relationship_labels_reject_tampered_evidence(case):
    runner = runpy.run_path(str(RUNNER_PATH))
    assert runner["_evidence_is_enforced"](case) is True


def test_report_runs_existing_schema_decision_run_and_eap_application_chain():
    runner = runpy.run_path(str(RUNNER_PATH))
    report = runner["build_report"](_fixture())
    repeated = runner["build_report"](_fixture())
    assert repeated == report
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["sample_count"] == 120
    assert report["schema_validation_rate"] == 1.0
    assert report["decision_run_terminal_rate"] == 1.0
    assert report["eap_suggestion_application_rate"] == 1.0
    assert report["enqueue_worker_application_rate"] == 1.0
    assert report["provider_boundary_call_rate"] == 1.0
    assert report["terminal_invariant_rate"] == 1.0
    assert report["protocol_unchanged"] is True
    assert report["schema_changed"] is False


def test_report_passes_idempotency_clamp_silence_and_completion_gates():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    runner = runpy.run_path(str(RUNNER_PATH))
    dynamic = runner["build_report"](_fixture())
    assert report == dynamic
    assert MARKDOWN_PATH.read_text(encoding="utf-8") == runner["render_markdown"](dynamic)
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["completion_gates"] == {
        "ordinary_question_bond_growth_rate": 0.0,
        "silence_relationship_decline_rate": 0.0,
        "single_turn_over_cap_rate": 0.0,
    }
    assert report["completion_gate_counts"] == {
        "ordinary_question_bond_growth": {"hits": 0, "denominator": 12},
        "silence_relationship_decline": {"hits": 0, "denominator": 12},
        "single_turn_over_cap": {"hits": 0, "denominator": 108},
    }
    assert report["idempotency_reuse_rate"] == 1.0
    assert report["duplicate_application_change_rate"] == 0.0
    assert report["evidence_validation_rate"] == 1.0
    assert report["label_exact_rate"] == 1.0
    assert len(report["outcomes"]) == 120
    message_outcomes = [row for row in report["outcomes"] if row["group"] != "silence"]
    silence_outcomes = [row for row in report["outcomes"] if row["group"] == "silence"]
    assert all(row["actual_applied_within_caps"] for row in message_outcomes)
    assert all(set(row["actual_applied"]) == {"bond", "trust"} for row in message_outcomes)
    assert all(row["no_messages_created"] for row in silence_outcomes)
    assert all(row["label"] is None and row["actual_applied"] is None for row in silence_outcomes)
    assert all(
        row[key] is None
        for row in silence_outcomes
        for key in (
            "label_exact", "schema_valid", "decision_run_terminal", "eap_suggestion_applied",
            "enqueue_worker_applied", "provider_boundary_called", "terminal_invariant",
            "idempotency_reused", "duplicate_application_unchanged", "evidence_enforced",
            "within_single_turn_caps", "actual_applied_within_caps", "bond_grew",
        )
    )
    assert report["all_completion_gates_passed"] is True
    encoded = json.dumps(report, ensure_ascii=False)
    assert "user_text" not in encoded
    assert "assistant_text" not in encoded
    assert "structured_output" not in encoded
    assert "raw_model_output" not in encoded


def test_frozen_relationship_contract_and_caps_are_not_redefined():
    definition = protocols.get_protocol(protocols.RELATIONSHIP_MEANING_V1)
    runner = runpy.run_path(str(RUNNER_PATH))
    assert definition.status is protocols.ProtocolStatus.FROZEN
    assert runner["RELATIONSHIP_PROTOCOL"] == protocols.RELATIONSHIP_MEANING_V1
    assert runner["SINGLE_TURN_CAPS"] is relationship.SINGLE_TURN_CAPS


def test_runner_import_has_no_temporary_directory_side_effect(monkeypatch):
    created = []
    monkeypatch.setattr("tempfile.mkdtemp", lambda *args, **kwargs: created.append((args, kwargs)))
    runpy.run_path(str(RUNNER_PATH))
    assert created == []


def test_report_restores_database_paths_and_removes_workspace_on_success(monkeypatch, tmp_path):
    runner = runpy.run_path(str(RUNNER_PATH))
    original_data_dir = runner["db"].DATA_DIR
    original_db_path = runner["db"].DB_PATH
    workspace = tmp_path / "evaluation"
    monkeypatch.setattr(runner["tempfile"], "mkdtemp", lambda **_kwargs: str(workspace))
    report = runner["build_report"](_fixture())
    assert report["sample_count"] == 120
    assert runner["db"].DATA_DIR == original_data_dir
    assert runner["db"].DB_PATH == original_db_path
    assert not workspace.exists()


def test_report_restores_database_paths_and_removes_workspace_on_failure(monkeypatch, tmp_path):
    runner = runpy.run_path(str(RUNNER_PATH))
    original_data_dir = runner["db"].DATA_DIR
    original_db_path = runner["db"].DB_PATH
    workspace = tmp_path / "evaluation"
    monkeypatch.setattr(runner["tempfile"], "mkdtemp", lambda **_kwargs: str(workspace))

    def fail_init():
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "partial.db").write_bytes(b"partial")
        raise RuntimeError("init failed")

    monkeypatch.setattr(runner["db"], "init_db", fail_init)
    with pytest.raises(RuntimeError, match="init failed"):
        runner["build_report"](_fixture())
    assert runner["db"].DATA_DIR == original_data_dir
    assert runner["db"].DB_PATH == original_db_path
    assert not workspace.exists()

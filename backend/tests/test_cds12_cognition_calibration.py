"""CDS.12 feedback calibration is bounded, idempotent and Shadow-only."""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import cognition_calibration as calibration
from app import db, specialty_contracts
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def clean_calibration():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM cognition_calibration_events")
        conn.execute("DELETE FROM cognition_feedback_signals")
        conn.execute("DELETE FROM cognition_calibration_profiles")
        conn.commit()
    finally:
        conn.close()
    yield


def test_migration_63_adds_only_body_free_calibration_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE decision_runs(id TEXT PRIMARY KEY)")
    migration = next(sql for version, sql in db.MIGRATIONS if version == 63)
    conn.executescript(migration)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {
        "cognition_calibration_profiles", "cognition_feedback_signals",
        "cognition_calibration_events",
    } <= tables
    columns = {
        row["name"] for table in tables if table.startswith("cognition_calibration")
        or table == "cognition_feedback_signals"
        for row in conn.execute(f"PRAGMA table_info({table})")
    }
    assert not ({"body", "prompt", "raw_output", "user_text"} & columns)
    conn.close()


def test_feedback_enums_are_scoped_to_their_decision_kind():
    proactive = calibration.submit_feedback(
        decision_kind="presence_thread_observer", feedback_kind="quick_reply",
        source_run_id=None, request_nonce="quick-1",
    )
    assert proactive["profile"]["domain"] == "proactive"
    assert proactive["profile"]["parameters"] == {
        "caution_bias": 0.0, "selection_bias": 0.02,
    }
    with pytest.raises(ValueError, match="not valid"):
        calibration.submit_feedback(
            decision_kind="recall_planner", feedback_kind="quick_reply",
            source_run_id=None, request_nonce="wrong-domain",
        )


def test_quick_later_unanswered_rejected_and_corrected_are_distinct_and_bounded():
    expected_caution = {
        "quick_reply": 0.0, "later_reply": 0.02, "unanswered": 0.05,
        "rejected": 0.08, "corrected": 0.04,
    }
    for index, (kind, caution) in enumerate(expected_caution.items()):
        calibration.rollback_profile(
            decision_kind="presence_thread_observer", request_nonce=f"reset-{index}",
        )
        result = calibration.submit_feedback(
            decision_kind="presence_thread_observer", feedback_kind=kind,
            source_run_id=None, request_nonce=f"signal-{index}",
        )
        assert result["profile"]["parameters"]["caution_bias"] == caution
    for index in range(20):
        calibration.submit_feedback(
            decision_kind="presence_thread_observer", feedback_kind="rejected",
            source_run_id=None, request_nonce=f"bounded-{index}",
        )
    profile = calibration.get_profile("presence_thread_observer")
    assert profile["parameters"] == {"caution_bias": 0.4, "selection_bias": -0.2}


def test_same_feedback_is_concurrently_idempotent():
    def submit(_index: int):
        return calibration.submit_feedback(
            decision_kind="memory_conflict_proposal", feedback_kind="corrected",
            source_run_id=None, request_nonce="same-request",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(24)))
    assert sum(result["created"] for result in results) == 1
    profile = calibration.get_profile("memory_conflict_proposal")
    assert profile["revision"] == 1 and profile["feedback_count"] == 1


def test_feedback_changes_only_allowlisted_preferences_and_never_hard_boundaries():
    before = set(calibration.IMMUTABLE_BOUNDARIES)
    result = calibration.submit_feedback(
        decision_kind="recall_planner", feedback_kind="wrong_source",
        source_run_id=None, request_nonce="recall-wrong-source",
    )
    assert set(result["profile"]["parameters"]) == calibration.ADJUSTABLE_PARAMS
    assert set(calibration.IMMUTABLE_BOUNDARIES) == before
    assert not (calibration.ADJUSTABLE_PARAMS & calibration.IMMUTABLE_BOUNDARIES)


def test_rollback_is_per_decision_kind_and_idempotent():
    for decision_kind in ("recall_planner", "memory_retention_proposal"):
        calibration.submit_feedback(
            decision_kind=decision_kind, feedback_kind="helpful",
            source_run_id=None, request_nonce=f"feedback-{decision_kind}",
        )
    first = calibration.rollback_profile(
        decision_kind="recall_planner", request_nonce="rollback-recall",
    )
    second = calibration.rollback_profile(
        decision_kind="recall_planner", request_nonce="rollback-recall",
    )
    assert first["rolled_back"] is True and second["rolled_back"] is False
    assert first["profile"]["parameters"] == calibration.DEFAULT_PARAMETERS
    assert calibration.get_profile("memory_retention_proposal")["feedback_count"] == 1


def test_api_diagnostics_are_body_free_and_do_not_expose_parameter_event_json():
    response = client.post("/api/cognition/feedback", json={
        "decision_kind": "companion_cognition", "feedback_kind": "corrected",
        "source_run_id": None, "request_nonce": "api-feedback",
    })
    assert response.status_code == 200
    diagnostics = client.get("/api/cognition/calibration").json()
    encoded = json.dumps(diagnostics, ensure_ascii=False)
    assert "parameter_delta_json" not in encoded and "changes_json" not in encoded
    assert set(diagnostics["immutable_boundaries"]) == calibration.IMMUTABLE_BOUNDARIES


def test_review_observation_selected_ids_requires_nonempty_string_members():
    result: specialty_contracts.DecisionResult = {
        "protocol_version": "cognitive-decision-v1", "run_id": "run",
        "decision_kind": "future", "mode": "shadow", "action": "select",
        "selected_ids": (1,), "reason_codes": ("relevant",),  # type: ignore[typeddict-item]
        "confidence_band": "high", "fallback_used": False,
        "application_allowed": False, "source_snapshot_hash": "a" * 64,
    }
    with pytest.raises(ValueError, match="collections"):
        specialty_contracts.validate_decision_result(
            result, candidate_ids=(1,), source_snapshot_hash="a" * 64,  # type: ignore[arg-type]
        )

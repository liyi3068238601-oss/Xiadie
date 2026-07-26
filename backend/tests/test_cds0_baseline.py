"""CDS.0 versioned synthetic corpus and legacy baseline evidence."""

from __future__ import annotations

import hashlib
import json
import runpy
from collections import Counter
from pathlib import Path

from app import archivist, context_assembler, context_budget, history_recall, knowledge_recall
from app.proactive import cognition, protocols

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cds0_evaluation_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-0-legacy-baseline.json"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cds0_evaluation_fixture.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_cds0_fixture_is_synthetic_complete_balanced_and_deterministic():
    fixture = _fixture()
    assert fixture["protocol_version"] == "cognitive-decision-eval-v1"
    assert fixture["synthetic_only"] is True
    assert fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 300
    assert Counter(case["track"] for case in fixture["cases"]) == {
        "presence": 50,
        "relationship_fallback": 50,
        "knowledge_gate": 50,
        "history_intent": 50,
        "context_fixed_budget": 50,
        "memory_retention": 50,
    }
    assert len({case["id"] for case in fixture["cases"]}) == 300
    generated = runpy.run_path(str(GENERATOR_PATH))["build_fixture"]()
    assert generated == fixture


def test_cds0_cases_have_disjoint_bounded_selection_labels():
    for case in _fixture()["cases"]:
        candidates = case["candidates"]
        labels = case["expected"]
        must = set(labels["must_select"])
        may = set(labels["may_select"])
        forbidden = set(labels["forbidden_select"])
        assert len(candidates) == len(set(candidates))
        assert not (must & may or must & forbidden or may & forbidden), case["id"]
        assert must | may | forbidden <= set(candidates), case["id"]
        assert must or forbidden, case["id"]


def test_cds0_report_is_bound_to_merge_schema_fixture_and_current_versions():
    report = _report()
    baseline = report["construction_baseline"]
    assert report["report_version"] == "cognitive-decision-baseline-report-v1"
    assert report["synthetic_only"] is True and report["contains_user_data"] is False
    assert baseline == {
        "repository": "liyi3068238601-oss/Xiadie",
        "predecessor_pr": 1,
        "base_branch": "main",
        "base_commit_sha": "6b8aa47134f8a9a55131c73bb1148e6912421c4f",
        "schema_version": 60,
        "test_baseline": "937 passed, 1 warning",
        "plan_version": "CDS v0.3",
        "recorded_at": "2026-07-22",
    }
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["algorithm_versions"] == {
        "presence": protocols.CONVERSATION_PRESENCE_V2,
        "relationship_fallback": cognition.PROTOCOL_VERSION,
        "knowledge_gate": knowledge_recall.PROTOCOL_VERSION,
        "knowledge_threshold": knowledge_recall.knowledge_recall_thresholds.THRESHOLD_VERSION,
        "history_index": history_recall.INDEX_VERSION,
        "history_score": history_recall.SCORE_VERSION,
        "context_package": context_assembler.PACKAGE_PROTOCOL_VERSION,
        "context_budget": context_budget.BUDGET_PROTOCOL_VERSION,
        "context_estimator": context_budget.ESTIMATOR_VERSION,
        "memory_retention": archivist.RETENTION_POLICY_VERSION,
    }


def test_cds0_report_covers_every_case_without_fixture_text_or_model_output():
    fixture = _fixture()
    report = _report()
    outcomes = report["outcomes"]
    assert report["overall"]["sample_count"] == len(outcomes) == 300
    assert {row["case_id"] for row in outcomes} == {case["id"] for case in fixture["cases"]}
    assert set(report["tracks"]) == set(fixture["tracks"])
    assert all(metrics["sample_count"] == 50 for metrics in report["tracks"].values())
    assert all("input" not in row and "raw_model_output" not in row for row in outcomes)
    assert report["overall"]["false_positive_selections"] >= 0
    assert report["overall"]["false_negative_selections"] >= 0
    assert report["overall"]["latency_ms"]["p95"] >= 0
    assert report["overall"]["estimated_tokens"]["total"] >= 0

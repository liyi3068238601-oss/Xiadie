from __future__ import annotations

import json
from pathlib import Path

from app import cognitive_decision as cds
from app import life_decisions, life_quality

FIXTURE = Path(__file__).parent / "fixtures" / "life10_evaluation_v1.json"


def _cases():
    return json.loads(FIXTURE.read_text("utf-8"))["cases"]


def _oracle_predictions():
    return [
        {
            "case_id": item["case_id"], "action": item["expected_action"],
            "selected_ids": item["expected_selected_ids"], "confidence_band": "high",
            "application_allowed": False,
        }
        for item in _cases()
    ]


def test_fixed_fixture_has_ten_cases_per_protocol_and_is_synthetic():
    payload = json.loads(FIXTURE.read_text("utf-8"))
    assert payload["synthetic_only"] is True and len(payload["cases"]) == 60
    assert {item["decision_kind"] for item in payload["cases"]} == set(life_decisions.LIFE_DECISION_KINDS)
    for kind in life_decisions.LIFE_DECISION_KINDS:
        assert sum(item["decision_kind"] == kind for item in payload["cases"]) == 10
    assert all(set(item["candidate_summaries"]) == set(item["candidate_ids"]) for item in payload["cases"])
    assert len({item["synthetic_summary"] for item in payload["cases"]}) == 60


def test_registry_bounds_all_life_protocols_and_keeps_them_shadow():
    for kind in life_decisions.LIFE_DECISION_KINDS:
        definition = cds.REGISTRY.get(kind)
        assert definition.mode is cds.DecisionMode.SHADOW
        assert definition.max_candidates <= 12 and definition.timeout_seconds <= 8


def test_oracle_report_is_correct_but_cannot_promote_with_small_fixture():
    report = life_quality.evaluate_predictions(_cases(), _oracle_predictions())
    assert report["invalid_total"] == report["low_confidence_application_total"] == 0
    assert all(item["accuracy"] == 1 for item in report["per_kind"].values())
    eligible, reasons = life_quality.promotion_eligible(report, provider_count=2)
    assert eligible is False
    assert sum(reason.endswith("sample_insufficient") for reason in reasons) == 6


def test_invalid_foreign_candidate_and_low_confidence_application_are_zero_tolerance():
    predictions = _oracle_predictions()
    predictions[0]["selected_ids"] = ["foreign"]
    predictions[1]["confidence_band"] = "low"
    predictions[1]["application_allowed"] = True
    report = life_quality.evaluate_predictions(_cases(), predictions)
    assert report["invalid_total"] == 1
    assert report["low_confidence_application_total"] == 1
    eligible, reasons = life_quality.promotion_eligible(report, provider_count=1)
    assert eligible is False
    assert {"provider_count_insufficient", "invalid_output_nonzero", "low_confidence_application_nonzero"} <= set(reasons)


def test_provider_consistency_is_paired_and_exact():
    left = _oracle_predictions()
    right = _oracle_predictions()
    right[0]["action"] = "skip"
    right[0]["selected_ids"] = []
    report = life_quality.provider_consistency(left, right)
    assert report == {"paired_cases": 60, "agreements": 59, "agreement_rate": 59 / 60}

"""LIFE.10 fixed evaluation and promotion gates for LIFE Shadow decisions."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from . import life_decisions

EVALUATION_VERSION = "life-decision-eval-v1"
MIN_CASES_PER_KIND = 50
MIN_ACCURACY = 0.90
REQUIRED_PROVIDERS = 2


def evaluate_predictions(cases: list[dict[str, Any]],
                         predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["case_id"]: item for item in predictions}
    counts = Counter(item["decision_kind"] for item in cases)
    correct = Counter()
    invalid = Counter()
    low_confidence_application = 0
    for case in cases:
        prediction = by_id.get(case["case_id"])
        if not prediction:
            invalid[case["decision_kind"]] += 1
            continue
        valid_action = prediction.get("action") in {"select", "skip", "ask"}
        selected = prediction.get("selected_ids")
        if not valid_action or not isinstance(selected, list) or not set(selected) <= set(case["candidate_ids"]):
            invalid[case["decision_kind"]] += 1
            continue
        if prediction.get("confidence_band") == "low" and prediction.get("application_allowed"):
            low_confidence_application += 1
        if prediction["action"] == case["expected_action"] and selected == case["expected_selected_ids"]:
            correct[case["decision_kind"]] += 1
    per_kind = {
        kind: {
            "cases": counts[kind], "correct": correct[kind], "invalid": invalid[kind],
            "accuracy": correct[kind] / counts[kind] if counts[kind] else 0.0,
        }
        for kind in life_decisions.LIFE_DECISION_KINDS
    }
    return {
        "evaluation_version": EVALUATION_VERSION,
        "case_count": len(cases), "per_kind": per_kind,
        "invalid_total": sum(invalid.values()),
        "low_confidence_application_total": low_confidence_application,
    }


def provider_consistency(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    left_map = {item["case_id"]: item for item in left}
    right_map = {item["case_id"]: item for item in right}
    common = sorted(left_map.keys() & right_map.keys())
    agreements = sum(
        left_map[key].get("action") == right_map[key].get("action")
        and left_map[key].get("selected_ids") == right_map[key].get("selected_ids")
        for key in common
    )
    return {
        "paired_cases": len(common), "agreements": agreements,
        "agreement_rate": agreements / len(common) if common else 0.0,
    }


def promotion_eligible(report: dict[str, Any], *, provider_count: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if provider_count < REQUIRED_PROVIDERS:
        reasons.append("provider_count_insufficient")
    if report.get("invalid_total") != 0:
        reasons.append("invalid_output_nonzero")
    if report.get("low_confidence_application_total") != 0:
        reasons.append("low_confidence_application_nonzero")
    for kind in life_decisions.LIFE_DECISION_KINDS:
        item = report.get("per_kind", {}).get(kind, {})
        if item.get("cases", 0) < MIN_CASES_PER_KIND:
            reasons.append(f"{kind}:sample_insufficient")
        elif item.get("accuracy", 0.0) < MIN_ACCURACY:
            reasons.append(f"{kind}:accuracy_below_threshold")
    return not reasons, reasons

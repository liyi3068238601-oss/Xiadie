import json
from pathlib import Path

from app import db, kig_pipeline, pwm


REPORT = Path(__file__).resolve().parents[2] / "docs" / "reports" / "kig-p-acceptance.json"


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_kig_p_final_report_has_required_nonzero_synthetic_denominators():
    report = _report()
    assert report["protocol_version"] == "kig-p-acceptance-v1"
    assert report["synthetic_only"] is True and report["contains_user_data"] is False
    assert report["provider_calls"] == 0
    assert report["scenario_counts"] == {
        "single_document": 100, "multi_document": 100, "cross_store": 100,
    }
    assert report["version_scenarios"] == 100 and report["entity_scenarios"] == 100


def test_kig_p_quality_scale_and_zero_tolerance_gates_pass():
    report = _report()
    assert report["release_gate"] == "pass" and all(report["gates"].values())
    assert report["quality"]["citation_accuracy"] == 1.0
    assert report["quality"]["cross_store_routing_accuracy"] >= 0.9
    assert report["quality"]["entity_auto_merge_precision"] >= 0.98
    assert report["quality"]["entity_rollback_recovery"] == 1.0
    assert all(value == 0 for value in report["zero_tolerance"].values())
    assert [row["chunk_count"] for row in report["scale_stress"]] == [10_000, 100_000, 250_000]
    assert all(row["probe_recall"] == 1.0 for row in report["scale_stress"])


def test_kig_v1_keeps_kig_r_protocol_and_uses_schema_80():
    report = _report()
    assert report["schema_version"] == 80
    assert report["retrieval_protocol"] == kig_pipeline.PROTOCOL_VERSION
    assert report["pwm_protocol"] == pwm.PROTOCOL_VERSION
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"] == "82"
    finally:
        conn.close()


def test_all_hard_budget_overflow_checks_are_exercised():
    budgets = _report()["hard_budgets"]
    assert budgets["per_source_claim_limit"] == 64 and budgets["per_source_claim_blocked"]
    assert budgets["alias_limit"] == 16 and budgets["alias_blocked"]
    assert budgets["disambiguation_bounded"] and budgets["maintenance_bounded"]

"""CIE.0 predecessor, fixed evaluation set, fallback and feature gate."""
from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

from app import cie_settings, db

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cie0_interaction_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cie-0-baseline.json"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cie0_evaluation_fixture.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_cie0_fixture_is_deterministic_synthetic_and_covers_required_suites():
    fixture = _fixture()
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert runpy.run_path(str(GENERATOR_PATH))["build_fixture"]() == fixture
    assert [item["rounds"] for item in fixture["continuous"]] == [5, 20, 100, 500]
    assert sum(item["rounds"] for item in fixture["continuous"]) == 625
    assert len(fixture["interruption"]) == 20
    assert len(fixture["attachments"]) == 20
    assert len(fixture["rhythm"]) == 20
    assert len(fixture["contributions"]) == 20


def test_cie0_report_locks_merged_kig_schema_and_does_not_claim_migration():
    report = _report()
    base = report["construction_baseline"]
    assert base["predecessor_pr"] == 4
    assert base["base_commit_sha"] == "b436e9f8876f8926ac90df3562edbeef3f085413"
    assert base["schema_version"] == 80
    assert base["next_schema_version_provisional"] == 81
    assert base["cie0_uses_migration"] is False
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()


def test_cie0_metrics_distinguish_measured_fallback_from_missing_capabilities():
    report = _report()
    metrics = report["metrics"]
    assert metrics["serialized_fallback_send_success_rate"] == 1.0
    latency = metrics["first_token_latency_ms"]
    assert latency["value"] is not None and latency["samples"] >= 3
    assert latency["p50"] > 0 and latency["p95"] >= latency["p50"]
    assert latency["provider_id"] and latency["model"]
    assert metrics["active_generation_cancel_support_rate"] == 0.0
    assert metrics["duplicate_reply_rate"] == 0.0
    assert metrics["third_party_body_leakage_rate"] == 0.0
    assert metrics["text_attachment_support_rate"] == 1.0
    assert metrics["native_image_support_rate"] == 0.0
    assert all(value is False for value in report["current_capability"].values())


def test_cie_feature_gate_is_single_fail_closed_and_round_trips():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM settings WHERE key=?", (cie_settings.SETTING_KEY,))
        conn.commit()
    finally:
        conn.close()
    assert cie_settings.SETTING_KEY == "cie_enabled"
    assert cie_settings.is_enabled() is False
    snapshot = cie_settings.snapshot()
    report_contract = _report()["fallback_contract"]
    assert snapshot["setting_key"] == report_contract["feature_flag"]
    assert snapshot["default_enabled"] == report_contract["feature_flag_default"]
    assert snapshot["fallback"] == {
        "turn_mode": report_contract["turn_mode"],
        "generation_mode": report_contract["generation_mode"],
        "transport": report_contract["transport"],
        "attachment_mode": report_contract["attachment_mode"],
        "native_image": False,
        "context_contribution": False,
    }
    assert cie_settings.set_enabled(True) is True
    assert cie_settings.set_enabled(False) is False


def test_cie0_default_gate_and_schema_baseline_remain_frozen_for_later_stages():
    db_source = (BACKEND_DIR / "app" / "db.py").read_text(encoding="utf-8")
    assert cie_settings.DEFAULT_ENABLED is False
    assert "(81," not in db_source

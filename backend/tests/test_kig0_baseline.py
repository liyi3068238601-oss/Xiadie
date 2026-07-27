"""KIG.0 immutable predecessor, capability ownership and 60-case baseline."""
from __future__ import annotations

import hashlib
import json
import runpy
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kig0_evaluation_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "kig-0-baseline.json"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_kig0_evaluation_fixture.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_kig0_fixture_has_exactly_twenty_cases_per_required_category():
    fixture = _fixture()
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert len(fixture["cases"]) == 60 and len(fixture["documents"]) == 80
    assert Counter(item["category"] for item in fixture["cases"]) == {
        "single_document": 20, "multi_document": 20, "knowledge_memory": 20,
    }
    assert runpy.run_path(str(GENERATOR_PATH))["build_fixture"]() == fixture


def test_kig0_report_locks_merged_life_schema_and_next_migration():
    report = _report()
    base = report["construction_baseline"]
    assert base["predecessor_pr"] == 3
    assert base["base_commit_sha"] == "f16d80ab0d2457065dc65d7d284d3cbf3584f5ee"
    assert base["schema_version"] == 71 and base["next_schema_version"] == 72
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["kig_tables_at_baseline"] == []


def test_kig0_current_retrieval_and_citation_metrics_are_measured_not_assumed():
    report = _report()
    assert report["metrics"]["knowledge_recall_rate"] == 1.0
    assert report["metrics"]["cross_memory_recall_rate"] == 1.0
    assert report["metrics"]["citation_allowlist_accuracy"] == 1.0
    assert report["metrics"]["unified_cross_source_evidence_rate"] == 0.0
    assert len(report["outcomes"]) == 60


def test_kig0_capability_matrix_preserves_existing_owners_and_records_gaps():
    report = _report()
    matrix = {item["capability"]: (item["state"], item["owner"]) for item in report["capability_matrix"]}
    assert matrix["knowledge_import_parse_chunk"] == ("x", "Knowledge")
    assert matrix["context_hard_budget"] == ("arrow", "CTX")
    assert matrix["fragment_episode_saga"] == ("arrow", "MEM")
    assert matrix["life_authoritative_ledger"] == ("arrow", "LIFE")
    assert matrix["unified_source_ref_registry"] == ("missing", "KIG")
    assert matrix["pwm_projection"] == ("missing", "KIG")
    assert matrix["web_result_live_adapter"] == ("not_applicable", "Future ToolRegistry")
    assert report["responsibility_conflicts"] == []
    for item in report["capability_matrix"]:
        if item["state"] == "partial":
            assert all(item[key] for key in ("existing", "gap", "minimal_delta", "rollback"))


def test_kig0_report_is_synthetic_body_free_and_uses_no_provider():
    report = _report()
    assert report["privacy"] == {
        "synthetic_only": True, "contains_user_data": False, "provider_calls": 0,
    }
    encoded = json.dumps(report, ensure_ascii=False)
    assert "raw_model_output" not in encoded and "api_key" not in encoded


def test_kig0_plan_and_life_freeze_have_no_unchecked_completion_items():
    kig_plan = (PROJECT_DIR / "docs" / "XIADIE_KNOWLEDGE_INTELLIGENCE_GOVERNANCE_AND_WORLD_MODEL_PLAN.md").read_text(encoding="utf-8")
    kig0 = kig_plan.split("### KIG.0：", 1)[1].split("### KIG.1：", 1)[0]
    assert "- [ ]" not in kig0
    life_plan = (PROJECT_DIR / "docs" / "LLM_DECISION_AND_LIFE_CONTINUITY_PLAN.md").read_text(encoding="utf-8")
    life_stages = life_plan.split("### LIFE.0：", 1)[1].split("## 11.", 1)[0]
    assert "- [ ]" not in life_stages

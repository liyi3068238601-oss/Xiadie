"""LIFE.0 immutable baseline, ownership and synthetic corpus evidence."""

from __future__ import annotations

import hashlib
import json
import math
import runpy
from collections import Counter
from pathlib import Path

from app import cognitive_decision as cds, db, specialty_contracts
from app.affect import engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "life0_evaluation_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "life-0-baseline.json"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_life0_evaluation_fixture.py"
RUNNER_PATH = BACKEND_DIR / "scripts" / "run_life0_baseline.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_life0_fixture_is_exactly_sixty_balanced_synthetic_cases():
    fixture = _fixture()
    assert fixture["protocol_version"] == "life-continuity-eval-v1"
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 60
    assert Counter(case["category"] for case in fixture["cases"]) == {
        "offline": 15, "important_date": 15, "diary": 15, "decision": 15,
    }
    assert len({case["id"] for case in fixture["cases"]}) == 60
    assert runpy.run_path(str(GENERATOR_PATH))["build_fixture"]() == fixture


def test_life0_fixture_freezes_safety_invariants_without_user_data():
    required = {
        "offline": {"no_provider_while_closed", "no_tool_claim_without_tool_run", "interval_idempotent"},
        "important_date": {"ambiguous_date_never_activates", "explicit_refusal_blocks_proactive"},
        "diary": {"every_user_fact_traceable", "forbidden_content_filtered", "private_not_auto_shared"},
        "decision": {"llm_proposes_program_decides", "source_rechecked_before_apply"},
    }
    for case in _fixture()["cases"]:
        assert case["expected"]["must_fail_closed"] is True
        assert required[case["category"]] <= set(case["expected"]["invariants"])


def test_life0_report_binds_merged_cds_schema_protocols_and_fixture():
    runner = runpy.run_path(str(RUNNER_PATH))
    dynamic = runner["build_report"](_fixture())
    report = _report()
    assert report == dynamic
    baseline = report["construction_baseline"]
    assert baseline["predecessor_pr"] == 2
    assert baseline["base_commit_sha"] == "0d7a2d08dc07f123d016da26da117fa58f9a48a1"
    assert baseline["schema_version"] == 63
    assert baseline["test_baseline"]["backend"] == "2304 passed, 1 warning"
    assert cds.PROTOCOL_VERSION in baseline["frozen_protocols"]
    assert cds.REGISTRY_VERSION in baseline["frozen_protocols"]
    assert specialty_contracts.CONTRACT_VERSION in baseline["frozen_protocols"]
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()


def test_life0_affect_baseline_is_deterministic_finite_and_relationship_stable():
    rows = _report()["affect_relationship_baseline"]
    assert [row["hours"] for row in rows] == [1, 8, 24, 72, 168]
    assert all(math.isfinite(value) for row in rows for key, value in row.items() if key != "hours")
    assert all(0 <= row["contact_need"] <= 1 for row in rows)
    assert all(row["bond"] == engine.DEFAULT_RELATIONSHIP["bond"] for row in rows)
    assert all(row["trust"] == engine.DEFAULT_RELATIONSHIP["trust"] for row in rows)


def test_life0_has_no_life_domain_tables_or_parallel_owner_yet():
    conn = db.connect()
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        conn.close()
    assert "life_proactive_seeds" in tables
    assert not ({
        "life_events", "life_clock", "life_self_state", "daily_schedules",
        "personal_goals", "important_dates", "diary_entries", "continuity_threads",
        "self_timeline_entries",
    } & tables)
    assert _report()["scenario_coverage"] == {
        "implemented": 0, "partial_adjacent_guards": 15, "missing_life_domain": 45,
    }


def test_life0_report_is_body_free_and_does_not_claim_reference_code():
    encoded = json.dumps(_report(), ensure_ascii=False)
    assert '"input"' not in encoded
    assert '"raw_model_output":' not in encoded
    assert _report()["privacy"]["raw_model_output_persisted"] is False
    assert _report()["privacy"]["contains_user_data"] is False

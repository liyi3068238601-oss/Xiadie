"""CDS.4 RecallPlanner remains bounded, synthetic and Shadow-only."""
from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from app import cognitive_decision as cds
from app import db, recall_planner_shadow as planner

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cds4_recall_planner_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-4-recall-shadow.json"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cds4_recall_fixture.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _payload(text: str, *, message_id: str = "m-synthetic") -> planner.RecallPlannerInput:
    return planner.RecallPlannerInput(
        candidate_ids=planner.candidate_ids(), source_message_id=message_id,
        valid_message_ids=(message_id,), text=text,
        forbidden_sources=planner.detect_forbidden_sources(text),
        legacy_selected_sources=("memory",),
    )


def test_fixture_is_deterministic_synthetic_and_has_600_balanced_turns():
    fixture = _fixture()
    generated = runpy.run_path(str(GENERATOR_PATH))["build_fixture"]()
    assert fixture == generated
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 600
    groups = {case["group"] for case in fixture["cases"]}
    assert len(groups) == 12
    assert all(sum(case["group"] == group for case in fixture["cases"]) == 50 for group in groups)


def test_report_is_body_free_bound_to_fixture_and_passes_safety_gates():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["sample_count"] == 600 and len(report["outcomes"]) == 600
    assert report["shadow_exact_rate"] == report["required_source_recall_rate"] == 1.0
    assert report["forbidden_source_violation_rate"] == 0.0
    assert report["bounded_query_rate"] == report["source_binding_rate"] == 1.0
    encoded = json.dumps(report, ensure_ascii=False)
    assert '"input"' not in encoded and "raw_model_output" not in encoded


def test_registry_contract_preserves_ctx_ownership_and_cannot_exceed_shadow():
    definition = cds.REGISTRY.get(planner.DECISION_KIND)
    assert definition.input_type is planner.RecallPlannerInput
    assert definition.result_type is planner.RecallPlannerResult
    assert definition.mode is cds.DecisionMode.SHADOW
    assert definition.fallback_owner == definition.application_owner == "ctx"
    assert definition.privacy_class == "user_private"
    assert definition.model_binding_revision == cds.MODEL_BINDING_POLICY_VERSION


@pytest.mark.parametrize("case", _fixture()["cases"])
def test_all_frozen_synthetic_cases_match_the_bounded_contract(case):
    payload = _payload(case["input"]["text"], message_id=case["input"]["message_id"])
    result = planner.plan_shadow(payload)
    planner.validate(payload, result)
    actual = {
        "task_type": result.task_type, "memory_need": result.memory_need,
        "history_need": result.history_need, "knowledge_need": result.knowledge_need,
        "lore_need": result.lore_need, "episode_saga_need": result.episode_saga_need,
        "hard_refusal": result.hard_refusal,
    }
    assert actual == case["expected"]


def test_explicit_forbid_selects_nothing_but_double_negative_does_not_forbid():
    forbidden = _payload("不要搜索任何资料")
    result = planner.plan_shadow(forbidden)
    planner.validate(forbidden, result)
    assert forbidden.forbidden_sources == tuple(item.value for item in planner.SourceKind)
    assert result.hard_refusal is True and result.selected_ids == () and result.query_terms == ()

    allowed = _payload("不要不查文档里的结论")
    assert allowed.forbidden_sources == ()
    allowed_result = planner.plan_shadow(allowed)
    planner.validate(allowed, allowed_result)
    assert allowed_result.hard_refusal is False and "source:knowledge" in allowed_result.selected_ids

    separate_clause = _payload("不用担心，帮我查文档里的结论")
    assert separate_clause.forbidden_sources == ()
    assert planner.plan_shadow(separate_clause).knowledge_need == "high"


def test_query_and_source_message_are_bounded_and_validator_rejects_invention():
    payload = _payload("比较两份文档并分析 A B C D E F G H I J K L 的差异")
    result = planner.plan_shadow(payload)
    planner.validate(payload, result)
    assert len(result.query_terms) <= 8 and all(len(term) <= 40 for term in result.query_terms)
    assert result.evidence_message_ids == (payload.source_message_id,)
    with pytest.raises(cds.DecisionProtocolError, match="source message"):
        planner.validate(payload, planner.RecallPlannerResult(
            **{**result.__dict__, "evidence_message_ids": ("invented",)}
        ))
    with pytest.raises(cds.DecisionProtocolError, match="unbound recall source"):
        planner.validate(payload, planner.RecallPlannerResult(
            **{**result.__dict__, "selected_ids": ("source:invented",)}
        ))


def test_shadow_evaluation_cannot_write_retrieval_domains_or_apply_context():
    payload = _payload("对比两份文档的迁移策略")
    source = (cds.SourceSnapshot("message", payload.source_message_id, "1", "c" * 64),)
    header = cds.build_header(
        decision_kind=planner.DECISION_KIND, policy_version=planner.POLICY_VERSION,
        request_id="recall-shadow-run", mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    candidates = tuple(
        cds.CandidateRef(item, "recall_source", hashlib.sha256(item.encode()).hexdigest())
        for item in planner.candidate_ids()
    )
    result = planner.plan_shadow(payload)
    raw = json.dumps({
        **result.__dict__, "selected_ids": list(result.selected_ids),
        "reason_codes": list(result.reason_codes),
        "evidence_message_ids": list(result.evidence_message_ids),
        "query_terms": list(result.query_terms),
    })
    tables = ("memory_recall_events", "knowledge_chat_retrievals", "conversation_history_recall_events")
    conn = db.connect()
    try:
        before = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables)
    finally:
        conn.close()
    run, _ = cds.create_run(header, payload, candidates)
    outcome = cds.evaluate_output(run.id, header, payload, raw, current_snapshot=source)
    conn = db.connect()
    try:
        after = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables)
    finally:
        conn.close()
    assert outcome["application_allowed"] is False and outcome["fallback_used"] is False
    assert result.advisory_expand_only is True and after == before


def test_active_header_is_rejected_before_a_run_can_be_created():
    payload = _payload("回忆我们之前的决定")
    source = (cds.SourceSnapshot("message", payload.source_message_id, "1", "d" * 64),)
    header = cds.build_header(
        decision_kind=planner.DECISION_KIND, policy_version=planner.POLICY_VERSION,
        request_id="recall-active-run", mode=cds.DecisionMode.ACTIVE,
        source_snapshot=source,
    )
    candidates = tuple(
        cds.CandidateRef(item, "recall_source", hashlib.sha256(item.encode()).hexdigest())
        for item in planner.candidate_ids()
    )
    with pytest.raises(cds.DecisionProtocolError) as exc:
        cds.create_run(header, payload, candidates)
    assert exc.value.code == "mode_not_authorized"

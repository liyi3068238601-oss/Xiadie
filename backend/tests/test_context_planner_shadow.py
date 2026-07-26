from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from app import cognitive_decision as cds
from app import context_assembler, context_budget
from app import context_planner_shadow as planner

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cds7_context_planner_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-7-context-planner-shadow.json"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cds7_context_fixture.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _payload(case: dict) -> planner.ContextPlannerInput:
    return planner.ContextPlannerInput(
        candidate_ids=planner.component_ids(),
        source_message_id=case["input"]["message_id"],
        valid_message_ids=(case["input"]["message_id"],),
        task_type=case["input"]["task_type"],
        available_components=tuple(case["input"]["available_components"]),
    )


def test_fixture_is_deterministic_synthetic_and_covers_four_context_scenes():
    fixture = _fixture()
    assert fixture == runpy.run_path(str(GENERATOR_PATH))["build_fixture"]()
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 80
    assert {case["group"] for case in fixture["cases"]} == {
        "document", "history", "relationship", "lore",
    }


@pytest.mark.parametrize("case", _fixture()["cases"])
def test_all_synthetic_cases_match_bounded_priority_proposals(case):
    payload = _payload(case)
    result = planner.plan_shadow(payload)
    planner.validate(payload, result)
    assert {
        "task_type": result.task_type,
        "allocation_rank": list(result.allocation_rank),
        "importance_by_component": result.importance_by_component,
        "must_include": list(result.must_include),
        "may_drop": list(result.may_drop),
    } == case["expected"]


def test_contract_is_shadow_only_and_keeps_fixed_ratio_fallback():
    definition = cds.REGISTRY.get(planner.DECISION_KIND)
    assert definition.mode is cds.DecisionMode.SHADOW
    assert definition.fallback_owner == definition.application_owner == "ctx"
    payload = _payload(_fixture()["cases"][0])
    fallback = planner.fixed_ratio_fallback(payload)
    planner.validate(payload, fallback)
    assert fallback.allocation_rank == planner.FIXED_RATIO_PRIORITY
    assert fallback.reason_codes == ("fixed_ratio_fallback",)


def test_plan_shadow_rejects_unknown_task_type_before_fallback():
    payload = _payload(_fixture()["cases"][0])
    unknown = planner.ContextPlannerInput(**{
        **payload.__dict__, "task_type": "unknown",
    })
    with pytest.raises(cds.DecisionProtocolError) as exc:
        planner.plan_shadow(unknown)
    assert exc.value.code == "task_type_invalid"


def test_main_import_registers_context_planner_in_a_fresh_runtime():
    completed = subprocess.run(
        [sys.executable, "-c", "from app import main, cognitive_decision as c; print(c.REGISTRY.get('context_planner').decision_kind)"],
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == planner.DECISION_KIND


def test_protected_regions_cannot_be_removed_or_reordered_by_a_proposal():
    payload = _payload(_fixture()["cases"][0])
    result = planner.plan_shadow(payload)
    assert result.protected_regions == planner.PROTECTED_REGIONS
    with pytest.raises(cds.DecisionProtocolError) as exc:
        planner.validate(payload, planner.ContextPriorityProposal(
            **{**result.__dict__, "protected_regions": ("current_question",)}
        ))
    assert exc.value.code == "protected_region_invalid"


def test_validator_rejects_final_token_numbers_and_unknown_components():
    payload = _payload(_fixture()["cases"][0])
    result = planner.plan_shadow(payload)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        planner.validate(payload, planner.ContextPriorityProposal(
            **{**result.__dict__, "importance_by_component": {
                **result.importance_by_component, "knowledge_tokens": 2048,
            }}
        ))
    assert exc.value.code == "component_priority_invalid"


def test_validator_rejects_priority_sets_that_disagree_with_importance():
    payload = _payload(_fixture()["cases"][0])
    result = planner.plan_shadow(payload)
    with pytest.raises(cds.DecisionProtocolError) as exc:
        planner.validate(payload, planner.ContextPriorityProposal(
            **{**result.__dict__, "must_include": ("lore",), "may_drop": ("knowledge",)}
        ))
    assert exc.value.code == "component_set_invalid"


def test_runtime_evaluates_allocation_rank_without_allowing_application():
    payload = _payload(_fixture()["cases"][0])
    source = (cds.SourceSnapshot("message", payload.source_message_id, "1", "e" * 64),)
    header = cds.build_header(
        decision_kind=planner.DECISION_KIND,
        policy_version=planner.POLICY_VERSION,
        request_id="context-planner-runtime",
        mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    candidates = tuple(
        cds.CandidateRef(item, "context_component", hashlib.sha256(item.encode()).hexdigest())
        for item in planner.component_ids()
    )
    proposal = planner.plan_shadow(payload)
    raw = json.dumps({
        **proposal.__dict__,
        "selected_ids": list(proposal.selected_ids),
        "reason_codes": list(proposal.reason_codes),
        "evidence_message_ids": list(proposal.evidence_message_ids),
        "allocation_rank": list(proposal.allocation_rank),
        "must_include": list(proposal.must_include),
        "may_drop": list(proposal.may_drop),
        "protected_regions": list(proposal.protected_regions),
    })
    run, _ = cds.create_run(header, payload, candidates)
    outcome = cds.evaluate_output(run.id, header, payload, raw, current_snapshot=source)
    assert outcome["fallback_used"] is False
    assert outcome["application_allowed"] is False
    assert outcome["selected_ids"] == list(proposal.allocation_rank)


@pytest.mark.parametrize("window,output", [(4_096, 512), (8_192, 1_024), (32_768, 2_048)])
def test_real_assemble_preserves_current_question_and_output_reserve_at_boundaries(window, output):
    capability = context_budget.resolve_model_context_capability(
        {"id": "custom"},
        "cds7-boundary",
        configured_profiles={
            "custom/cds7-boundary": {
                "context_window": window,
                "max_output_tokens": output,
                "default_output_tokens": output,
            },
        },
    )
    current_question = "边界问题-" + "问" * 40
    package = context_assembler.assemble(
        history=[{"id": "current", "role": "user", "content": current_question, "model": ""}],
        capability=capability,
        output_reserve_tokens=output,
        attachment_block="附件" * 500,
        memory_digest="记忆" * 500,
        lore_digest="设定" * 500,
        knowledge_block="知识" * 500,
    )
    assert package.messages[-1]["content"] == current_question
    assert package.output_reserve_tokens == output
    assert package.budget_plan.reserved_total_tokens <= window


def test_real_assemble_fails_closed_when_protected_regions_exceed_tiny_window():
    capability = context_budget.resolve_model_context_capability(
        {"id": "custom"},
        "cds7-tiny-boundary",
        configured_profiles={
            "custom/cds7-tiny-boundary": {
                "context_window": 512,
                "max_output_tokens": 128,
                "default_output_tokens": 128,
            },
        },
    )
    with pytest.raises(context_budget.ContextBudgetError) as exc:
        context_assembler.assemble(
            history=[{"id": "current", "role": "user", "content": "边界问题", "model": ""}],
            capability=capability,
            output_reserve_tokens=128,
        )
    assert exc.value.code == "context_protected_region_exceeds_window"


def test_report_is_body_free_bound_to_fixture_and_records_plan_actual_differences():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["sample_count"] == 80 and len(report["outcomes"]) == 80
    assert report["proposal_exact_rate"] == 1.0
    assert report["protected_region_preservation_rate"] == 1.0
    assert report["output_reserve_preservation_rate"] == 1.0
    assert report["real_assembly_validation_rate"] == 1.0
    assert report["difference_recording_rate"] == 1.0
    assert report["production_assembly_changed"] is False
    encoded = json.dumps(report, ensure_ascii=False)
    assert '"input"' not in encoded and "raw_model_output" not in encoded

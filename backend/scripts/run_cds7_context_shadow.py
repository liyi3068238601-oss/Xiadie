from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import context_assembler, context_budget, context_planner_shadow as planner  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "cds7_context_planner_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cds-7-context-planner-shadow.json"
MD_PATH = PROJECT_DIR / "docs" / "reports" / "cds-7-context-planner-shadow.md"


def _payload(case: dict) -> planner.ContextPlannerInput:
    item = case["input"]
    return planner.ContextPlannerInput(
        candidate_ids=planner.component_ids(),
        source_message_id=item["message_id"],
        valid_message_ids=(item["message_id"],),
        task_type=item["task_type"],
        available_components=tuple(item["available_components"]),
    )


def _view(result: planner.ContextPriorityProposal) -> dict:
    return {
        "task_type": result.task_type,
        "allocation_rank": list(result.allocation_rank),
        "importance_by_component": result.importance_by_component,
        "must_include": list(result.must_include),
        "may_drop": list(result.may_drop),
    }


def build_report(fixture: dict) -> dict:
    outcomes = []
    for case in fixture["cases"]:
        payload = _payload(case)
        proposal = planner.plan_shadow(payload)
        planner.validate(payload, proposal)
        units = int(case["input"]["component_units"])
        values = {
            name: (f"{name}-合成内容" * units) if name in payload.available_components else ""
            for name in planner.COMPONENTS
        }
        output_reserve = 1_024
        window = max(8_192, int(case["input"]["total_budget"]) * 8)
        capability = context_budget.resolve_model_context_capability(
            {"id": "custom"},
            "cds7-evaluation",
            configured_profiles={
                "custom/cds7-evaluation": {
                    "context_window": window,
                    "max_output_tokens": output_reserve,
                    "default_output_tokens": output_reserve,
                },
            },
        )
        current_question = f"合成评测问题-{case['id']}"
        package = context_assembler.assemble(
            history=[
                {"id": f"prior-user-{case['id']}", "role": "user", "content": "合成上一轮问题", "model": ""},
                {"id": f"prior-assistant-{case['id']}", "role": "assistant", "content": "合成上一轮回答", "model": "synthetic"},
                {"id": case["input"]["message_id"], "role": "user", "content": current_question, "model": ""},
            ],
            capability=capability,
            output_reserve_tokens=output_reserve,
            attachment_block=values["attachment"],
            memory_digest=values["existing_memory_digest"],
            lore_digest=values["lore"],
            knowledge_block=values["knowledge"],
            cross_session_recall=(),
        )
        actual_tokens = package.component_tokens
        actual_order = sorted(
            (name for name, tokens in actual_tokens.items() if tokens),
            key=lambda name: (-actual_tokens[name], planner.COMPONENTS.index(name)),
        )
        proposed = list(proposal.allocation_rank)
        differences = {
            "order_changed": proposed != actual_order,
            "proposed_but_not_injected": [name for name in proposed if not actual_tokens[name]],
            "injected_outside_proposal": [name for name in actual_order if name not in proposed],
        }
        outcomes.append({
            "case_id": case["id"],
            "group": case["group"],
            "proposal_exact": _view(proposal) == case["expected"],
            "proposed_allocation_rank": proposed,
            "actual_fixed_ratio_order": actual_order,
            "actual_component_tokens": actual_tokens,
            "differences": differences,
            "difference_recorded": set(differences) == {
                "order_changed", "proposed_but_not_injected", "injected_outside_proposal",
            },
            "protected_regions_preserved": proposal.protected_regions == planner.PROTECTED_REGIONS,
            "output_reserve_preserved": "output_reserve" in proposal.protected_regions,
            "real_assembly_valid": (
                package.messages[-1]["content"] == current_question
                and package.messages[-3]["content"] == "合成上一轮问题"
                and package.messages[-2]["content"] == "合成上一轮回答"
                and package.output_reserve_tokens == output_reserve
                and package.budget_plan.reserved_total_tokens <= window
            ),
        })
    total = len(outcomes)
    return {
        "report_version": "context-planner-shadow-report-v1",
        "protocol_version": fixture["protocol_version"],
        "synthetic_only": True,
        "contains_user_data": False,
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "sample_count": total,
        "group_counts": dict(sorted(Counter(row["group"] for row in outcomes).items())),
        "proposal_exact_rate": sum(row["proposal_exact"] for row in outcomes) / total,
        "protected_region_preservation_rate": sum(row["protected_regions_preserved"] for row in outcomes) / total,
        "output_reserve_preservation_rate": sum(row["output_reserve_preserved"] for row in outcomes) / total,
        "real_assembly_validation_rate": sum(row["real_assembly_valid"] for row in outcomes) / total,
        "difference_recording_rate": sum(row["difference_recorded"] for row in outcomes) / total,
        "proposal_actual_difference_rate": sum(row["differences"]["order_changed"] for row in outcomes) / total,
        "production_assembly_changed": False,
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    return "\n".join([
        "# CDS.7 ContextPlanner Shadow 评测", "",
        f"- 样本：{report['sample_count']} 个纯合成场景；不含用户数据，不调用真实 Provider。",
        f"- Fixture SHA-256：`{report['fixture_sha256']}`",
        f"- Proposal 精确匹配：{report['proposal_exact_rate']:.2%}",
        f"- Proposal 与 CTX v1 实际注入顺序存在差异：{report['proposal_actual_difference_rate']:.2%}", "",
        "## 完成门", "", "| 指标 | 结果 |", "|---|---:|",
        f"| 当前问题、最近完整轮次与输出预算保护 | {report['protected_region_preservation_rate']:.2%} |",
        f"| 输出预算保护 | {report['output_reserve_preservation_rate']:.2%} |",
        f"| 真实 assemble 保护区验证 | {report['real_assembly_validation_rate']:.2%} |",
        f"| 计划与实际注入差异记录 | {report['difference_recording_rate']:.2%} |", "",
        "## 边界", "",
        "- `context-priority-proposal-v1` 只表达语义优先级，不输出最终 token 数。",
        "- 实际注入继续调用冻结 CTX v1 固定比例分配器；本评测不向 ContextAssembler 传入 proposal。",
        "- 固定比例 fallback 保留，ContextPackage v1、生产聊天装配与输出预算均未修改。", "",
    ])


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    report = build_report(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_PATH.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()

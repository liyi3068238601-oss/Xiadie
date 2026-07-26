"""Run CDS.4 RecallPlanner against frozen legacy triggers and synthetic labels."""
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

from app import history_recall, knowledge_context, lore, recall_planner_shadow as planner  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "cds4_recall_planner_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cds-4-recall-shadow.json"
MD_PATH = PROJECT_DIR / "docs" / "reports" / "cds-4-recall-shadow.md"


def legacy_sources(text: str) -> tuple[str, ...]:
    selected = ["memory"]  # Current chat calls memory.build_digest for every text turn.
    if history_recall._EXPLICIT_RECALL.search(text):  # noqa: SLF001 - frozen trigger comparison
        selected.append("history")
    if knowledge_context.retrieval_query(text)[0]:
        selected.append("knowledge")
    if lore.retrieve_lore(text):
        selected.append("lore")
    return tuple(selected)


def payload(case: dict) -> planner.RecallPlannerInput:
    text = case["input"]["text"]
    message_id = case["input"]["message_id"]
    return planner.RecallPlannerInput(
        candidate_ids=planner.candidate_ids(), source_message_id=message_id,
        valid_message_ids=(message_id,), text=text,
        forbidden_sources=planner.detect_forbidden_sources(text),
        legacy_selected_sources=legacy_sources(text),
    )


def view(result: planner.RecallPlannerResult) -> dict:
    return {
        "task_type": result.task_type, "memory_need": result.memory_need,
        "history_need": result.history_need, "knowledge_need": result.knowledge_need,
        "lore_need": result.lore_need, "episode_saga_need": result.episode_saga_need,
        "hard_refusal": result.hard_refusal,
    }


def build_report(fixture: dict) -> dict:
    outcomes = []
    for case in fixture["cases"]:
        item = payload(case)
        result = planner.plan_shadow(item)
        planner.validate(item, result)
        expected = case["expected"]
        selected = {value.removeprefix("source:") for value in result.selected_ids}
        legacy = set(item.legacy_selected_sources)
        required = {kind.removesuffix("_need") for kind, need in expected.items()
                    if kind.endswith("_need") and need != "none"}
        outcomes.append({
            "case_id": case["id"], "group": case["group"],
            "shadow_exact": view(result) == expected,
            "legacy_source_exact": legacy == required,
            "selected_sources": sorted(selected), "legacy_sources": sorted(legacy),
            "required_source_miss": sorted(required - selected),
            "forbidden_source_selected": bool(item.forbidden_sources and selected),
            "query_bounded": len(result.query_terms) <= 8 and all(len(term) <= 40 for term in result.query_terms),
            "source_bound": result.evidence_message_ids == (item.source_message_id,),
        })
    total = len(outcomes)
    return {
        "report_version": "recall-planner-shadow-report-v1",
        "protocol_version": fixture["protocol_version"],
        "synthetic_only": True, "contains_user_data": False,
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "sample_count": total,
        "group_counts": dict(sorted(Counter(row["group"] for row in outcomes).items())),
        "shadow_exact_rate": sum(row["shadow_exact"] for row in outcomes) / total,
        "legacy_source_exact_rate": sum(row["legacy_source_exact"] for row in outcomes) / total,
        "required_source_recall_rate": 1 - sum(bool(row["required_source_miss"]) for row in outcomes) / total,
        "forbidden_source_violation_rate": sum(row["forbidden_source_selected"] for row in outcomes) / total,
        "bounded_query_rate": sum(row["query_bounded"] for row in outcomes) / total,
        "source_binding_rate": sum(row["source_bound"] for row in outcomes) / total,
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    return "\n".join([
        "# CDS.4 RecallPlanner Shadow 评测", "",
        f"- 样本：{report['sample_count']} 轮纯合成输入；不含用户数据，不调用真实 Provider。",
        f"- Fixture SHA-256：`{report['fixture_sha256']}`",
        f"- Shadow 任务与来源需求精确匹配：{report['shadow_exact_rate']:.2%}",
        f"- 冻结旧触发器来源精确匹配：{report['legacy_source_exact_rate']:.2%}", "",
        "## 安全门", "",
        "| 指标 | 结果 |", "|---|---:|",
        f"| 必需来源召回率 | {report['required_source_recall_rate']:.2%} |",
        f"| 明确禁止后仍选择来源 | {report['forbidden_source_violation_rate']:.2%} |",
        f"| 查询建议有界率 | {report['bounded_query_rate']:.2%} |",
        f"| source message 绑定率 | {report['source_binding_rate']:.2%} |", "",
        "## 边界", "",
        "- Planner 只输出 SourceKind、任务/查询意图、需求等级与最多8个查询词。",
        "- 不执行检索、不生成候选、不读取正文、不注入 ContextPackage。",
        "- CTX/Knowledge/MEM/Lore/Episode-Saga 保留权限、候选和最终预算裁决权。", "",
    ])


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture["scenario_count"] < 500 or fixture["contains_user_data"] is not False:
        raise ValueError("CDS.4 fixture boundary failed")
    report = build_report(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_PATH.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()

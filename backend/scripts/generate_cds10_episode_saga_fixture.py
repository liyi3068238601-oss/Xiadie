from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds10_episode_saga_shadow_v1.json"
QUALITY_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds10_episode_saga_quality_v1.json"

GROUPS = {
    "episode_complete_narrative": ("episode_boundary_proposal", {
        "same_goal": True, "causal_chain": True, "outcome_present": True,
        "projected_confidence": "high", "expected": "form_episode",
    }),
    "episode_open_narrative": ("episode_boundary_proposal", {
        "same_goal": True, "causal_chain": True, "outcome_present": False,
        "projected_confidence": "medium", "expected": "form_episode",
    }),
    "episode_goal_mismatch": ("episode_boundary_proposal", {
        "same_goal": False, "causal_chain": True, "outcome_present": True,
        "projected_confidence": "medium", "expected": "skip",
    }),
    "episode_causal_gap": ("episode_boundary_proposal", {
        "same_goal": True, "causal_chain": False, "outcome_present": False,
        "projected_confidence": "medium", "expected": "skip",
    }),
    "episode_low_confidence": ("episode_boundary_proposal", {
        "same_goal": True, "causal_chain": True, "outcome_present": True,
        "projected_confidence": "low", "expected": "skip",
    }),
    "saga_create": ("saga_transition_proposal", {
        "transition_hint": "create_new", "target_status": None,
        "evidence_origin": "observed", "projected_confidence": "high", "expected": "create_new",
    }),
    "saga_append": ("saga_transition_proposal", {
        "transition_hint": "append_existing", "target_status": "active",
        "evidence_origin": "observed", "projected_confidence": "high", "expected": "append_existing",
    }),
    "saga_branch": ("saga_transition_proposal", {
        "transition_hint": "branch", "target_status": "active",
        "evidence_origin": "observed", "projected_confidence": "medium", "expected": "branch",
    }),
    "saga_pause": ("saga_transition_proposal", {
        "transition_hint": "pause", "target_status": "active",
        "evidence_origin": "observed", "projected_confidence": "high", "expected": "pause",
    }),
    "saga_complete": ("saga_transition_proposal", {
        "transition_hint": "complete", "target_status": "active",
        "evidence_origin": "user_confirmed", "projected_confidence": "high", "expected": "complete",
    }),
    "saga_confirmed_revive": ("saga_transition_proposal", {
        "transition_hint": "revive", "target_status": "completed",
        "evidence_origin": "user_confirmed", "projected_confidence": "high", "expected": "revive",
    }),
    "saga_merge_review": ("saga_transition_proposal", {
        "transition_hint": "merge_suggestion", "target_status": "active",
        "evidence_origin": "observed", "projected_confidence": "high", "expected": "merge_suggestion",
    }),
}


def build_fixture() -> dict:
    cases = []
    for group, (decision_kind, template) in GROUPS.items():
        for index in range(1, 21):
            case_id = f"{group}-{index:02d}"
            if decision_kind == "episode_boundary_proposal":
                candidate_ids = [f"fragment-{case_id}-{position}" for position in range(3)]
                values = {
                    "candidate_ids": candidate_ids,
                    "same_goal": template["same_goal"],
                    "causal_chain": template["causal_chain"],
                    "turning_point_ids": [candidate_ids[1]] if index % 2 else [],
                    "outcome_present": template["outcome_present"],
                    "projected_confidence": template["projected_confidence"],
                }
            else:
                target_id = None if template["target_status"] is None else f"saga-{case_id}"
                values = {
                    "candidate_ids": [f"episode-{case_id}-0", f"episode-{case_id}-1"],
                    "target_saga_id": target_id,
                    "target_status": template["target_status"],
                    "transition_hint": template["transition_hint"],
                    "evidence_origin": template["evidence_origin"],
                    "projected_confidence": template["projected_confidence"],
                }
            cases.append({
                "id": case_id, "group": group, "decision_kind": decision_kind,
                "input": values, "expected": {"proposal": template["expected"]},
            })
    return {
        "protocol_version": "cds10-episode-saga-shadow-evaluation-v1",
        "synthetic_only": True, "contains_user_data": False,
        "scenario_count": len(cases), "cases": cases,
    }


QUALITY_SCENARIOS = (
    ("episode-release", "episode_boundary_proposal", "form_episode", (
        "团队确认发布目标并开始整理清单", "修复阻塞问题后完成发布并记录结果",
    ), True),
    ("episode-study", "episode_boundary_proposal", "form_episode", (
        "开始准备资格考试并制定复习安排", "按安排完成模拟练习并调整复习重点",
    ), True),
    ("episode-generic-project", "episode_boundary_proposal", "skip", (
        "项目甲开始整理旅行照片", "项目乙完成数据库迁移结果复盘",
    ), True),
    ("episode-unrelated", "episode_boundary_proposal", "skip", (
        "早餐后决定更换咖啡豆", "晚上完成客户端崩溃修复",
    ), False),
    ("saga-product", "saga_transition_proposal", "create_new", (
        "产品原型完成并邀请首批体验", "根据反馈完成第二轮改版",
    ), True),
    ("saga-health", "saga_transition_proposal", "create_new", (
        "开始恢复跑步并完成三公里", "持续训练后完成第一次十公里",
    ), True),
    ("saga-generic-theme", "saga_transition_proposal", "skip", (
        "年度计划中开始学习绘画", "年度计划中完成家庭搬迁",
    ), True),
    ("saga-unrelated", "saga_transition_proposal", "skip", (
        "完成旧电脑资料备份", "开始筹备朋友生日聚会",
    ), False),
)


def build_quality_corpus() -> dict:
    cases = []
    for case_id, decision_kind, label, members, shared_entity in QUALITY_SCENARIOS:
        cases.append({
            "id": case_id, "decision_kind": decision_kind, "label": label,
            "raw_narrative": {
                "members": list(members), "shared_active_entity": shared_entity,
            },
        })
    return {
        "protocol_version": "cds10-episode-saga-raw-narrative-regression-v1",
        "corpus_role": "labeled_raw_narrative_regression",
        "candidate_path": "real_database_candidates", "synthetic_only": True,
        "contains_user_data": False,
        "label_authorship": "human_authored_synthetic_not_reviewed",
        "sample_count": len(cases), "cases": cases,
    }


def main() -> None:
    DEFAULT_OUTPUT.write_text(
        json.dumps(build_fixture(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    QUALITY_OUTPUT.write_text(
        json.dumps(build_quality_corpus(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()

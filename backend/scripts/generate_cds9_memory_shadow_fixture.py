from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds9_memory_shadow_v1.json"

TOPICS = (
    "咖啡", "红茶", "晨跑", "夜间阅读", "周末徒步", "室内植物", "爵士乐",
    "古典音乐", "纸质书", "电子书", "远程办公", "团队协作", "辛辣食物",
    "清淡饮食", "早班航班", "火车旅行", "城市散步", "海边度假", "摄影", "烹饪",
)

GROUPS = {
    "user_supersedes_automatic": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "automatic",
        "newer_origin": "user_confirmed",
        "relation_hint": "contradiction",
        "condition_changed": False,
        "expected": {"relation_type": "supersedes", "superseded_id": "older"},
    },
    "automatic_cannot_supersede_user": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "user_confirmed",
        "newer_origin": "automatic",
        "relation_hint": "contradiction",
        "condition_changed": False,
        "expected": {"relation_type": "possible_conflict", "superseded_id": None},
    },
    "injection_cannot_supersede_user": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "user_confirmed",
        "newer_origin": "system_injected",
        "relation_hint": "contradiction",
        "condition_changed": False,
        "expected": {"relation_type": "possible_conflict", "superseded_id": None},
    },
    "user_confirmed_newer_wins": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "user_confirmed",
        "newer_origin": "user_confirmed",
        "relation_hint": "contradiction",
        "condition_changed": False,
        "expected": {"relation_type": "supersedes", "superseded_id": "older"},
    },
    "observed_newer_wins": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "observed",
        "newer_origin": "observed",
        "relation_hint": "contradiction",
        "condition_changed": False,
        "expected": {"relation_type": "supersedes", "superseded_id": "older"},
    },
    "conditional_difference": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "user_confirmed",
        "newer_origin": "user_confirmed",
        "relation_hint": "contradiction",
        "condition_changed": True,
        "expected": {"relation_type": "conditional_difference", "superseded_id": None},
    },
    "compatible_pair": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "observed",
        "newer_origin": "observed",
        "relation_hint": "compatible",
        "condition_changed": False,
        "expected": {"relation_type": "compatible", "superseded_id": None},
    },
    "user_correction": {
        "decision_kind": "memory_conflict_proposal",
        "older_origin": "user_confirmed",
        "newer_origin": "user_confirmed",
        "relation_hint": "correction",
        "condition_changed": False,
        "expected": {"relation_type": "supersedes", "superseded_id": "older"},
    },
    "protected_keep": {
        "decision_kind": "memory_retention_proposal",
        "origin": "user_confirmed",
        "status": "active",
        "retention_band": "low",
        "protected": True,
        "injection_only": False,
        "expected": {"proposed_action": "keep", "recovery_allowed": False},
    },
    "active_cool": {
        "decision_kind": "memory_retention_proposal",
        "origin": "automatic",
        "status": "active",
        "retention_band": "low",
        "protected": False,
        "injection_only": False,
        "expected": {"proposed_action": "cool", "recovery_allowed": False},
    },
    "cooling_freeze": {
        "decision_kind": "memory_retention_proposal",
        "origin": "automatic",
        "status": "cooling",
        "retention_band": "low",
        "protected": False,
        "injection_only": False,
        "expected": {"proposed_action": "freeze", "recovery_allowed": False},
    },
    "injection_no_recovery": {
        "decision_kind": "memory_retention_proposal",
        "origin": "system_injected",
        "status": "frozen",
        "retention_band": "high",
        "protected": False,
        "injection_only": True,
        "expected": {"proposed_action": "keep", "recovery_allowed": False},
    },
    "confirmed_reconsolidate": {
        "decision_kind": "memory_retention_proposal",
        "origin": "user_confirmed",
        "status": "frozen",
        "retention_band": "high",
        "protected": False,
        "injection_only": False,
        "expected": {"proposed_action": "reconsolidate", "recovery_allowed": True},
    },
    "recent_keep": {
        "decision_kind": "memory_retention_proposal",
        "origin": "observed",
        "status": "active",
        "retention_band": "high",
        "protected": False,
        "injection_only": False,
        "expected": {"proposed_action": "keep", "recovery_allowed": False},
    },
}


def build_fixture() -> dict:
    cases = []
    for group, template in GROUPS.items():
        for index, topic in enumerate(TOPICS, start=1):
            case_id = f"{group}-{index:02d}"
            item = {
                "id": case_id,
                "group": group,
                "scenario": {
                    "topic": topic,
                    "age_days": 181 + index if template["decision_kind"] == "memory_retention_proposal" else index,
                    "importance": round((index - 1) / 100, 2),
                    "confidence": round((20 - index) / 100, 2),
                },
                **template,
            }
            if template["decision_kind"] == "memory_conflict_proposal":
                item["input"] = {
                    "candidate_ids": [f"older-{case_id}", f"newer-{case_id}"],
                    "older_id": f"older-{case_id}",
                    "newer_id": f"newer-{case_id}",
                    "older_origin": template["older_origin"],
                    "newer_origin": template["newer_origin"],
                    "relation_hint": template["relation_hint"],
                    "condition_changed": template["condition_changed"],
                }
                item["expected"] = {
                    **template["expected"],
                    "superseded_id": f"older-{case_id}" if template["expected"]["superseded_id"] else None,
                }
            else:
                item["input"] = {
                    "candidate_ids": [f"fragment-{case_id}"],
                    "fragment_id": f"fragment-{case_id}",
                    "origin": template["origin"],
                    "status": template["status"],
                    "retention_band": template["retention_band"],
                    "protected": template["protected"],
                    "injection_only": template["injection_only"],
                }
            for key in (
                "older_origin", "newer_origin", "relation_hint", "condition_changed",
                "origin", "status", "retention_band", "protected", "injection_only",
            ):
                item.pop(key, None)
            cases.append(item)
    return {
        "protocol_version": "cds9-memory-shadow-evaluation-v1",
        "synthetic_only": True,
        "contains_user_data": False,
        "scenario_count": len(cases),
        "cases": cases,
    }


def main() -> None:
    DEFAULT_OUTPUT.write_text(
        json.dumps(build_fixture(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()

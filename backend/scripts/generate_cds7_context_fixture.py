from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds7_context_planner_v1.json"
COMPONENTS = (
    "attachment", "rolling_summary", "cross_session_recall",
    "existing_memory_digest", "knowledge", "lore",
)
PROFILES = {
    "document": {
        "knowledge": "critical", "attachment": "high", "rolling_summary": "medium",
        "cross_session_recall": "low", "existing_memory_digest": "low", "lore": "low",
    },
    "history": {
        "cross_session_recall": "critical", "rolling_summary": "high",
        "existing_memory_digest": "medium", "attachment": "low", "knowledge": "low", "lore": "low",
    },
    "relationship": {
        "existing_memory_digest": "critical", "cross_session_recall": "high",
        "rolling_summary": "high", "attachment": "low", "knowledge": "low", "lore": "low",
    },
    "lore": {
        "lore": "critical", "rolling_summary": "medium", "attachment": "low",
        "cross_session_recall": "low", "existing_memory_digest": "low", "knowledge": "low",
    },
}
AVAILABILITY = (
    COMPONENTS,
    tuple(name for name in COMPONENTS if name != "attachment"),
    tuple(name for name in COMPONENTS if name not in {"attachment", "rolling_summary"}),
    ("rolling_summary", "cross_session_recall", "existing_memory_digest", "knowledge", "lore"),
    ("rolling_summary", "cross_session_recall", "existing_memory_digest", "knowledge", "lore", "attachment"),
)
RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "none": 0}


def build_fixture() -> dict:
    cases = []
    for group, profile in PROFILES.items():
        for availability_index, available in enumerate(AVAILABILITY, 1):
            for variant in range(1, 5):
                importance = {
                    name: profile[name] if name in available else "none" for name in COMPONENTS
                }
                priority = sorted(
                    available, key=lambda name: (-RANK[importance[name]], COMPONENTS.index(name)),
                )
                cases.append({
                    "id": f"{group}-{availability_index:02d}-{variant:02d}",
                    "group": group,
                    "input": {
                        "message_id": f"msg-{group}-{availability_index:02d}-{variant:02d}",
                        "task_type": group,
                        "available_components": list(available),
                        "total_budget": 256 * (availability_index + variant),
                        "component_units": 400 + variant * 100,
                    },
                    "expected": {
                        "task_type": group,
                        "allocation_rank": priority,
                        "importance_by_component": importance,
                        "must_include": [name for name in priority if importance[name] == "critical"],
                        "may_drop": [name for name in reversed(priority) if importance[name] == "low"],
                    },
                })
    return {
        "protocol_version": "context-priority-proposal-eval-v1",
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

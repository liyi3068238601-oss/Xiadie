"""Build LIFE.0 body-free baseline evidence from current production constants."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import cognitive_decision as cds  # noqa: E402
from app import specialty_contracts  # noqa: E402
from app.affect import engine  # noqa: E402
from app.proactive import protocols  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "life0_evaluation_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "life-0-baseline.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "life-0-construction-baseline.md"
REPORT_VERSION = "life-construction-baseline-v1"
PREDECESSOR_SHA = "0d7a2d08dc07f123d016da26da117fa58f9a48a1"


def _affect_baselines() -> list[dict]:
    initial = {
        "affect": dict(engine.DEFAULT_AFFECT),
        "relationship": dict(engine.DEFAULT_RELATIONSHIP),
    }
    rows = []
    for hours in (1, 8, 24, 72, 168):
        state = engine.advance(initial, hours * 60)
        rows.append({
            "hours": hours,
            "contact_need": round(state["affect"]["contact_need"], 6),
            "guardedness": round(engine.guardedness(state), 6),
            "valence": round(state["affect"]["valence"], 6),
            "arousal": round(state["affect"]["arousal"], 6),
            "immersion": round(state["affect"]["immersion"], 6),
            "bond": round(state["relationship"]["bond"], 6),
            "trust": round(state["relationship"]["trust"], 6),
        })
    return rows


def build_report(fixture: dict) -> dict:
    if fixture.get("scenario_count") != 60 or len(fixture.get("cases", [])) != 60:
        raise ValueError("LIFE.0 requires exactly 60 frozen scenarios")
    category_counts = Counter(case["category"] for case in fixture["cases"])
    return {
        "report_version": REPORT_VERSION,
        "construction_baseline": {
            "repository": "liyi3068238601-oss/Xiadie",
            "predecessor_pr": 2,
            "base_branch": "main",
            "base_commit_sha": PREDECESSOR_SHA,
            "schema_version": 63,
            "frozen_protocols": [
                cds.PROTOCOL_VERSION, cds.REGISTRY_VERSION,
                specialty_contracts.CONTRACT_VERSION,
                protocols.CONVERSATION_PRESENCE_V2,
                protocols.USER_AFFECT_OBSERVATION_V1,
                protocols.RELATIONSHIP_MEANING_V1,
                protocols.PROACTIVE_DECISION_V2,
                protocols.EXPRESSION_PLAN_V1,
                protocols.PROACTIVE_FEEDBACK_V1,
                "context-package-v1",
            ],
            "test_baseline": {
                "backend": "2304 passed, 1 warning",
                "frontend": "47 passed",
                "vite_modules": 189,
            },
            "plan_version": "LIFE v0.3",
            "recorded_at": "2026-07-26",
        },
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "scenario_counts": dict(sorted(category_counts.items())),
        "scenario_coverage": {
            "implemented": 0,
            "partial_adjacent_guards": category_counts["decision"],
            "missing_life_domain": 45,
        },
        "affect_relationship_baseline": _affect_baselines(),
        "current_capability_matrix": [
            {"capability": "affect_relationship", "state": "implemented", "owner": "EAP/Affect"},
            {"capability": "context_assembler", "state": "implemented", "owner": "CTX"},
            {"capability": "memory_episode_saga", "state": "implemented", "owner": "MEM"},
            {"capability": "life_proactive_seed_adapter", "state": "partial", "owner": "EAP"},
            {"capability": "life_clock_self_state", "state": "missing", "owner": "LIFE"},
            {"capability": "life_event_ledger", "state": "missing", "owner": "LIFE"},
            {"capability": "daily_schedule_goal", "state": "missing", "owner": "LIFE"},
            {"capability": "important_date", "state": "missing", "owner": "LIFE"},
            {"capability": "diary_continuity", "state": "missing", "owner": "LIFE"},
            {"capability": "self_timeline", "state": "missing", "owner": "LIFE"},
        ],
        "existing_workers": [
            "conversation_summary", "affect_observer", "companion_cognition",
            "proactive_orchestrator", "memory_observer", "episode_consolidator",
            "saga_consolidator", "archivist", "knowledge", "knowledge_recall",
        ],
        "existing_llm_decision_points": [
            "affect_observer", "companion_cognition", "cognitive_runtime",
            "conversation_summary", "episode_summary", "memory_observer", "saga_summary",
        ],
        "fixed_algorithms": [
            "affect-v1.2", "context-package-v1", "context-budget-v1",
            "conversation-presence-v2 fallback", "proactive deterministic policy",
            "archivist retention policy", "episode grouping", "saga grouping",
            "knowledge recall gate", "history recall gate",
        ],
        "privacy": {
            "synthetic_only": True, "contains_user_data": False,
            "raw_model_output_persisted": False, "fixture_inputs_in_report": False,
        },
    }


def render_markdown(report: dict) -> str:
    base = report["construction_baseline"]
    lines = [
        "# LIFE.0 ConstructionBaseline 与实现差距报告", "",
        f"- predecessor：PR #{base['predecessor_pr']} merge `{base['base_commit_sha']}`",
        f"- 基线：Schema {base['schema_version']}；后端 `{base['test_baseline']['backend']}`；前端 `{base['test_baseline']['frontend']}`；Vite {base['test_baseline']['vite_modules']} modules",
        f"- 固定场景：60 条纯合成场景；fixture SHA-256 `{report['fixture_sha256']}`；不调用真实 Provider",
        "", "## 当前能力矩阵", "",
        "| 能力 | 状态 | 唯一所有者 |", "|---|---|---|",
    ]
    for item in report["current_capability_matrix"]:
        lines.append(f"| `{item['capability']}` | {item['state']} | {item['owner']} |")
    lines += [
        "", "## Affect / Relationship 时间基线", "",
        "| 小时 | contact_need | guardedness | valence | arousal | immersion | bond | trust |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["affect_relationship_baseline"]:
        lines.append(
            f"| {row['hours']} | {row['contact_need']:.6f} | {row['guardedness']:.6f} | "
            f"{row['valence']:.6f} | {row['arousal']:.6f} | {row['immersion']:.6f} | "
            f"{row['bond']:.6f} | {row['trust']:.6f} |"
        )
    lines += [
        "", "## 审计结论", "",
        "- 当前没有 LifeClock、SelfState、LifeEvent、DailySchedule、PersonalGoal、ImportantDate、Diary 或 SelfTimeline 领域实现。",
        "- 现有 `life_proactive_seeds` 只是 EAP 拥有的候选入口，不是 LIFE 事实表，也不具备发送权。",
        "- 当前 lifespan 已有十类 worker；LIFE 必须复用单一生命周期编排，不创建主动发送器、第二套情绪/关系或第二套记忆。",
        "- 60 场景中 45 条 LIFE 领域能力尚缺失；15 条决策安全场景仅有 CDS 邻接门禁，不能当作 LIFE 已实现。",
        "- 离线连续性定义为下次启动时的有界模拟补算；应用完全退出期间不调用 Provider、不访问网络、不执行工具、不投递消息。",
        "- 参考项目只作产品理念分析；未导入或复制其代码、Prompt 或资源。",
        "", "## 回滚", "",
        "LIFE.0 只新增测试、合成 fixture、ADR 与报告，不新增迁移或生产写路径；回滚提交不会影响用户数据。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    report = build_report(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"scenario_count": 60, "json": str(JSON_PATH), "markdown": str(MARKDOWN_PATH)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

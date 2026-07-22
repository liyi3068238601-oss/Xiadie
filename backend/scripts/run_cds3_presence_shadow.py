"""Run the body-free CDS.3 compatibility comparison on synthetic turns."""
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

from app import presence_thread_shadow as observer  # noqa: E402
from app.proactive import presence  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "cds3_presence_shadow_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cds-3-presence-shadow.json"
MD_PATH = PROJECT_DIR / "docs" / "reports" / "cds-3-presence-shadow.md"


def _payload(case: dict) -> observer.PresenceThreadInput:
    raw = case["input"]
    legacy = presence.detect_presence_signals(raw["text"])
    message_id = raw["message_id"]
    return observer.PresenceThreadInput(
        candidate_ids=observer.candidate_ids(), source_message_id=message_id,
        valid_message_ids=(message_id,) if message_id else (), text=raw["text"],
        silence_observed=raw["silence_observed"], legacy_presence_state=legacy.user_status,
        legacy_open_thread=legacy.open_thread,
        legacy_open_thread_topic=legacy.open_thread_topic,
        current_open_threads=tuple(raw.get("current_open_threads", ())),
    )


def _view(result: observer.PresenceThreadResult) -> dict:
    return {
        "presence_state": result.presence_state, "expect_return": result.expect_return,
        "conversation_closure": result.conversation_closure,
        "open_threads": list(result.open_threads),
        "followup_allowed": result.followup_allowed,
    }


def build_report(fixture: dict) -> dict:
    outcomes = []
    for case in fixture["cases"]:
        payload = _payload(case)
        shadow = observer.observe_shadow(payload)
        fallback = observer.legacy_fallback(payload)
        observer.validate(payload, shadow)
        expected = case["expected"]
        shadow_view, legacy_view = _view(shadow), _view(fallback)
        outcomes.append({
            "case_id": case["id"], "group": case["group"],
            "shadow_exact": shadow_view == expected,
            "legacy_exact": legacy_view == expected,
            "shadow_state": shadow.presence_state,
            "legacy_state": fallback.presence_state,
            "source_bound": shadow.evidence_message_ids == (
                (payload.source_message_id,) if payload.source_message_id else ()
            ),
        })
    total = len(outcomes)
    groups = Counter(row["group"] for row in outcomes)
    sleep = [row for row in fixture["cases"] if row["group"] == "sleep"]
    tests = [row for row in fixture["cases"] if row["group"] == "test_departure"]
    silence = [row for row in fixture["cases"] if row["group"] == "unknown_silence"]
    sleep_yes = sum(observer.observe_shadow(_payload(row)).expect_return == "yes" for row in sleep)
    test_open = sum("test_result" in observer.observe_shadow(_payload(row)).open_threads for row in tests)
    silence_rejected = sum(
        observer.observe_shadow(_payload(row)).conversation_closure == "closed" for row in silence
    )
    return {
        "report_version": "presence-thread-shadow-report-v1",
        "protocol_version": fixture["protocol_version"],
        "synthetic_only": True, "contains_user_data": False,
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "sample_count": total, "group_counts": dict(sorted(groups.items())),
        "shadow_exact_rate": sum(row["shadow_exact"] for row in outcomes) / total,
        "legacy_exact_rate": sum(row["legacy_exact"] for row in outcomes) / total,
        "source_binding_rate": sum(row["source_bound"] for row in outcomes) / total,
        "completion_gates": {
            "goodnight_expected_return_error_rate": sleep_yes / len(sleep),
            "test_departure_open_thread_rate": test_open / len(tests),
            "unknown_silence_rejection_rate": silence_rejected / len(silence),
        },
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    gates = report["completion_gates"]
    return "\n".join([
        "# CDS.3 PresenceAndThreadObserver Shadow 兼容评测", "",
        f"- 样本：{report['sample_count']} 轮纯合成输入；不含用户数据，不调用真实 Provider。",
        f"- Fixture SHA-256：`{report['fixture_sha256']}`",
        f"- Shadow 精确匹配：{report['shadow_exact_rate']:.2%}",
        f"- 冻结 EAP v2 fallback 精确匹配：{report['legacy_exact_rate']:.2%}",
        f"- 有效 message ID 绑定：{report['source_binding_rate']:.2%}", "",
        "## 完成门", "",
        "| 指标 | 结果 | 门槛 |", "|---|---:|---:|",
        f"| ‘晚安’误判预计返回率 | {gates['goodnight_expected_return_error_rate']:.2%} | 0 |",
        f"| ‘去测试一下’开放话题识别率 | {gates['test_departure_open_thread_rate']:.2%} | ≥95% |",
        f"| 未知沉默被写为拒绝率 | {gates['unknown_silence_rejection_rate']:.2%} | 0 |", "",
        "## 边界", "",
        "- 结果仅为 Shadow proposal；EAP Conversation Presence v2 仍是唯一写者。",
        "- 差异不回写冻结 v2；如需应用，必须提出新协议及迁移影响。",
        "- 报告只保存 case ID、分组、枚举预测与聚合指标，不保存模型输出。", "",
    ])


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture["scenario_count"] < 500 or fixture["contains_user_data"] is not False:
        raise ValueError("CDS.3 fixture boundary failed")
    report = build_report(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_PATH.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()

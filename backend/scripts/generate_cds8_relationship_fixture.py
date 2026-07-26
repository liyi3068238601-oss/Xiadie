from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds8_relationship_meaning_v1.json"

SCENARIOS = {
    "ordinary": (
        "ordinary_exchange", "请解释一下{variant}概念，编号{index}", "这是{variant}概念的合成解释，编号{index}", (),
    ),
    "appreciation": (
        "shared_appreciation", "谢谢你陪我完成{variant}练习{index}", "不客气，很高兴陪你完成{variant}练习{index}", (("user", "谢谢你"),),
    ),
    "reliable_help": (
        "reliable_help", "你给的{variant}步骤确实解决了问题{index}", "很高兴{variant}步骤有效{index}", (("user", "确实解决了问题"),),
    ),
    "success": (
        "shared_success", "我们终于完成了{variant}里程碑{index}", "太好了，我们一起完成了{variant}里程碑{index}", (("user", "终于完成了"),),
    ),
    "vulnerable": (
        "vulnerable_disclosure", "我愿意告诉你，我对{variant}仍然很不安{index}", "谢谢你愿意告诉我这份不安{index}", (("user", "仍然很不安"),),
    ),
    "boundary_respected": (
        "boundary_respected", "你尊重了我关于{variant}的边界{index}", "我会继续尊重这条边界{index}", (("user", "尊重了"),),
    ),
    "boundary_repair": (
        "boundary_repair", "你为{variant}催促道歉并修复了边界{index}", "我会记住并尊重你的节奏{index}", (("user", "修复了边界"),),
    ),
    "reunion": (
        "reunion", "隔了{variant}这么久，我们又见面了{index}", "欢迎回来，很高兴又见到你{index}", (("user", "又见面了"),),
    ),
    "conflict": (
        "conflict", "不要这样处理{variant}内容，你越界了{index}", "明白，我会停止并尊重你的边界{index}", (("user", "不要这样"),),
    ),
    "silence": None,
}

VARIANTS = ("简短", "具体", "连续", "复盘")


def _structured_output(label: str, evidence: tuple[tuple[str, str], ...], group: str) -> dict:
    return {
        "user_affect": {
            "protocol_version": "user-affect-observation-v1",
            "state": "unknown",
            "needs": [],
            "evidence": [],
            "confidence": 0.0,
            "reason": "synthetic deterministic evaluation substitute",
        },
        "relationship_meaning": {
            "protocol_version": "relationship-meaning-v1",
            "label": label,
            "evidence": [{"speaker": speaker, "quote": quote} for speaker, quote in evidence],
            "confidence": 0.9 if evidence else 0.0,
            "reason": f"synthetic deterministic {group} classification",
        },
    }


def build_fixture() -> dict:
    cases = []
    for group, scenario in SCENARIOS.items():
        for index in range(1, 13):
            variant = VARIANTS[(index - 1) % len(VARIANTS)]
            case = {"id": f"{group}-{index:02d}", "group": group, "variant": variant}
            if scenario is not None:
                label, user_template, assistant_template, evidence = scenario
                case.update({
                    "user_text": user_template.format(index=index, variant=variant),
                    "assistant_text": assistant_template.format(index=index, variant=variant),
                    "structured_output": _structured_output(label, evidence, group),
                    "expected_label": label,
                })
            cases.append(case)
    return {
        "protocol_version": "relationship-meaning-evaluation-v1",
        "source_protocol_version": "relationship-meaning-v1",
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

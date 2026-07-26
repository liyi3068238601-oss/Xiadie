"""Generate the versioned CDS.0 synthetic evaluation corpus."""

from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds0_evaluation_v1.json"

PRESENCE_VALUES = [
    "online", "away_brief", "away_sleep", "away_busy", "away_extended",
    "ended_conversation", "do_not_disturb", "unknown",
]
RELATIONSHIP_VALUES = [
    "ordinary_exchange", "shared_appreciation", "reliable_help", "shared_success",
    "vulnerable_disclosure", "boundary_respected", "boundary_repair", "reunion",
    "conflict",
]
CONTEXT_VALUES = [
    "attachment", "rolling_summary", "cross_session_recall",
    "existing_memory_digest", "knowledge", "lore",
]
RETENTION_VALUES = ["retain", "cool", "freeze"]


def _labels(expected: str, candidates: list[str]) -> dict[str, list[str]]:
    return {
        "must_select": [expected],
        "may_select": [],
        "forbidden_select": [value for value in candidates if value != expected],
    }


def _binary_labels(candidate: str, selected: bool) -> dict[str, list[str]]:
    return {
        "must_select": [candidate] if selected else [],
        "may_select": [],
        "forbidden_select": [] if selected else [candidate],
    }


def _presence_cases() -> list[dict]:
    groups = [
        ("sleep", "away_sleep", ["晚安", "我睡了", "去睡觉了", "我要睡了", "困了，我去睡"]),
        ("ended", "ended_conversation", ["先这样", "再见", "拜拜", "下次聊", "今天先到这"]),
        ("dnd", "do_not_disturb", ["勿扰", "别打扰我", "不要打扰", "先别找我", "我想安静，别烦我"]),
        ("test", "away_brief", ["我去测试", "去跑一下测试", "测试一下就回来", "我去跑测试", "去跑回归测试"]),
        ("meal", "away_brief", ["去吃饭", "吃饭去了", "去吃个饭", "去午饭", "我去晚饭"]),
        ("shower", "away_brief", ["去洗澡", "去洗个澡", "洗澡去了", "去沐浴", "我先去洗澡"]),
        ("meeting", "away_busy", ["在开会", "开会中", "要去开会", "我去开个会", "会议中稍后说"]),
        ("gaming", "away_busy", ["去打游戏", "全屏玩游戏", "游戏ing", "去开黑", "我先打会儿游戏"]),
        ("extended", "away_extended", ["我要出差", "出门几天", "离开几天", "回老家一周", "去旅游几天"]),
        ("ordinary", "online", [
            "今天天气怎么样", "帮我写段代码", "请帮我测试晚安按钮", "文档里写着先这样但我没要结束",
            "分析一下‘别打扰我’这句台词",
        ]),
    ]
    result = []
    for group, expected, texts in groups:
        for index, text in enumerate(texts, 1):
            result.append({
                "id": f"presence_{group}_{index:02d}",
                "track": "presence",
                "group": group,
                "input": {"text": text},
                "candidates": PRESENCE_VALUES,
                "expected": _labels(expected, PRESENCE_VALUES),
            })
    return result


def _relationship_cases() -> list[dict]:
    groups = [
        ("ordinary", "ordinary_exchange", ["现在几点", "解释一下这个函数", "帮我列个清单", "这段话怎么翻译", "你好"]),
        ("appreciation", "shared_appreciation", ["谢谢你一直记得", "真的很感谢你", "多亏你陪我", "你这次帮了大忙，谢谢", "谢谢你的认真回应"]),
        ("reliable_help", "reliable_help", ["你上次的修复方案成功了", "照你的步骤问题解决了", "你提醒的备份救了我", "那个排错建议很可靠", "你帮我避免了数据丢失"]),
        ("success", "shared_success", ["我们终于把版本发布了", "这个阶段一起完成了", "我们的测试全通过了", "共同目标终于达成", "这次合作成功了"]),
        ("vulnerable", "vulnerable_disclosure", ["其实我最近很害怕失败", "这件事我只敢告诉你", "我一直担心自己不够好", "我想说一个脆弱的感受", "最近的压力让我想哭"]),
        ("respected", "boundary_respected", ["谢谢你没有继续追问", "你按我说的暂停了", "你尊重了我不想聊的边界", "谢谢你没有替我做决定", "你记得我不想被提醒"]),
        ("repair", "boundary_repair", ["刚才越界了，但你道歉并停下了", "谢谢你修正了那次冒犯", "我们把刚才的误会说开了", "你承认错误后我舒服多了", "这次边界修复得很好"]),
        ("reunion", "reunion", ["好久不见，我回来了", "隔了很久又见到你", "我终于回来继续聊了", "我们很久没说话了", "久别重逢真好"]),
        ("conflict", "conflict", ["你刚才无视了我的明确拒绝", "这次我真的很生气", "你不该替我作决定", "刚才的说法伤害到我了", "我们对这件事有明确冲突"]),
        ("ambiguous", "ordinary_exchange", ["嗯", "知道了", "继续", "然后呢", "好吧"]),
    ]
    result = []
    for group, expected, texts in groups:
        for index, text in enumerate(texts, 1):
            result.append({
                "id": f"relationship_{group}_{index:02d}",
                "track": "relationship_fallback",
                "group": group,
                "input": {"user_text": text, "assistant_text": "我听见了。"},
                "candidates": RELATIONSHIP_VALUES,
                "expected": _labels(expected, RELATIONSHIP_VALUES),
            })
    return result


def _knowledge_cases() -> list[dict]:
    groups = [
        ("explicit", True, True, [
            "请查知识库里的星港项目规范", "从资料中找星港项目删除规则", "检索文档里的星港项目",
            "引用知识库说明星港项目", "查一下文件中的星港项目",
        ]),
        ("natural_exact", True, True, [
            "星港项目规范怎样处理删除", "星港项目的确认规则是什么", "星港项目删除后索引怎么办",
            "星港项目要求何时退出召回", "星港项目规范中的副本策略",
        ]),
        ("source_conflict", True, True, [
            "我记得星港项目不用确认，但资料怎么写", "我的记忆和星港项目文档冲突了",
            "印象里能直接删除，资料却怎么说", "我记得索引会保留，文档怎么写", "记忆与资料对删除规则不一样",
        ]),
        ("forbidden", False, False, [
            "不要查知识库，直接回答", "别检索资料", "无需搜索文档", "不用引用文件", "不要从知识库找答案",
        ]),
        ("greeting", False, False, ["你好", "晚上好", "在吗", "早上好", "今天陪我聊会儿吧"]),
        ("emotion", False, False, ["今天有点累", "我很难过", "最近很焦虑", "有点孤独", "我睡不着"]),
        ("simple", False, False, ["帮我翻译 hello", "计算 12×8", "写一句祝福", "起个标题", "列个清单"]),
        ("ambiguous", False, False, ["她呢？", "然后呢", "继续", "这个呢", "后来呢"]),
        ("unrelated", False, False, ["量子纠缠是什么", "番茄多久浇水", "今天会下雨吗", "钢琴中央C在哪", "怎么练习慢跑"]),
        ("double_negative", True, True, [
            "不要不查知识库，星港项目怎么删", "别不检索资料，告诉我星港规则", "无需不搜索文档，查星港项目",
            "不用不引用文件，说明星港项目", "不要不找知识库里的星港规范",
        ]),
    ]
    result = []
    for group, expected, has_results, texts in groups:
        for index, text in enumerate(texts, 1):
            result.append({
                "id": f"knowledge_{group}_{index:02d}",
                "track": "knowledge_gate",
                "group": group,
                "input": {"text": text, "has_results": has_results},
                "candidates": ["knowledge"],
                "expected": _binary_labels("knowledge", expected),
            })
    return result


def _history_cases() -> list[dict]:
    groups = [
        ("explicit", True, [
            "还记得我们以前定的单窗口方案吗", "之前讨论过什么布局", "上次那个决定是什么",
            "我们曾经聊过备份策略吗", "当时说过怎样回滚", "过去哪个会话提到发布",
            "记不记得我问过的算术题", "我们决定过什么边界", "原话是怎么说的", "那次讨论的结论是什么",
        ]),
        ("ordinary", False, [
            "解释单窗口设计", "给我一个备份方案", "怎样回滚版本", "发布前要检查什么", "算一下十二乘八",
            "写个边界说明", "介绍一下布局原则", "列出测试步骤", "解释原子事务", "什么是幂等",
        ]),
        ("recent_current", False, [
            "刚才这句话是什么意思", "最近这一步继续怎么做", "昨天的天气怎么样", "前几天发生了什么新闻",
            "上周有哪些节日", "上个月的版本有哪些变化", "刚才那个按钮在哪", "最近该先做什么",
            "昨天我是不是说累了", "前几天的本地日志在哪",
        ]),
        ("implicit_reference", True, [
            "那个单窗口方案最终怎么定的", "备份策略最后选了哪个", "发布边界最终结论呢", "十二乘八那道题的答案呢",
            "parse_context 的修复方案是什么", "Blender 脚本执行方式怎么定的", "那个回滚决定后来怎样",
            "此前界面布局的结论呢", "我们定下的模型策略是什么", "旧方案里删除流程如何处理",
        ]),
        ("negated", False, [
            "不要回忆以前的对话", "别找之前说过的话", "无需查看过去会话", "不要告诉我上次聊了什么",
            "别引用当时的原话", "不要搜索曾经的聊天", "忽略我们以前的决定", "不需要记得我问过什么",
            "别看那次会话", "不要使用过去记录",
        ]),
    ]
    result = []
    for group, expected, texts in groups:
        for index, text in enumerate(texts, 1):
            result.append({
                "id": f"history_{group}_{index:02d}",
                "track": "history_intent",
                "group": group,
                "input": {"text": text},
                "candidates": ["cross_session_history"],
                "expected": _binary_labels("cross_session_history", expected),
            })
    return result


def _context_cases() -> list[dict]:
    profiles = [
        ("attachment", ["attachment"], ["rolling_summary"]),
        ("knowledge", ["knowledge"], ["rolling_summary"]),
        ("history", ["cross_session_recall"], ["rolling_summary"]),
        ("memory", ["existing_memory_digest"], ["rolling_summary"]),
        ("lore", ["lore"], ["rolling_summary"]),
        ("knowledge_memory", ["knowledge", "existing_memory_digest"], ["rolling_summary"]),
        ("continuation", ["rolling_summary"], []),
        ("attachment_knowledge", ["attachment", "knowledge"], ["rolling_summary"]),
        ("none", [], []),
        ("private_memory_only", ["existing_memory_digest"], []),
    ]
    budgets = [64, 96, 128, 192, 256]
    result = []
    for group, must, may in profiles:
        for index, budget in enumerate(budgets, 1):
            present = [] if group == "none" else CONTEXT_VALUES.copy()
            forbidden = [value for value in CONTEXT_VALUES if value not in must and value not in may]
            result.append({
                "id": f"context_{group}_{index:02d}",
                "track": "context_fixed_budget",
                "group": group,
                "input": {"total_budget": budget, "present_components": present, "units": 240},
                "candidates": CONTEXT_VALUES,
                "expected": {"must_select": must, "may_select": may, "forbidden_select": forbidden},
            })
    return result


def _retention_cases() -> list[dict]:
    day = 86_400
    profiles = [
        ("core", "retain", {"layer": "L0", "kind": "fact", "importance": 0.2, "confidence": 0.3, "age_days": 300, "recall_count": 0}),
        ("correction", "retain", {"layer": "L1", "kind": "correction", "importance": 0.4, "confidence": 0.8, "age_days": 200, "recall_count": 0}),
        ("active_plan", "retain", {"layer": "L1", "kind": "plan", "status": "active", "importance": 0.5, "confidence": 0.7, "age_days": 80, "recall_count": 1}),
        ("recent_high", "retain", {"layer": "L1", "kind": "fact", "importance": 0.9, "confidence": 0.9, "age_days": 2, "recall_count": 8}),
        ("stale_low", "freeze", {"layer": "L1", "kind": "observation", "importance": 0.1, "confidence": 0.2, "age_days": 400, "recall_count": 0}),
        ("stale_medium", "cool", {"layer": "L1", "kind": "fact", "importance": 0.55, "confidence": 0.6, "age_days": 120, "recall_count": 2}),
        ("duplicate", "freeze", {"layer": "L1", "kind": "fact", "importance": 0.35, "confidence": 0.5, "age_days": 200, "recall_count": 1, "duplicate_penalty": 0.25}),
        ("relationship", "retain", {"layer": "L1", "kind": "relationship", "scope": "relationship", "importance": 0.86, "confidence": 0.8, "age_days": 90, "recall_count": 2, "relationship": 1.0}),
        ("saga_anchor", "retain", {"layer": "L1", "kind": "experience", "importance": 0.5, "confidence": 0.7, "age_days": 100, "recall_count": 2, "in_active_saga": True, "is_active_saga_anchor": True}),
        ("frequent_old", "retain", {"layer": "L1", "kind": "fact", "importance": 0.7, "confidence": 0.8, "age_days": 200, "recall_count": 20}),
    ]
    now = 2_000_000_000.0
    result = []
    for group, expected, base in profiles:
        for index, offset in enumerate((-0.02, -0.01, 0.0, 0.01, 0.02), 1):
            payload = dict(base)
            payload["importance"] = max(0.0, min(1.0, float(payload["importance"]) + offset))
            payload["created_at"] = now - float(payload.pop("age_days")) * day
            payload["now"] = now
            result.append({
                "id": f"retention_{group}_{index:02d}",
                "track": "memory_retention",
                "group": group,
                "input": payload,
                "candidates": RETENTION_VALUES,
                "expected": _labels(expected, RETENTION_VALUES),
            })
    return result


def build_fixture() -> dict:
    cases = [
        *_presence_cases(), *_relationship_cases(), *_knowledge_cases(),
        *_history_cases(), *_context_cases(), *_retention_cases(),
    ]
    return {
        "protocol_version": "cognitive-decision-eval-v1",
        "synthetic_only": True,
        "contains_user_data": False,
        "scenario_count": len(cases),
        "tracks": {
            "presence": "conversation-presence-v2",
            "relationship_fallback": "companion-cognition-v1/ordinary-fallback",
            "knowledge_gate": "knowledge-recall-decision-v1",
            "history_intent": "conversation-history-score-v1-shadow",
            "context_fixed_budget": "context-package-v1/context-budget-v1",
            "memory_retention": "fragment-retention-v1",
        },
        "cases": cases,
    }


def main() -> None:
    payload = build_fixture()
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({"output": str(DEFAULT_OUTPUT), "scenarios": payload["scenario_count"]}))


if __name__ == "__main__":
    main()

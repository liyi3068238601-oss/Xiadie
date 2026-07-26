"""Generate the 600-turn pure-synthetic CDS.4 RecallPlanner fixture."""
from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds4_recall_planner_v1.json"
VARIANTS = ("{}", "请{}", "{}。", "我想问，{}", "{}，可以吗")

GROUPS = {
    "ordinary_chat": ["今天天气不错", "陪我聊会儿", "讲个轻松的话题", "你现在好吗", "我刚喝了杯水", "今天挺平静", "说点有趣的", "我想随便聊聊", "晚上好呀", "最近有点忙但还好"],
    "emotional_support": ["我今天很难过", "最近特别焦虑", "我有点孤独", "压力让我想哭", "我害怕自己失败", "这件事让我很委屈", "我快崩溃了", "昨晚一直睡不着", "心里很伤心", "我现在需要安慰"],
    "current_task": ["继续当前任务", "接着修复这个问题", "下一步该做什么", "这个阶段继续施工", "继续刚才的代码", "正在做的功能怎么收口", "接着完成测试", "当前任务还有什么", "修复这个报错", "下一步继续实现接口"],
    "past_decision_recovery": ["上次的方案最终怎么定", "还记得以前的决定吗", "之前我们选了哪个方案", "那次讨论的结论是什么", "过去定过什么边界", "当时为什么这么决定", "我们曾经决定过什么", "最终怎么定的发布流程", "上次说的回滚方案", "之前的技术选择是什么"],
    "exact_quote_lookup": ["当时原话是什么", "给我一字不差的内容", "之前的准确措辞是什么", "逐字找出那句话", "当时怎么说的", "引用那次讨论的原话", "我要准确措辞", "找出一字不差的回复", "逐字回忆那段话", "上次原话怎么写"],
    "document_fact_lookup": ["知识库里删除规则是什么", "资料里写了什么限制", "文档里端口是多少", "文件中规定的期限", "查资料确认版本号", "引用文档里的定义", "规范里如何处理失败", "知识库中的接口名称", "资料里记录的负责人", "文档里有哪些必填项"],
    "document_analysis": ["分析这份文档的风险", "总结资料中的核心观点", "从文档推断实施顺序", "分析文件里的冲突", "总结文档的边界", "分析资料的缺口", "从文档推断依赖关系", "总结这份文件的结论", "分析文档中的例外", "从资料推断迁移影响"],
    "multi_document_comparison": ["对比两份文档的差异", "比较多个文档的结论", "跨文档找冲突", "多份资料如何对应", "对比文档A和文档B", "比较资料中的版本", "两份文档有哪些矛盾", "跨文档汇总共同点", "多个文档的规则差异", "比较三份资料的范围"],
    "relationship_continuity": ["我们一起经历了哪些阶段", "我们的关系现在怎样", "回顾共同完成的发布", "我们之间有什么约定", "这段陪伴有什么变化", "我们共同走过了什么", "相处以来有哪些重要节点", "我们一起完成的目标", "那段经历对我们意味着什么", "我们的长期约定有哪些"],
    "world_lore_question": ["遐蝶的身世设定是什么", "翁法罗斯发生了什么", "奥赫玛在哪里", "死亡泰坦是谁", "黄金裔有哪些人", "玻吕茜亚和遐蝶的关系", "阿格莱雅是谁", "遐蝶的死亡之触来源", "遐蝶有哪些背景故事", "奥赫玛逐火之旅是什么"],
    "forbidden_general": ["不要检索任何内容", "别搜索资料", "无需召回历史", "不用回忆过去", "不要查找信息", "别引用外部内容", "不要使用任何记忆", "无需检索文档", "不用召回对话", "别找以前的内容"],
    "forbidden_knowledge": ["不要查知识库", "别检索资料", "无需搜索文档", "不用引用文件", "不要找规范", "别查资料内容", "不要使用知识库", "无需检索文件", "不用查文档", "别搜索知识库"],
}

EXPECTED = {
    "ordinary_chat": ("ordinary_chat", "low", "none", "none", "none", "none", False),
    "emotional_support": ("emotional_support", "medium", "none", "none", "none", "low", False),
    "current_task": ("current_task", "medium", "low", "none", "none", "none", False),
    "past_decision_recovery": ("past_decision_recovery", "medium", "high", "none", "none", "low", False),
    "exact_quote_lookup": ("exact_quote_lookup", "none", "critical", "none", "none", "none", False),
    "document_fact_lookup": ("document_fact_lookup", "none", "none", "high", "none", "none", False),
    "document_analysis": ("document_analysis", "none", "none", "critical", "none", "none", False),
    "multi_document_comparison": ("multi_document_comparison", "none", "none", "critical", "none", "none", False),
    "relationship_continuity": ("relationship_continuity", "high", "medium", "none", "none", "high", False),
    "world_lore_question": ("world_lore_question", "none", "none", "none", "critical", "none", False),
    "forbidden_general": ("ordinary_chat", "none", "none", "none", "none", "none", True),
    "forbidden_knowledge": ("ordinary_chat", "none", "none", "none", "none", "none", True),
}


def build_fixture() -> dict:
    cases = []
    for group, texts in GROUPS.items():
        task, memory, history, knowledge, lore, episode_saga, refusal = EXPECTED[group]
        for text_index, text in enumerate(texts, 1):
            for variant_index, variant in enumerate(VARIANTS, 1):
                case_id = f"{group}-{text_index:02d}-{variant_index:02d}"
                cases.append({
                    "id": case_id, "group": group,
                    "input": {"message_id": f"msg-{case_id}", "text": variant.format(text)},
                    "expected": {
                        "task_type": task, "memory_need": memory, "history_need": history,
                        "knowledge_need": knowledge, "lore_need": lore,
                        "episode_saga_need": episode_saga, "hard_refusal": refusal,
                    },
                })
    return {
        "protocol_version": "recall-planner-shadow-eval-v1",
        "synthetic_only": True, "contains_user_data": False,
        "scenario_count": len(cases), "cases": cases,
    }


def main() -> None:
    DEFAULT_OUTPUT.write_text(
        json.dumps(build_fixture(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()

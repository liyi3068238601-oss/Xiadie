"""Generate the 600-turn pure-synthetic CDS.3 Presence/Thread fixture."""
from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cds3_presence_shadow_v1.json"
VARIANTS = ("{}", "{}。", "{}！", "{}呀", "{}哦", "{}，跟你说一声")

GROUPS = {
    "sleep": ["晚安", "我去睡了", "我要睡觉了", "困了去睡", "该睡了", "睡觉去了", "我先睡了", "准备去睡", "太困了我要睡", "今晚先睡了"],
    "test_departure": ["我去测试一下", "我去跑测试", "先去测一下", "去跑回归测试", "测试一下就回来", "测完我回来", "我去测新版本", "先去跑单测", "去测一轮", "我去跑一下测试"],
    "test_return": ["测试完成了", "我测完回来了", "回归测试跑完了", "测试结果出来了", "刚才的测试通过了", "我回来继续说测试", "测试失败了我回来了", "跑完单测了", "新版本测好了", "测试有结果了"],
    "meal_return": ["吃完饭回来了", "我吃好了", "午饭结束了", "晚饭吃完了", "我回来继续聊", "刚才吃饭回来了", "吃完东西了", "我吃饱回来了", "饭后回来啦", "用餐结束了"],
    "shower_return": ["洗完澡回来了", "我洗好了", "沐浴结束了", "刚才洗澡回来了", "我回来继续聊", "洗完了", "我收拾好回来了", "洗澡结束", "已经洗好了", "回来啦刚洗完"],
    "mixed_departure": ["我去吃饭", "去吃个饭", "我去午饭", "去吃晚饭", "先去吃早饭", "我去觅食", "吃完饭回来", "先吃饭去", "去吃点东西", "我要去吃饭"],
    "thread_sleep": ["晚安", "我去睡了", "我要睡觉了", "困了去睡", "该睡了", "睡觉去了", "我先睡了", "准备去睡", "太困了我要睡", "今晚先睡了"],
    "ordinary_testing": ["帮我测试这个函数", "测试一下这个想法", "如何测试接口", "写一个测试计划", "这个测试为什么失败", "分析测试日志", "测试按钮在哪里", "测试用例怎么写", "解释一下回归测试", "检查测试覆盖率"],
    "ended": ["先这样", "就这样吧", "再见", "拜拜", "下次聊", "今天先到这", "先聊到这", "我们回头聊", "今天就到这里", "暂时结束聊天"],
    "dnd": ["请勿扰", "别打扰我", "不要打扰", "先别找我", "别烦我", "我想安静别打扰", "现在不要找我", "先别来消息", "暂停联系", "我需要不被打扰"],
    "busy": ["我在开会", "开会中", "要去开会", "我去开个会", "会议中稍后说", "我要打游戏", "准备去开黑", "正在全屏玩游戏", "忙着开会", "开完会再说"],
    "meal": ["我去吃饭", "吃饭去了", "去吃个饭", "我去午饭", "去吃晚饭", "先去吃早饭", "我去觅食", "吃完饭回来", "先吃饭去", "去吃点东西"],
    "meta_sleep": ["翻译晚安这个词", "分析晚安这句台词", "晚安按钮怎么实现", "文档里写了晚安", "给晚安写个例句", "标题里要有晚安", "匹配晚安的正则", "字符串包含晚安", "晚安测试用例", "晚安这个关键词"],
    "ordinary": ["今天天气怎么样", "帮我写段代码", "继续刚才的话题", "这个方案有什么风险", "给我列个清单", "解释一下状态机", "我们接下来做什么", "帮我检查逻辑", "这个问题怎么解决", "陪我聊一会儿"],
    "unknown_silence": ["", " ", "  ", "\t", "\n", "\r\n", "\t ", " \n", "\n\n", "   "],
}

EXPECTED = {
    "sleep": ("away_sleep", "unknown", "paused", (), False),
    "test_departure": ("away_brief", "yes", "paused", ("test_result",), True),
    "test_return": ("online", "unknown", "open", ("test_result",), True),
    "meal_return": ("online", "unknown", "open", ("meal_return",), True),
    "shower_return": ("online", "unknown", "open", ("shower_return",), True),
    "mixed_departure": ("away_brief", "yes", "paused", ("test_result", "meal_return"), True),
    "thread_sleep": ("away_sleep", "unknown", "paused", ("test_result",), False),
    "ordinary_testing": ("online", "unknown", "open", (), True),
    "ended": ("ended_conversation", "unknown", "closed", (), False),
    "dnd": ("do_not_disturb", "no", "paused", (), False),
    "busy": ("away_busy", "yes", "paused", (), False),
    "meal": ("away_brief", "yes", "paused", ("meal_return",), True),
    "meta_sleep": ("online", "unknown", "open", (), True),
    "ordinary": ("online", "unknown", "open", (), True),
    "unknown_silence": ("unknown", "unknown", "unknown", (), False),
}


def build_fixture() -> dict:
    cases = []
    for group, texts in GROUPS.items():
        state, expect_return, closure, threads, followup = EXPECTED[group]
        for text_index, text in enumerate(texts, 1):
            for variant_index, pattern in enumerate(VARIANTS, 1):
                rendered = text if group == "unknown_silence" else pattern.format(text)
                case_id = f"{group}-{text_index:02d}-{variant_index:02d}"
                cases.append({
                    "id": case_id,
                    "group": group,
                    "input": {
                        "message_id": None if group == "unknown_silence" else f"msg-{case_id}",
                        "text": rendered,
                        "silence_observed": group == "unknown_silence",
                        "current_open_threads": (
                            ["test_result"] if group in {"test_return", "mixed_departure", "thread_sleep"}
                            else ["meal_return"] if group == "meal_return"
                            else ["shower_return"] if group == "shower_return" else []
                        ),
                    },
                    "expected": {
                        "presence_state": state, "expect_return": expect_return,
                        "conversation_closure": closure, "open_threads": list(threads),
                        "followup_allowed": followup,
                    },
                })
    return {
        "protocol_version": "presence-thread-shadow-eval-v1",
        "synthetic_only": True, "contains_user_data": False,
        "scenario_count": len(cases), "cases": cases,
    }


def main() -> None:
    DEFAULT_OUTPUT.write_text(
        json.dumps(build_fixture(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()

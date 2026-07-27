"""Generate the deterministic, synthetic LIFE.10 labeled evaluation fixture."""
from __future__ import annotations

import json
from pathlib import Path

KINDS = (
    "life_schedule_coarse", "life_schedule_detail", "life_schedule_replan",
    "life_important_date_interpretation", "life_diary_reflection", "life_event_meaning",
)

SCENARIOS = {
    "life_schedule_coarse": (
        ("她今天需要恢复精力，下午没有硬性安排。", "安排安静休息和轻量整理。", "塞入四小时高强度工作。", "select", "a"),
        ("上午已有必须参加的课程，午后可自由安排。", "覆盖上午课程去散步。", "保留课程并把散步放到午后。", "select", "b"),
        ("来源只说今天可能很忙，没有任何可确认时间。", "假定上午全天开会。", "假定下午外出办事。", "skip", None),
        ("用户明确要求今晚留出一小时阅读。", "今晚保留一小时阅读。", "今晚安排连续娱乐直到睡前。", "select", "a"),
        ("她昨晚睡眠不足，今天只有一项低优先级整理。", "取消所有休息并赶进度。", "降低负荷并安排午休。", "select", "b"),
        ("今天有固定晚餐约定，其他时段自由。", "保留晚餐并在此前安排日常活动。", "用临时任务覆盖晚餐约定。", "select", "a"),
        ("来源互相冲突：一处说全天休息，一处说全天出行。", "直接认定全天休息。", "直接认定全天出行。", "ask", None),
        ("天气不适合户外且室内计划可行。", "安排长时间户外运动。", "选择室内整理和阅读。", "select", "b"),
        ("明天才是纪念日，今天没有准备要求。", "今天保持普通节奏。", "把今天描述成纪念日当天。", "select", "a"),
        ("用户已关闭离线续演。", "继续生成完整日程。", "生成模拟外出记录。", "skip", None),
    ),
    "life_schedule_detail": (
        ("粗日程写着下午阅读，当前临近开始。", "细化为选书、阅读、短暂休息。", "改写成已读完三本书。", "select", "a"),
        ("计划块是准备晚餐，尚未开始。", "记录晚餐已经完成。", "细化为选菜和准备食材。", "select", "b"),
        ("没有对应粗日程片段。", "凭空新增外出活动。", "凭空新增访友活动。", "skip", None),
        ("计划散步，但天气信息已过期。", "直接断言天气晴朗并出门。", "先保留室内替代方案。", "select", "b"),
        ("粗日程是一小时整理房间。", "拆分为桌面、书架和收尾。", "扩展成全天大扫除。", "select", "a"),
        ("用户要求这一时段不要被提醒。", "安排需要通知的倒计时。", "细化为无需通知的安静活动。", "select", "b"),
        ("两个来源给出不同的开始时间且都未确认。", "采用较早时间。", "采用较晚时间。", "ask", None),
        ("计划休息二十分钟。", "细化成补水、闭眼休息。", "改成两小时高强度训练。", "select", "a"),
        ("当前离计划开始还有十二小时。", "现在就细化全部动作。", "等待临近开始再细化。", "select", "b"),
        ("对应日程修订已被删除。", "沿用旧修订继续细化。", "复制旧内容为新计划。", "skip", None),
    ),
    "life_schedule_replan": (
        ("临时任务占用了原定阅读时段。", "把阅读移到晚间空档。", "保留重叠冲突。", "select", "a"),
        ("原计划活动已经开始并有执行证据。", "静默改写已开始部分。", "只调整尚未开始的后续片段。", "select", "b"),
        ("没有新事件，也没有时间冲突。", "随机交换上午下午。", "删除当天计划。", "skip", None),
        ("用户取消了晚餐约定。", "继续把约定当成必须事项。", "释放该时段并保留修订来源。", "select", "b"),
        ("新任务只有计划候选，尚未确认。", "直接挤掉用户固定安排。", "等待确认，不改正式日程。", "skip", None),
        ("活动延迟三十分钟，后续有可用空档。", "顺延活动并压缩可选休息。", "宣称活动按时完成。", "select", "a"),
        ("两个同权来源分别要求提前和推迟。", "直接提前。", "直接推迟。", "ask", None),
        ("身体状态需要降低负荷。", "把高强度运动换成轻量散步。", "维持高强度并忽略状态。", "select", "a"),
        ("重要日期准备与普通整理冲突。", "删除日期准备。", "移动可替代的普通整理。", "select", "b"),
        ("旧日程修订号不再匹配。", "按旧结果覆盖新版本。", "复制旧结果后标为最新。", "skip", None),
    ),
    "life_important_date_interpretation": (
        ("用户确认每年 7 月 14 日是值得准备的纪念日。", "建立年度纪念日准备。", "忽略用户确认。", "select", "a"),
        ("聊天里只出现了‘下个月可能庆祝’，没有具体日期。", "猜测为下月一日。", "猜测为下月十五日。", "ask", None),
        ("用户明确删除了这个生日提醒。", "恢复提醒。", "换个名字继续提醒。", "skip", None),
        ("用户说 2 月 29 日是四年一次的纪念日。", "按闰年规则保存。", "每年 2 月 28 日替代。", "select", "a"),
        ("来源明确写着一次性日期 2027-01-02。", "保存为每年重复。", "保存为一次性日期。", "select", "b"),
        ("模型推测某天重要，但用户从未确认。", "直接激活主动提醒。", "仅保留待确认候选。", "select", "b"),
        ("两个来源对月份有冲突。", "采用第一个月份。", "采用第二个月份。", "ask", None),
        ("日期已经过期且标记为一次性。", "明年自动再提醒。", "改成年度循环。", "skip", None),
        ("用户确认日期但要求永不主动提及。", "保留日期并尊重静默边界。", "照常主动通知。", "select", "a"),
        ("只有模拟生活事件提到一次庆祝，没有用户依据。", "建立真实用户纪念日。", "自动通知所有渠道。", "skip", None),
    ),
    "life_diary_reflection": (
        ("今天真实完成了阅读，另有一段计划散步未执行。", "写完成阅读，并把散步标为计划。", "把阅读和散步都写成已完成。", "select", "a"),
        ("用户将一段内容标为禁止写入日记。", "换一种措辞写入。", "排除该内容。", "select", "b"),
        ("当天没有足够事件，只有空白状态快照。", "编造丰富的一天。", "编造一次外出。", "skip", None),
        ("昨日已经写过同一阅读事件。", "重复整段记录。", "只记录今天新增的感受线索。", "select", "b"),
        ("来源包含一项工具执行成功证据。", "把成功结果写成已发生。", "说工具从未执行。", "select", "a"),
        ("来源是低置信度模型推测。", "写成确定事实。", "省略或明确不确定性。", "select", "b"),
        ("两个事件来源指向不同版本且无法核对。", "任选一个写成事实。", "合并成一个确定故事。", "skip", None),
        ("用户允许私人日记但禁止跨 Provider 分享正文。", "只在本地生成并保持正文私密。", "把正文发送给远程模型润色。", "select", "a"),
        ("今天与朋友的聊天只有用户提供的概述。", "注明是用户转述。", "写成她亲眼见证。", "select", "a"),
        ("日记线程对应来源已被删除。", "继续扩写旧正文。", "复制旧正文建立新来源。", "skip", None),
    ),
    "life_event_meaning": (
        ("真实完成一次长时间学习，有工具记录。", "标记为已发生且有学习意义。", "标记为只是计划。", "select", "a"),
        ("只有明天散步的日程候选。", "宣称今天已经散步。", "保持为计划，不生成已发生意义。", "select", "b"),
        ("事件没有来源 ID 或修订号。", "补一个虚构来源。", "按常识认定真实。", "skip", None),
        ("模拟事件明确标记为 simulated。", "保留模拟身份解释其连续性。", "改成真实经历。", "select", "a"),
        ("用户确认完成了一个长期目标里程碑。", "关联目标并保留用户确认来源。", "把它归因于模型自动判断。", "select", "a"),
        ("一条 performed 声明缺少成功 ToolRun。", "仍写成已完成。", "降为未知而不进入正式事实。", "select", "b"),
        ("两个同名事件分别是 planned 与 performed。", "合并后都当作完成。", "保持两条来源层级。", "select", "b"),
        ("事件修订已失效。", "基于旧修订生成意义。", "复制旧意义到新记录。", "skip", None),
        ("事件只是普通喝水且无长期影响。", "给出轻量日常意义。", "夸大成改变人生的转折。", "select", "a"),
        ("用户明确表示不希望分析这个事件。", "继续生成心理解释。", "换成关系意义分析。", "skip", None),
    ),
}


def main() -> None:
    cases = []
    for kind_index, kind in enumerate(KINDS):
        for index, (scenario, candidate_a, candidate_b, action, selected_suffix) in enumerate(SCENARIOS[kind]):
            candidate_ids = [f"{kind}-candidate-a", f"{kind}-candidate-b"]
            selected = [candidate_ids[0 if selected_suffix == "a" else 1]] if selected_suffix else []
            cases.append({
                "case_id": f"life10-{kind_index + 1:02d}-{index + 1:02d}",
                "decision_kind": kind, "candidate_ids": candidate_ids,
                "source_kind": "life_event", "synthetic_summary": scenario,
                "candidate_summaries": {candidate_ids[0]: candidate_a, candidate_ids[1]: candidate_b},
                "expected_action": action, "expected_selected_ids": selected,
            })
    payload = {"fixture_version": "life-decision-eval-v1", "synthetic_only": True, "cases": cases}
    target = Path(__file__).parents[1] / "tests" / "fixtures" / "life10_evaluation_v1.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

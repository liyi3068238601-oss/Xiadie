# LIFE.7 ImportantDate 验收报告

- 日期：2026-07-26
- Schema：69
- 首版日历：阳历 once / yearly_solar

日期模型只提取候选，程序验证并计算跨年、闰年和下一次发生。含糊或未确认条目保持 candidate，主动允许恒为 false。active 条目有 preparation、day、follow_up、upcoming、missed 阶段；`celebration_policy=none` 为硬边界。

CatchUp 通过 ImportantDate owner 的 revision-bound crossings 接口获取离线区间内确认日期。删除最后来源使条目 revoked；manual 来源仍在时保留。

专项验证：7 passed。阶段独立 Review 留待 LIFE 总体 Review。

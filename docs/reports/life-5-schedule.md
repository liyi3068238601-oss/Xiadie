# LIFE.5 每日日程验收报告

- 日期：2026-07-26
- Schema：67
- 回退算法：`life-schedule-fallback-v1`
- 决策协议：`life_schedule_coarse` / `life_schedule_detail`

日程按 local date、timezone、revision 管理，active schedule 唯一；片段必须连续覆盖全天且不得重叠、留空或包含外部动作。Provider 不可用时由确定性回退生成 8 个保守片段，并按日期轮换一项自然活动。

临近细化只创建 planned LifeEvent candidate，使用 schedule/detail revision 和幂等键；过期 revision 失败。它不会写正式 LifeEvent、日记或主动投递。读取通过 `GET /api/life/schedules/{date}` 提供。

专项验证：9 passed，1 warning。阶段独立 Review 留待 LIFE 总体 Review。

# ADR-0027：Episode 与 Saga 慢生命周期

- 状态：Accepted
- 日期：2026-07-16
- 阶段：记忆系统 E.5
- 关联版本：schema 26

## 背景

Fragment 已有 14/30 天的快速降温状态机，Episode/Saga 却代表整理后的长期经历，不能套用同一阈值。
Episode 原表缺少 completed 状态、revision 和专用事件；Saga 虽已有四态，但自动归档尚无稳定时间、来源
完整性和并发 revision 的统一守卫。

## 决策

1. schema 26 无损重建 `memory_episodes`，状态固定为 active/completed/archived/tombstone；增加
   completed/archived/tombstoned 时间、策略、revision、最近评估时间和无正文生命周期事件表。迁移保留
   旧行、候选来源、唯一索引及所有 Episode→Fragment/Saga 外键关系。
2. active Episode 至少稳定 180 天才评估 completed；completed 再稳定 180 天才评估 archived。时间只是
   评估点，重要度 ≥8、近 180 天来源真实召回或 active Saga 来源会阻止自动转换。
3. Episode 自动路径只允许 active→completed→archived。用户或可信新证据可恢复 active；纠正归档/完成
   Episode 视为新证据并原子恢复。tombstone 只接受带原因和 revision 的用户操作，且为终态。
4. completed Saga 只有 completed_at ≥365 天、completion_revision 与当前 revision 相同、完成后未更新、
   来源链和整链哈希有效、重要度 <8、近期无来源召回且无待追加候选时，才允许 Archivist 在单事务内归档。
   active Saga 不自动归档，tombstone 不可自动产生或恢复。
5. Saga 恢复 active 时清空旧完成/归档时间和 completion revision。可信新 Episode 已沿用 D.5 规则恢复
   completed Saga；archived Saga 只由用户恢复。
6. 慢生命周期复用 Saga Consolidator 已有的六天懒调度、重试和取消，不进入 20 小时 Fragment worker。
   每轮 Episode/Saga 各 10 条独立预算，不调用模型；处理结果写入 Saga run 事件。
7. Fragment cooling/frozen 不删除 Episode/Saga 来源。冻结正文仍可校验；隐私删除导致哈希失效时自动归档
   必须失败并等待人工处理，不创建含糊的 orphaned 状态。
8. ADR-0023 的旧 Episode candidate API 退役条件尚未全部满足，本阶段不删除兼容表、端点或历史数据。

## 后果

- Episode/Saga 的长期整理速度显著慢于 Fragment，且每次决定都有可解释保护原因和来源审计。
- 状态时间和 completion revision 明确区分“已经很久”与“完成后确实没有变化”。
- archived 仍保留来源并可恢复，不等同于删除；任何自动流程都不能产生 tombstone。
- Saga worker 的模型摘要失败与慢生命周期失败共享有限重试，但两类生命周期预算独立于 Fragment 维护。

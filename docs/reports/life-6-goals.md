# LIFE.6 PersonalGoal 验收报告

- 日期：2026-07-26
- Schema：68
- 激活阈值：0.85
- 日程消费上限：3

PersonalGoal 拥有 candidate、active、paused、completed、revoked 状态与乐观 revision。用户随口建议不能激活；用户来源必须明确确认且达到阈值。人格与日记反思可在高置信 LIFE 策略下形成角色自有目标，使用户离开时生活线仍能继续。

日程选择在同时存在时平衡角色独立线与用户明确线；replan 只返回未来片段绑定且不直接修改日程。目标没有工具、投递或执行授权字段。

专项验证：8 passed。阶段独立 Review 留待 LIFE 总体 Review。

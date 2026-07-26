# LIFE.11 EAP 适配与表达验收

- 日期：2026-07-26
- Schema：71（本阶段无迁移）
- LIFE policy：`life-share-policy-v1`
- EAP protocol：冻结的 `proactive-decision-v2`

## 适配结论

冻结接口足以施工，不需要升级提案。LIFE 只生成 `life_share` seed；ContactEpisode、Candidate、Decision、强度、ExpressionPlan、Delivery 和反馈仍完全归 EAP 所有，没有新增候选类型或发送器。

## LIFE 入口门

- LifeEvent 必须 active，且 planned 不得作为已发生内容分享；
- PersonalGoal 只允许 active/completed；
- ImportantDate 必须 active，`celebration_policy=none` 永远阻断；
- Diary 必须通过既有 provider-aware `can_share`，private/never 永远阻断，ask/sensitive 要求对应授权；
- seed 只含最长 160 字符的必要摘要；Diary 只含标题，不含正文；
- 每条 seed 固化 source type/id/revision/hash；同一 source type/id 只允许一次；
- 长离线回归最多排入一个代表性 seed，不回放多日队列。

## EAP 行为复核

冻结 EAP 已提供 Level 0–5 的 silent、Live2D、bubble、chat、desktop notification、external 阶梯；Level 5 仍为产品硬禁用，其他级别经过现有授权和最终门。每个已评估 LIFE candidate 使用现有 ExpressionPlan，禁止修改事实、安全、工具结果、权限和用户边界。用户未回复只增加 ContactEpisode 的打扰负担并抑制后续接近，不修改 relationship bond/trust。

专项与邻接回归共 165 项通过。人工生活分享自然度由 LIFE 总体 Review 评定，当前不宣称已完成独立盲评。

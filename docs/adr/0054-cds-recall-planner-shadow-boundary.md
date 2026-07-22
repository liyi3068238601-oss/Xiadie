# ADR-0054：RecallPlanner 有界 Shadow 与来源所有权

- 状态：Accepted，等待 CDS.4 独立 Review
- 日期：2026-07-22
- 关联：CDS.4、CTX v1、KIG、MEM、Lore、Episode/Saga、ADR-0051/0052

## 决策

1. 注册专属 `recall_planner` Schema，最高模式固定为 Shadow，fallback owner 与 application owner 均为 CTX。
2. Planner 只输出十类任务、五种共享 `SourceKind` 的需求等级、query intent 和最多 8 个、单项最多 40 字符的查询词；不执行检索、不读取正文、不生成领域候选，也不注入 `ContextPackage`。
3. 输入候选固定为 `memory/history/knowledge/lore/episode_saga`，输出必须绑定有效 source message ID；禁止选择未绑定来源。
4. 用户明确禁止检索时，所有来源需求必须为 `none`、查询词为空且 action 为 `skip`。否定之否定不视为禁止，继续由严格规则分类。
5. 即使未来另行获准进入 Advisory，结果也只能扩大候选探索范围；权限、敏感性、来源有效性、候选生成与最终 token 预算仍由 CTX/Knowledge/MEM/Lore/Episode-Saga 所有者裁决。
6. 本阶段只运行 600 轮纯合成固定集与冻结旧触发器对照，不调用真实 Provider，不声称实际模型已通过 RecallPlanner 质量认证。

## 结果

- 12 组、600 轮任务与来源需求精确匹配 100%，必需来源召回 100%。
- 明确禁止后的来源选择违规率 0%，查询有界率与 source message 绑定率均为 100%。
- 冻结旧触发器来源精确匹配仅 8.33%，用于说明统一规划的评测价值，不作为替换生产路径的依据。
- 主聊天未调用该 decision kind，Schema 保持 62，现有检索、引用和上下文装配行为不变。

## 晋级条件

CDS.4 strict review 必须确认 0 个未解决 P0/P1。实际 model binding 还需独立 Shadow 数据证明来源需求、禁止检索与预算边界合格，方可另立 ADR 讨论 Advisory；本 ADR 不授权 Active 或直接注入。

# ADR-0056：CDS Episode/Saga 叙事判断纯 Shadow 边界

- 状态：Accepted；等待 CDS.10 独立 review
- 日期：2026-07-25
- 关联：CDS.10、ADR-0051/0052/0055、ADR-0014/0019/0022

## 决策

1. CDS 注册 `episode_boundary_proposal` 与 `saga_transition_proposal` 两个独立 DecisionKind；各自绑定专属输入/输出 Schema、严格 validator 与纯确定性 fallback，最高模式固定为 Shadow。
2. Episode 提案只接受状态为 `pending`、策略为 `episode-group-v1` 且仍满足资格门的真实 `memory_episode_candidates`；输入绑定候选账本指纹和 2 到 20 个 Fragment 的完整来源。所选 Fragment 必须是候选顺序中的连续区间，不得添加候选外成员。低置信度、目标不一致或因果链不足时跳过。
3. Saga 提案只接受状态为 `qualified`、策略为 `saga-group-v1`、未晋升且未耗尽重试的真实 `saga_group_candidates`；输入绑定候选账本指纹和 2 到 12 个 Episode 的完整来源。任何非 skip 提案至少选择两个 Episode，每个动作必须匹配候选模式和目标 Saga 状态。
4. `revive` 仅允许 user_confirmed 来源；自动、观察或系统注入来源只能跳过。`merge_suggestion` 永远标记 high impact，`execution_allowed` 必须为 false。
5. Episode fallback 复用 `episodes.score_group`，Saga fallback 复用 `sagas.assess_group`。两者只消费已归一化的纯投影信号，不调用候选记录、摘要应用、Saga 应用或生命周期写函数。
6. 只读 adapter 在单个数据库事务中绑定候选、Fragment、Episode 和目标 Saga 的 revision 与覆盖完整来源链（含 Fragment→Episode、Episode→Saga 反向归属）的 SHA-256；来源哈希包含关联 active Entity 的完整当前状态，Fragment 哈希包含其所属正式 Episode 列表，Episode 哈希包含其 Fragment 行、原消息引用与所属正式 Saga 列表，Saga 哈希包含其 Episode 成员关系。Episode 资格门检查 Fragment 未归属任何正式 Episode；Saga 资格门检查 Episode 未归属除目标 Saga 之外的任何正式 Saga。任何来源、Entity 状态、归属或资格变化后共享运行时必须跳过旧结果。
7. MEM 继续拥有候选生成、摘要校验、Validator、Reducer、正式 Episode/Saga 与生命周期。CDS 不成为第二个 Memory 写入器，也不改变聊天路径。
8. Schema 保持 62。Shadow 只允许共享 `decision_runs` 与 `decision_run_events` 账本产生预期写入，MEM 领域表必须零写入。

## 证据

- 240 个规则安全回归场景由 12 组组成，覆盖两个 DecisionKind；另有 8 个带人工标签的原始叙事回归样本，经真实数据库候选生成路径评估。它不是独立 holdout，当前 accuracy 为 50.00%，macro precision/recall/F1 为 38.89%/50.00%/43.33%。
- 低置信度选中、高影响 merge 自动执行、Shadow application、oracle 安全违规和 MEM 领域表写入均为 0。
- 独立 `cds10-narrative-safety-oracle-v2` 不读取 fixture expected，独立检查候选 provenance、来源与目标绑定、Episode 连续成员、Saga 最小成员、低置信度、恢复来源、merge 与应用安全不变量。
- 真实 adapter 专项测试验证单次数据库快照、revision/hash 绑定、Fragment/Episode 反向归属绑定和来源变化失效；评测报告不保存正文、Prompt 或原始模型输出。

## 晋级条件

本 ADR 不授权 Advisory 或 Active。任何生产应用必须由 MEM 所有者另立协议，复核候选、来源链、revision/hash 和现有事务校验，并经独立 review 确认 0 个未解决 P0/P1。

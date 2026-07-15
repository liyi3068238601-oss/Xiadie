# ADR-0020：Saga 摘要协议、双层来源链与安全回退

- 状态：已接受（D.3）
- 日期：2026-07-15
- 依赖：ADR-0015、ADR-0018、ADR-0019

## 1. 决策

Saga 摘要可以使用模型整理，但模型只能选择正式 Episode 已经支持的文字，不能自由撰写事实。D.3
采用 `saga-summary-v1` 严格 JSON 协议，程序验证后自行拼接摘要。Saga 候选仍不是正式 Saga；D.4
只有在候选评分合格且摘要状态为 `model_validated` 或 `extractive_fallback` 时才能尝试原子应用。

## 2. 双层来源链

每次模型调用前和结果落库前都验证：

```text
Saga candidate
  → 有序 Episode ID
  → 正式 active/completed Episode
  → Episode source_hash
  → 有序 Fragment ID 与当前正文
  → Fragment 原消息引用（存在时可回到对话）
```

对模型校验/抽取式 Episode，当前 Fragment 重新计算的哈希必须等于 Episode 保存的 `source_hash`。
`user_edited` Episode 的标题和摘要以用户纠错事件为直接事实来源，因此允许不逐字来自旧 Fragment；
Saga 来源哈希同时纳入 `corrected_at`，用户再次纠正会使旧模型结果失效。Fragment 原消息被用户删除时，
正式 Fragment 仍是留存证据，但界面必须继续说明原消息不可用。

模型调用前先检查完整性、状态、哈希和不安全文本。来源失配或含提示注入时不调用模型，记录
`summary_rejected`，不保存正文或原始模型输出。

## 3. 模型输出协议

输出字段：

- `title`：只能使用 Episode/Entity 已有主题词和通用“长期故事/记录”词。
- `theme`：只能使用 Episode 或共同 Entity 已有词。
- `current_stage`：必须逐字来自时间上最新 Episode，并列出其 ID。
- `claims`：2～10 条；每条逐字来自列出的 1～4 个 Episode，角色为 anchor、development、change
  或 resolution。
- `lifecycle_signal`：active 或 completed；它只是 D.5 的候选信号，D.3 不改变正式生命周期。
- `completion_evidence_episode_ids`：completed 时必填，并必须被 resolution claim 直接引用。

程序要求摘要覆盖最早和最新 Episode，anchor 必须引用最早 Episode，发展/变化/收束必须引用最新
Episode。completed 还要求来源原文明确含“完成、结束、终止、告一段落、达成、取消、不再继续”等
收束证据。仅凭模型判断“应该结束”无效。

## 4. 安全校验顺序

1. 候选必须是 qualified，Episode ID 数量和顺序完整。
2. Episode 必须是正式可用状态，且每个 Episode 都有 Fragment 来源。
3. 非人工纠错 Episode 的 Fragment 当前哈希必须匹配保存哈希。
4. Episode 标题/摘要不得含密钥、密码或覆盖系统规则等不安全模式。
5. JSON 大小、严格 schema、额外字段和 ID 枚举校验。
6. current_stage 必须来自最新 Episode，claim 必须逐字受来源支持且不得重复。
7. 标题、主题、起点、发展覆盖和完成证据校验。
8. 程序拼接摘要并检查 1000 字上限。
9. 写事务内重读 Episode 与 Fragment，重算整条 Saga 来源哈希；变化则拒绝旧结果。

任何一层失败都不能把模型文字写入候选。

## 5. 一次结构修复

只有 `invalid_json`、`invalid_type` 和 `schema_invalid` 可以调用一次修复模型。修复提示只允许修 JSON
结构，禁止改写或新增标题、主题、current_stage、claim、状态、角色、事实或 ID。事实不受来源支持、
提示注入、错误完成状态和来源变化均不可修复，直接进入当前来源的安全回退。

两次调用的 prompt/completion token 合并记录；保存 provider、model、是否修复和错误码，但永远不保存
原始或修复前模型输出。

## 6. 安全抽取回退

模型不可用、调用失败、输出不合规或 TOCTOU 冲突时，从事务内重新读取的当前 Episode 生成
`saga-extractive-v1`：

- 按来源顺序选择安全 Episode 摘要；
- 第一条为 anchor，其余为 development；
- current_stage 使用最新安全 Episode；
- lifecycle_signal 固定为 active，回退逻辑永远不自动结束 Saga；
- 标记 `extractive_fallback` 并保存触发错误码。

如果来源链本身损坏或不足两条安全 Episode，回退也拒绝写入，记录 `summary_rejected`。

## 7. schema 20 与审计

`saga_group_candidates` 增加标题、摘要、主题、当前阶段、生命周期信号、摘要状态/协议、模型、Episode
证据、完成证据、警告、错误、整链哈希、token 和修复标记。not_started 候选对外仍隐藏空正文，维持
D.2 数据最小化。

`saga_candidate_summary_events` 只允许 `summary_validated`、`summary_fallback`、
`summary_rejected`，保存错误与无敏感内容的调用元数据。原始模型输出不进入表、事件或日志。

## 8. D.2 review 建议处理

- 采纳“参考 C.4 七层校验并适配双层来源链”；D.3 实际固定为上述九步校验。
- 无 Entity 的文本路径继续只生成 Episode 对，D.3 不扩大评分器职责。
- coherence 拆分维持不变。
- 最近 100 Episode 扫描上限继续保留，D.5/D.6 监控真实数量后再决定游标扫描。
- N13/N14 与 Saga 摘要事实正确性无关，继续在原计划追踪。

## 9. 验收边界

D.3 不创建正式 Saga、不改变 Saga 生命周期、不启动后台 worker，也不向关系/情绪系统提供信号。
验收必须覆盖正常校验、结构修复、模型不可用回退、虚构动机、提示注入、错误完成状态、Fragment
哈希失配和模型调用期间用户纠正。

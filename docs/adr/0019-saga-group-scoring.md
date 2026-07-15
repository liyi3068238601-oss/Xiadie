# ADR-0019：Saga 跨日预筛、四分量评分与候选生命周期

- 状态：已接受（D.2）
- 日期：2026-07-15
- 依赖：ADR-0018

## 1. 决策

Saga 候选使用纯本地、确定性且可回放的规则生成。模型不能创建候选、提高分数或绕过最低门槛。
第一版策略版本为 `saga-group-v1`：

| 参数 | 数值 | 原因 |
|---|---:|---|
| 最少 Episode | 2 | 单次经历不能自称长期故事 |
| 最多 Episode | 12 | 控制候选、摘要与来源审查成本 |
| 最长总跨度 | 180 天 | 覆盖数周到数月的发展，同时避免把多年无关内容一次聚合 |
| 最大相邻间隔 | 60 天 | 长期故事允许停顿，但不能靠极远的两个点强行连接 |
| 候选保留期 | 21 天 | Saga 周任务至少有三次观察机会；到期不删除 Episode |
| 综合门槛 | 0.52 | 比 Episode 更强调长期主题证据，需配合主题硬门槛 |
| 共同实体文本门槛 | 0.10 | 同一个人物或项目不代表所有经历都是同一故事 |
| 无实体文本门槛 | 0.48 | 没有 Entity 锚点时必须有很强的标题/摘要重合 |
| 单次扫描上限 | 最近 100 个 | 限制本地两两文本预筛成本 |

“跨自然日”按后端所在系统的本地日期计算。当前应用是单用户 Windows 桌面应用，系统时区就是用户
时区；如果以后支持远程服务器或多时区账号，策略版本必须升级并保存显式用户时区。

## 2. 四分量

```text
total = entity × 0.30
      + text × 0.35
      + time × 0.15
      + coherence × 0.20
```

- `entity`：所有 Episode 共同 active Entity 数量除以 Entity 并集数量。
- `text`：标题与摘要三元字符集合的两两 Jaccard 平均值，中文无需分词器也能稳定计算。
- `time`：由最大相邻间隔衰减；越连续越高，超过 60 天直接拒绝。
- `coherence`：相邻文本连续性 50% + 相邻 Entity 连续性 30% + 来源 Episode 平均置信度 20%。

先计算四分量，再检查主题硬门槛。即使综合分达到 0.52，只要共同实体组的文本分低于 0.10，或无
实体组的文本分低于 0.48，也不能晋级。这样可以阻止“都提到用户/同一个大项目”造成的错误聚合。

## 3. 分组来源

只扫描状态为 `active` 或未来兼容的 `completed` 正式 Episode。`archived`、`tombstone` 和 Episode
候选不参与默认预筛。候选来源有两条：

1. active Entity 相同的 Episode 在时间窗口内形成组；
2. 没有共同 Entity 时，文本相似度达到 0.48 的跨日 Episode 对可以形成组。

共同 Entity 只负责缩小搜索空间，不自动通过主题门槛。所有组必须跨至少两个系统本地自然日，Episode
按 `start_at,id` 稳定排序。

## 4. 指纹、重叠和冲突

分组指纹为 `sha256(policy_version + 排序去重后的 Episode ID)`。输入顺序或重复 ID 不改变指纹，策略
升级会产生新指纹。相同策略、相同来源重复运行必须复用候选。

同一轮多个合格组重叠时，按总分、组大小、开始时间和指纹稳定排序；先选中的合格组占用本轮 Episode，
后续重叠组不晋级。低分观察记录可以重叠，因为它们不具备正式归属权。

持久化前再次查询 `memory_saga_episodes`。只要任一 Episode 已属于有效 Saga，候选保存为
`conflicted / episode_already_in_saga`，不能复用来源或修改已有 Saga。D.4 正式应用时仍须在写事务内
重复检查，D.2 检查不能替代事务约束。

正式 Saga 的 `grouping_fingerprint` 继续全局唯一，包括 tombstone。用户删除或隐私清理后的相同来源
组合不得自动重建，否则会产生幽灵恢复；需要重新形成故事时，必须有新的 Episode 来源，产生新指纹。

## 5. 候选数据最小化

schema 19 的 `saga_group_candidates` 只保存：

- 有序 Episode ID 与共同 Entity ID；
- 四个分量、总分、门槛判断、跨度和最大间隔；
- 状态、冲突原因、策略版本、评估次数和时间。

候选不复制 Episode 标题、摘要或 Fragment 正文。低分 `observing` 候选到期变为 `expired`，不删除或
修改任何 Episode。`qualified`、`conflicted` 和 `expired` 是 D.2 的终态；来源集合变化会产生新指纹。

## 6. D.1 review 建议处理

- 采纳参考 Episode 四分量的建议，但 Saga 使用更长窗口、更高文本权重、跨日硬门槛和两级主题门槛。
- `memory_saga_events.action` 继续使用自由文本，D.5 在唯一事件写入口校验合法 action。
- `memory_sagas.grouping_fingerprint` 继续全局唯一；本 ADR 明确 tombstone 后不允许同来源自动重建。
- `summary_status=extractive_fallback` 维持不变；D.2 不创建正式 Saga。

## 7. 验收边界

D.2 只生成候选账本，不调用模型、不创建正式 Saga、不写 Saga-Entity/Saga-Event，也不修改 Episode、
Fragment、Entity、关系或情绪状态。D.3 负责摘要事实校验，D.4 才能原子应用正式 Saga。

# ADR-0018：Saga 聚合地基、摘要更新与生命周期

- 状态：已接受（D.1 数据地基）
- 日期：2026-07-15
- 依赖：ADR-0013～0017（Episode 自动化与正式界面）

## 1. 决策背景

Episode 表示一次连续经历，Saga 表示跨越多个日期、仍在发展的长期主题。遐蝶是人物扮演类陪伴
Agent，因此 Saga 的价值不是把相似文字堆成大摘要，而是让她能够自然理解“我们一直在做什么、事情
如何变化、现在进行到哪里”。Saga 只能整理已经正式落库且有来源链的 Episode，不能直接消费聊天
消息、候选 Episode 或无来源的模型推断。

阶段 D 分六个可独立审查的小阶段：

1. D.1：本 ADR、正式表、约束和迁移测试。
2. D.2：纯本地候选预筛、稳定分组指纹和跨 Saga 冲突阻断。
3. D.3：有来源约束的 Saga 摘要生成与事实校验。
4. D.4：后台任务账本、自动创建和增量追加的原子事务。
5. D.5：生命周期、纠错 API 与受限关系信号。
6. D.6：正式界面、来源时间线、总验收与旧候选 API 退役决策复核。

## 2. 聚合规则

首版 Saga 候选必须同时满足：

- 至少包含 2 个不同正式 Episode；
- Episode 的日期跨度至少跨过一个自然日；
- 至少共享一个 active Entity，或通过受控主题词得到足够高的文本相关度；
- 时间跨度和相邻间隔处于策略允许范围；
- Episode 当前未被其他有效 Saga 占用；
- tombstone Episode 永远不参与候选。

共同实体只是预筛条件，不等于一定属于同一故事。D.2 使用实体、文本、时间连续性和叙事连贯性四个
可解释分量评分，并将阈值、权重、窗口和版本固定在策略模块中。模型不能绕过本地最低门槛。

分组指纹由策略版本和有序 Episode ID 生成。相同输入重复运行必须命中同一结果，Episode 在同一时间
只能属于一个未移除的 Saga 来源链。跨 Saga 冲突先阻断并记录，不自动搬运来源。

## 3. 来源链与事实边界

`memory_saga_episodes` 是 Saga 来源的规范关系表，按 `position` 保存叙事顺序；
`source_episode_ids_json` 和 `source_hash` 是应用时的有序快照与防篡改校验值，不代替关系表。
Saga 详情必须能够沿下列路径回到原对话：

```text
Saga → memory_saga_episodes → Episode → memory_episode_fragments → Fragment → source message
```

Saga 摘要只能概括来源 Episode 已经支持的事实。模型输出必须经过与 Episode 摘要相同思想的结构校验、
来源证据校验和一次受限修复；失败时采用来源文本整理，不能凭空补全动机、结果或时间。

Saga 不得修改 Episode 或 Fragment 的标题、正文、状态、重要度和来源。Saga Entity 默认从 Episode
Entity 派生；人工调整只改 Saga 关系，不反向改写实体来源关系。

## 4. 创建与增量更新

新 Saga 的创建事务必须一次性写入：

- `memory_sagas` 正式对象；
- 有序 Saga-Episode 关系；
- Saga-Entity 关系；
- `created` 审计事件；
- 候选或任务账本的终态。

任一步失败都整批回滚。增量追加新 Episode 时，先读取旧 Saga 和旧来源哈希，在事务外生成并校验
新摘要，进入写事务后再次比较旧哈希和 `updated_at`。只有快照仍一致时，才同时追加关系、更新摘要、
时间范围和来源哈希并写 `episode_appended` 事件。模型失败、校验失败或并发冲突均保留旧摘要和旧来源。

不允许仅因相似度下降自动移除已经成为历史证据的 Episode。错误归组由后续专用纠错操作标记关系
`removed_at`，并重算摘要和来源快照；审计历史继续保留。

## 5. 生命周期状态机

Saga 使用精确状态机：

```text
active ──完成证据/人工结束──> completed ──归档策略/人工归档──> archived
  ^                               │                         │
  └────新发展证据/人工恢复────────┘                         │
  └────────────人工恢复且来源仍有效─────────────────────────┘

active/completed/archived ──仅用户删除或隐私清除──> tombstone
```

合法转换：

| 旧状态 | 新状态 | 条件 |
|---|---|---|
| active | completed | 来源 Episode 明确支持结束，或用户主动结束 |
| completed | active | 出现与主题一致的新发展 Episode，或用户主动恢复 |
| completed | archived | Archivist 策略达到门槛，或用户主动归档 |
| archived | active | 用户主动恢复且来源仍有效；自动恢复留待阶段 E 决定 |
| active/completed/archived | tombstone | 仅用户删除或隐私清除 |

禁止 `tombstone` 恢复，禁止自动任务产生 tombstone，禁止 active 直接自动 archived。状态变化必须在同一
事务写入旧状态、新状态、原因、来源、策略版本和时间。`completed_at`、`archived_at`、
`tombstoned_at` 保存最近一次进入对应状态的时间；重新激活不删除历史事件。

正式 Saga 的分组指纹全局唯一，tombstone 也继续占用原指纹。相同来源组合不能被后台任务自动重建，
避免用户删除或隐私清理后发生幽灵恢复；新的长期故事必须包含新的 Episode 来源并产生新指纹。

Episode 的 completed/archived/delete 语义由阶段 E 的统一生命周期事务处理。D 阶段不为了 Saga 聚合
重建 Episode 表，也不把 Saga 状态强加给其来源 Episode。

## 6. 纠错与摘要状态

Saga 纠错使用专用端点，区别普通编辑：

- 标题、摘要或主题被纠正后，标记 `summary_status=user_edited`、
  `summary_protocol_version=manual-v1` 并清空模型证据；
- 只改重要度时保留原摘要校验状态；
- 纠错不能暗中改变 Episode 来源集合或来源哈希；
- 来源归组纠错是另一种显式操作，必须重新校验摘要并记录 removed/appended 事件。

## 7. 受限关系信号

Saga 可以向关系/情绪系统提供只读、可审计且有上限的 delta 建议，例如共同完成长期项目后提供很小的
bond/trust 正向信号。接收方自行应用既有上限、冷却和安全规则。Saga 服务不能直接写 relationship_state、
affect_state、Episode 或 Fragment；无来源的摘要文字不能产生关系 delta。

## 8. 表结构决策

- `memory_sagas`：正式对象、摘要校验信息、来源快照、生命周期时间和纠错信息。
- `memory_saga_episodes`：有序 Episode 来源；`removed_at IS NULL` 时一个 Episode 只能属于一个 Saga。
- `memory_saga_entities`：Saga 与 Entity 的 Episode 派生或人工关系，不接受关系系统反向写入，
  也不修改 Episode-Entity。
- `memory_saga_events`：创建、追加、摘要更新、状态转换和纠错的独立审计流。

外键删除策略刻意保守：Saga 不物理删除来源 Episode/Entity；Episode/Entity 被 Saga 引用时使用
`ON DELETE RESTRICT`。隐私清理需要先执行可审计的 Saga 来源清理事务，不能让级联删除悄悄切断证据。

## 9. review 建议处理

- Episode 纠错乐观锁：建议正确但不属于 D.1；Saga 增量更新从一开始采用快照复核，Episode 端点在
  生命周期/API 整理阶段统一补齐。
- 旧 Episode 候选 API：主界面已不使用。保留到 D.6 总验收，届时确认无桌面调用、无迁移依赖后写
  退役 ADR；在此之前仅作为兼容面，不扩展功能。
- Episode 来源分页：当前正式 Episode 最多 20 条来源，暂不增加复杂度；达到真实体验瓶颈后改为游标分页。
- N13/N14：均为既有低优先级体验项，与 Saga 数据正确性无关，继续留在对应计划书追踪。

## 10. D.1 验收边界

D.1 只证明数据地基正确，不声称已经能够生成 Saga。验收包括 schema 18 可重复初始化、四张表存在、
状态/数值/时间约束有效、来源有序、一个 Episode 不会同时进入两个有效 Saga、事件可追溯，以及全量
回归无破坏。D.2 才开始候选预筛。

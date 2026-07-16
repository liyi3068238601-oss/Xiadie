# ADR-0024：Archivist 生命周期、保留审计与物理清理边界

- 状态：Accepted
- 日期：2026-07-16
- 阶段：记忆系统 E.1
- 关联版本：schema 23

## 背景

Fragment 已经使用 `active/cooling/frozen/tombstone` 四态，但此前只有状态枚举，没有真实召回计数、进入
冷却/冻结的时间、保留策略版本或专用状态事件。直接按“14/30/90 天”更新状态会把时间误当成删除理由，
也无法解释某条记忆为什么被保留或退出召回。

Archivist 是本地维护程序，不是聊天角色。它必须在不阻塞聊天、不凭模型直觉删除事实、不泄露记忆正文
到审计日志的前提下维护长期记忆。

## 决策

### 1. Fragment 状态与召回

- 保持 `active → cooling → frozen` 的自动单向降温路径；`cooling/frozen → active` 只在真实强相关注入、
  新证据或用户操作后发生。
- `tombstone` 只允许用户明确删除或隐私清除。Archivist 永远没有进入 tombstone 的权限。
- 只有 Fragment 实际装入某一轮模型上下文时才算一次召回。FTS 命中、候选排序、详情页查看和后台扫描
  都不计数。
- schema 23 新增 `last_recalled_at`、`recall_count`、`cooling_since`、`frozen_at`、
  `lifecycle_policy_version` 和 `lifecycle_revision`。
- `memory_recall_events` 以 `(fragment_id, context_key)` 唯一约束保证同一轮重复命中只计一次；账本只保存
  ID、会话、token 估算、策略版本和时间，不保存正文。

### 2. 保留评分与状态事件

- 第一版策略名为 `fragment-retention-v1`，使用设计书第 14.2 节的 importance、recall、recency、
  relationship、active Saga、confidence 和 duplicate penalty 分量。
- 14 天和 30 天只是“允许评估”的最短年龄；分数与保护条件不满足时不得转换。
- 每次状态变化在同一个短 `BEGIN IMMEDIATE` 事务内更新 Fragment revision/时间并写入
  `memory_lifecycle_events`。事件保存旧状态、新状态、0～1 分数、分量 JSON、原因、来源、策略版本和时间。
- 生命周期事件和召回账本均禁止正文字段。用户纠错后的当前事实、稳定边界、活跃 Saga 锚点和未完成计划
  属于保护对象，具体识别算法在 E.2 固定并测试。

### 3. Episode 与 Saga

- Fragment 降温不能删除 Episode/Saga 的来源链接或正文证据。
- Episode/Saga 使用更慢的成熟与归档策略，不能套用 Fragment 的 14/30 天阈值；其 schema 与服务在 E.5
  单独实现。
- completed Saga 的自动归档只由 Archivist 执行。第一版候选时间点为完成后至少 12 个月，并要求自
  `max(completed_at, updated_at)` 起没有追加、纠错、恢复或其他 revision 变化；高 significance、近期真实
  召回或重要关系叙事仍可长期保留 completed。active Saga 不得自动归档。
- 归档不是删除，用户仍可查看并恢复。Saga 的 tombstone 继续只接受用户/隐私来源。

### 4. 任务与失败边界

- E.2～E.4 复用 Episode/Saga worker 的幂等入队、串行认领、最多三次退避、陈旧恢复、协作取消和短
  写事务模式。
- 每日任务以 `last_archivist_run` 距成功超过 20 小时为入队条件，不补跑错过的每一天；每轮必须限制
  对象数和耗时。
- 模型、向量索引或 Archivist 失败不能让聊天失败，也不能留下部分状态更新。

### 5. 物理清理与备份

- cooling、frozen 和 archived 均保留正文。frozen 可以退出派生检索索引，但不能清空来源。
- tombstone 立即退出召回与索引；普通用户删除先保留最小对象行与无正文审计，隐私清除才清空正文、
  标签和派生向量。
- 备份中的物理清除不能在没有备份保留/轮换策略时假装完成。当前阶段只记录待清除边界，不主动遍历或
  删除备份。

## 未采用或延后

- 不采用“到期直接删除正文”：时间不足以证明内容无价值，且会破坏 Episode/Saga 来源链。
- 不在 E.1 启动任何自动转换：先迁移旧库并验证约束，E.2/E.3 再实现可回放评分与事务服务。
- 不在 Archivist 阶段顺便实现 Saga 搜索、星图或全文筛选；这些属于检索和展示能力。
- 旧 Episode 候选 API 继续遵守 ADR-0023 的五项退役条件，Archivist 重构本身不等于条件已经满足。

## 数据与迁移

- schema 23 只增加可空/有安全默认值的 Fragment 字段、两个审计表和索引，不改写正文与来源。
- 已处于 cooling/frozen 的旧 Fragment 分别用原 `updated_at` 回填 `cooling_since/frozen_at`；active 记录的
  召回时间保持 NULL、计数为 0，不臆造历史召回。
- 新表使用外键 RESTRICT；正式清理必须先执行明确的隐私/审计策略，不能因级联删除无意抹掉账本。

## 验证

- 空库升级到 schema 23，重复 `init_db()` 保持幂等。
- 独立旧表迁移保留 active/cooling/frozen 行并正确回填默认值。
- 同一 Fragment/context 重复召回事件被唯一约束拒绝。
- 生命周期 revision、状态枚举和分数范围由数据库约束保护。
- 两类审计表结构不包含 content、summary、tags、source_text 或 raw_output。

## 后续阶段

- E.2：真实注入计数、纯保留评分、保护条件。
- E.3：精确状态转换、恢复、索引同步与事务审计。
- E.4：Archivist worker、20 小时调度、预算和恢复。
- E.5：Episode/Saga 成熟、完成与自动归档。
- E.6：冲突关系、管理界面与总验收。

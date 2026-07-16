# ADR-0028：保守冲突关系与生命周期管理闭环

- 状态：Accepted
- 日期：2026-07-16
- 阶段：记忆系统 E.6
- 关联版本：schema 27

## 背景

E.1～E.5 已完成 Fragment、Episode、Saga 的写入、整理和生命周期，但用户还不能集中查看保留分、保护
原因与状态事件；重复或矛盾的 Fragment 也没有安全的表达方式。冲突检测若直接覆盖正文，会把不确定推断
升级成事实，违背陪伴型 Agent 的记忆可追溯边界。

## 决策

1. schema 27 新增 `memory_fragment_relations` 和无正文事件表。关系保存源/目标方向、共享 Entity、
   `superseded` 或 `possible_conflict`、置信度、规则、检测器版本、可选模型版本和人工处置状态。
2. 预筛仅检查共享 active Entity、相同 scope、相同可变 kind、active/enabled/normal 的小集合。experience、
   correction、敏感、禁用、冷却和冻结内容不参与自动关系判断。
3. 明确否定且时间较新的记录建立 older→newer 的 `superseded`；局部文本相似只建立
   `possible_conflict`。检测器不修改 Fragment 正文、enabled 或生命周期状态，也不调用模型。
4. Archivist 在既有扫描预算内执行关系预筛；即使没有到期 Fragment，也可完成关系检测。run 保存
   `relation_count`，并继续用 `conflict_count` 表示并发 revision 冲突，两者不得混淆。
5. 用户可把关系标记为 resolved 或 dismissed，必须填写原因并写事件；处置关系仍不自动改正文。
6. 管理页展示 Fragment 保留分量、保护原因、状态/修订、生命周期事件和相关冲突；Episode 展示四态和
   专用生命周期事件。cooling/frozen Fragment 与 completed/archived Episode 可人工恢复。
7. Fragment 隐私清除和 Episode tombstone 都使用确认框加 `DELETE` 文本确认，并明确应用不会自动创建
   备份、应用外备份不随本地删除清理。自动流程仍不能产生 tombstone。
8. E.5 review 的 `MAX` 建议按真实代码调整：到期时间的二值比较改用 `CASE`；聚合最近召回时间所需的
   `MAX(column)` 保留。生产库检查时只有 3 个 Fragment、1 个 active Episode、0 个 Saga、0 个完成 Saga，
   schema 26→27 不会触发批量 Saga 归档；迁移仍在正常启动事务中执行。

## 后果

- 系统可以表达“新旧变化”和“可能冲突”，但不冒充自动纠错。
- 关系检测是确定性、零模型调用、有限预算且幂等；已建立关系不会持续占用后续扫描预算。
- 用户能理解记忆为何保留或降温，也能进行恢复和明确的高风险清理。
- 更复杂的语义矛盾检测若未来引入模型，必须新增协议、成本、隐私披露和降级测试，不能复用本版规则名。

## 验证

- schema、资格预筛、明确否定方向、不确定关系、幂等、无正文改写、人工处置和详情 API 均有专项测试。
- Fragment→Episode→Saga 的既有端到端、失败恢复、重复运行与来源链测试继续全量通过。
- 后端 235 项、前端 16 项、生产构建及 Electron 主进程/预加载语法检查通过。


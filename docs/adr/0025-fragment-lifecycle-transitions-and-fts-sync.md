# ADR-0025：Fragment 精确生命周期转换与 FTS 同步

- 状态：Accepted
- 日期：2026-07-16
- 阶段：记忆系统 E.3
- 关联版本：schema 24

## 背景

ADR-0024 与 E.1/E.2 已建立生命周期字段、真实召回账本、保留评分和跨层保护识别，但尚未改变任何状态。
本阶段需要把规则变成可审计的确定性转换，同时保证 frozen 内容不被普通检索召回、恢复后可重新检索，且
任何失败都不会留下“状态已改、索引或事件未改”的半成品。

## 决策

1. 自动转换仅允许 `active → cooling → frozen`，一次评估最多前进一步。active 至少 14 天、分数
   `<0.45` 且无保护才进入 cooling；cooling 额外至少 30 天、分数 `<0.30`、期间无新修改且无保护才
   进入 frozen。90 天不是删除期限，只作为长期稳定边界测试。
2. `cooling/frozen → active` 只有三种来源：强相关真实召回、新证据和用户操作，分别写独立 reason code。
   召回恢复先按“增加一次真实注入”计算假设分，至少 `0.50` 才允许进入提示词和原子记账。
3. 状态、进入时间、`lifecycle_revision`、策略版本、FTS 标记和无正文生命周期事件在同一个
   `BEGIN IMMEDIATE` 事务写入，并使用预期 revision 防止覆盖并发修改。
4. schema 24 增加 `fts_indexed`。frozen 使用 FTS5 external-content 的特殊 delete 命令退出索引；从 frozen
   恢复时重建索引。状态感知触发器只维护 `fts_indexed=1` 的行，避免普通内容更新把 frozen 内容重新加入。
5. Archivist 的合法目标状态不含 tombstone。tombstone 仅由用户删除或隐私清除产生；隐私清除还会清空
   Fragment 正文、标签、内部理由、情绪、证据/来源 ID，并净化既有 Fragment 审计正文。
6. E.3 暴露确定性评估和恢复能力，但不启动周期扫描。run/event 任务账本、调度、重试、取消和预算属于 E.4。

## 后果

- frozen 不再参与普通 FTS 召回，但数据库正文仍保留，符合“冻结不是删除”的边界。
- 恢复原因可审计，普通 PATCH 无法绕过状态机；用户恢复使用专用端点与乐观 revision。
- FTS 是派生数据，`fts_indexed` 是数据库内的同步事实；旧 schema 23 数据迁移后默认保持原索引状态。
- 新增迁移兼容、阈值、保护、恢复、revision 冲突、事务回滚、索引往返、tombstone 和隐私清除测试。

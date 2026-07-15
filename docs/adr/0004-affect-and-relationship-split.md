# ADR-0004：拆分短期心境与长期关系

- 状态：Accepted
- 日期：2026-07-15
- 决策者：项目所有者、Codex
- 取代：ADR-0002 中的 CompanionState 状态定义
- 关联计划：`docs/AFFECT_AND_RELATIONSHIP_SYSTEM_PLAN.md`

## 背景

旧 `companion_state` 同时把 connection 当作熟悉度和联系需求，把 pride 当作自信和防御，且没有根据真实经过时间推进。前端还另有一套基于回复关键词的 Live2D 情绪判断，多个状态来源无法保持一致。

## 决策

- 长期关系保存为 `relationship_state`：`bond`、`trust` 和互动次数。
- 短期心境保存为 `affect_state`：`contact_need`、`guardedness_transient`、`valence`、`arousal` 和 `immersion`。
- guardedness 的慢速基线由 trust 派生，短期事件只修改 transient。
- 使用确定性懒推进和最多五分钟的积分步长，不使用随机概率。
- 所有实际状态变化写入 `affect_events`，保留算法版本和可空来源消息。
- 后端统一输出情绪簇、语调和主动信号；主动信号暂不发送。
- 旧 `companion_state` 暂留为迁移来源，不再作为新逻辑的事实来源。

## 后果

- 关系不会因为用户沉默而下降。
- 应用关闭期间也能在下次读取时推进状态。
- 调参可以依赖确定性时间线测试和事件回放。
- 前端仍需在后续阶段切换到统一 API 并删除关键词情绪判断。

## 回滚

停止在聊天提示中使用新状态并恢复旧模块读取即可回到原行为。新表独立存在，不影响会话、模型、人格和记忆数据。

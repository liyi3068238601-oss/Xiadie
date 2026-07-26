# LIFE.2 LifeEventLedger 验收报告

- 日期：2026-07-26
- Schema：64
- 写入所有者：LIFE
- ToolRun 证据：复用既有 `tool_logs`

## 事实与生命周期

`life_events` 是唯一事件主表；`life_event_revisions` 保留追加式纠正历史，`life_event_sources` 保存逐来源 revision/hash，`life_event_audit_events` 只记录状态、revision 和 reason code。

世界层级严格为 planned、simulated、observed、performed。planned/simulated 不会被读取为真实执行；performed 只允许 `agent_action`，且必须绑定已完成的 ToolRun。生命周期只允许 active、superseded、revoked，当前施工实现创建、追加纠正与不可逆撤销；非法或过期 revision 写入失败。

## 来源删除与幂等

来源删除只撤销对应 link；仍有其他 active 来源时事件继续 active，最后一个来源删除时事件自动 revoked。相同幂等键与相同内容返回原事件，不重复物化；相同键承载不同内容会报 `idempotency_conflict`。

## API 与验证

- `GET /api/life/events`：只读事件投影。
- `GET /api/life/events/diagnostics`：无正文状态审计。
- 没有公开 LIFE 写 API，尚未接入日记或聊天。
- LIFE.2 专项 8 passed；迁移/API/CDS/邻接回归 113 passed，1 warning。
- 无来源 performed agent action 写入率：0。

阶段独立 Review 按用户要求留待 LIFE 总体 Review。

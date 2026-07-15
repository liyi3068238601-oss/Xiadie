# ADR-0007：观察器后台执行、有限重试与原子应用

- 状态：Accepted
- 日期：2026-07-15
- 决策者：项目所有者、Codex
- 关联计划：`docs/AFFECT_AND_RELATIONSHIP_SYSTEM_PLAN.md` 阶段 2.3

## 背景

阶段 2.2 已能生成和审计候选，但网络调用仍延迟聊天完成、失败任务不会真正重试、观察模型不能独立选择，候选也尚未进入真实 affect 状态。

## 决策

- 聊天路径只将任务写为 `queued` 并唤醒 worker，SSE done 不等待观察模型。
- worker 随 FastAPI lifespan 启动和关闭；启动后处理 queued、到期 recovery 和过期 running。
- 每次 claim 在 `BEGIN IMMEDIATE` 内将 attempt_count 加一，最多三次；失败采用 5、10 分钟退避，第三次进入 `exhausted`。
- 观察模型设置支持跟随当前聊天模型，或选择独立的真实供应商与模型；mock 不可作为独立观察模型。
- 净化候选应用到最新持久化状态，guardedness 只改变 transient，应用后再次执行领域 clamp。
- affect/relationship 更新、observation 事件和任务 applied 状态必须在同一 SQLite 事务提交。
- 任务通过 `applied_event_id` 指向实际状态事件；应用事务失败则完整回滚并按有限重试处理。
- stale running 恢复只由 worker 执行；聊天热路径和 GET API 不产生恢复写入。
- 单次观察输入上限从阶段 2.2 的 20000 字符收紧到 12000 字符。正常对话仍可覆盖，
  同时减少异常超长上下文的调用成本；超限任务直接进入 `skipped`，不保存原文。

## 后果

- 用户不再为第二次模型调用等待最多 20 秒。
- 进程重启可继续未完成任务；永久失败不会无限计费。
- 情绪观察首次成为真实状态来源，但仍受到阶段 2.1 的协议、证据、置信度和限幅保护。
- 记忆观察器尚未正式实现，因此暂不合并调用；待记忆阶段 B 确定 schema 后再评估。

## 回滚

停止 lifespan worker 并让聊天不再 enqueue，即可恢复为纯 fallback 引擎。迁移 9 的任务与事件引用独立存在，不影响会话和记忆表。

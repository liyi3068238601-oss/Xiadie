# 对话上下文 CTX.2 摘要数据地基阶段报告

- 日期：2026-07-19
- 开工提交：`24a51de`
- 数据库迁移：schema 41 → 42
- 摘要协议预留：`conversation-summary-v1`
- 运行行为：新增派生账本与 regenerate 失效；不生成、不注入摘要
- 下一阶段：CTX.3 受约束的后台摘要生成

## 阶段结论

CTX.2 已建立会话滚动摘要的可重建派生数据地基：revision、run、event 三张表，连续完整轮次来源、SHA-256
来源指纹、单 active revision、幂等任务、租约、心跳、有限重试、陈旧恢复、重新生成失效、会话删除级联和无正文
只读诊断均已落地。

本阶段没有调用摘要模型，没有自动排队生成摘要，没有修改长期记忆系统，也没有在聊天 prompt、SSE 或普通用户界面
中加入摘要。用户继续获得 CTX.1 的原始聊天体验；新数据结构只是为 CTX.3/CTX.4 提供安全基础。

## CTX.1 严格 Review 建议处理

| 等级 | 建议 | 决定与结果 |
|---|---|---|
| P0 | 旧数据库升级后消息完整、相同来源幂等、删除会话无孤儿 | 采纳；schema 42 迁移和级联专项测试通过 |
| P0 | events 不保存原始消息正文或模型原始 JSON | 采纳；事件 metadata 只允许预定义标量计数，reason code 强制稳定格式 |
| P0 | 前端不能伪造 active，状态机只能由后台事务改变，来源竞态拒绝落库 | 采纳；HTTP 仅 GET 诊断，数据库 CHECK/单 active 索引和带租约原子激活共同约束 |
| P0 | CTX.2 不改变聊天回答内容 | 采纳；摘要 marker 不进入模型 messages，阶段无摘要读取链路 |
| P1 | CTX.2 开工前修复 N20/N21 | 不采纳时点；按用户决定继续保留在整个 CTX 计划末尾、CTX.7 后处理 |

## 数据模型

### `conversation_summary_runs`

- 以协议、会话、连续来源首尾 ID 和 `source_hash` 组成幂等键。
- 状态固定为 `queued/running/recovery_pending/completed/failed/exhausted/cancelled`。
- 保存 attempt、max attempts、lease token、lease expiry、heartbeat、next attempt 和稳定错误码。
- lease token 不通过诊断 API 返回。

### `conversation_summary_revisions`

- 状态固定为 `active/superseded/invalid/failed`，同一会话由 partial unique index 保证最多一个 active。
- 保存连续来源首尾 message ID、消息数、source hash、协议和 revision。
- 已为 CTX.3 一次建好结构化字段：`summary_text`、open threads、decisions、corrections、entity refs、
  provider/model 与 token usage，避免下一阶段再次迁移泛化 JSON。
- 只读诊断不返回摘要正文及上述结构化内容，只返回存在标志和项目数量。

### `conversation_summary_events`

- 只保存对象 ID、状态、action、reason code、版本/数量等允许的标量计数。
- 不保存消息正文、摘要正文、模型原始输出、密钥或任意字符串 metadata。

## 生命周期与安全行为

1. 只有严格连续的 `user/assistant` 完整配对可以成为来源范围。
2. source hash 覆盖消息 ID、role、content 和 model，但数据库只存 hash，不在事件中存正文。
3. worker 必须持有未过期 lease 才能 heartbeat、失败或激活结果。
4. 激活前在同一事务重新计算来源；来源变化时任务失败，旧结果不写入 revision。
5. 新 revision 原子激活时才 supersede 旧 active；失败 revision 不替换 active。
6. regenerate 成功替换旧 assistant 前，覆盖该消息的 active revision 变为 invalid，相关未完成 run 变为 failed。
7. 应用启动只恢复过期 run，不启动摘要生成；真正生成 worker 属于 CTX.3。
8. 删除 session 通过外键级联清理 runs、revisions 和 events。

## 阶段 Review findings

| 等级 | 发现 | 处理 |
|---|---|---|
| P1 | 初版草案只使用泛化 `summary_json`，会迫使 CTX.3 再做一次结构迁移 | 已修正：schema 42 直接采用计划书列明的结构化摘要、纠正、决定、开放事项、实体和 usage 字段 |
| P1 | 任意 error code 若直接写入事件，未来调用方可能误把正文放入审计 | 已修正：事件 action/reason 只接受稳定小写代码格式，metadata 只接受白名单标量计数 |
| P1 | 新 schema 使八个历史测试的“当前最新版本=41”断言过期 | 已只更新最新版本断言为 42；各测试对原历史迁移的验证内容保持不变 |
| P0/P1 | 摘要是否提前影响陪伴聊天 | 未发现；主聊天不读取 revisions，测试确认摘要 marker 不进入模型 messages |
| P0/P1 | 是否存在前端写接口伪造 active | 未发现；当前只提供 runs/revisions/events GET 诊断接口 |

Review 修正后未留下 P0/P1 问题。

## 验证证据

- CTX.2 专项：9 passed。
- 后端全量：446 passed；仅有 TestClient 弃用提示与 `.pytest_cache` 目录权限提示。
- 前端：33 passed。
- TypeScript + Vite：185 modules，生产构建通过；保留既有 Live2D 普通脚本提示。
- Electron：`main.js`、`preload.js` 语法检查通过。
- Python `py_compile` 与 `git diff --check`：通过。

专项覆盖：

- schema 41 旧库迁移且原消息不丢失；
- 连续完整来源范围与结构化列；
- 相同来源幂等和单 active；
- 新 revision 原子 supersede；
- 非法状态被数据库拒绝；
- 生成期间来源变化拒绝落库；
- heartbeat、错误 lease、陈旧恢复和达到最大尝试后 exhausted；
- 事件与只读诊断无正文；
- HTTP 无写入口；
- session 删除无孤儿；
- 聊天不消费摘要，regenerate 使覆盖 revision 失效。

## 已知限制与 CTX.3 边界

- `conversation-summary-v1` 目前只是表级协议标识，尚未定义或验证模型输出内容；正式输出验证属于 CTX.3。
- CTX.2 不自动 enqueue，会话完成后入队和后台 worker 属于 CTX.3。
- CTX.2 不将摘要展示给普通用户，也不将摘要放入聊天上下文；正式注入只能在 CTX.4 完成预算闭环后进行。
- N20/N21 仍按用户指定顺序留在整个 CTX 计划最后处理。

## Review 结论

**通过。** CTX.2 满足迁移完整性、状态机、来源可追溯、恢复安全、无正文审计、只读 API 和聊天隔离完成门，
可以进入 CTX.3。

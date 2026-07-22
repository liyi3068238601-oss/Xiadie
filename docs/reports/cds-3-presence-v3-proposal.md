# Conversation Presence v3 语义提案（未实施）

> 来源：CDS.3 Shadow 兼容校准  
> 状态：Proposal only；未修改、未注册、未迁移 EAP `conversation-presence-v2`

## 发现的兼容缺口

1. v2 对 `away_sleep` 固定写入 8 小时 `expected_return_at`；CDS.3 将“晚安”理解为暂停但不推断明确返回承诺，`expect_return=unknown`。
2. v2 的高精度正则仍会把“翻译晚安”“晚安按钮”等引用或元讨论识别为睡眠；Shadow 先识别 meta context，保留 `online`。
3. v2 的 `open_thread_topic` 是自由文本；CDS.3 只允许 `test_result/meal_return/shower_return` 等有界 thread code，并要求有效 source message ID。
4. v2 没有显式区分 `conversation_closure`、`response_need` 与 `followup_allowed`；这些语义不应从未知沉默或关系状态间接推断。

## 建议的新协议边界

- `conversation-presence-v3` 若立项，应新增三态 `expect_return=yes/no/unknown`，不再把默认时长等同于用户承诺。
- `earliest_followup_hint` 只表示最早可评估窗口，不表示届时必定联系。
- 引用/翻译/代码/文档等 meta context 在明确离开表达前优先保持 online。
- 未知沉默只能写 `unknown`，不得写拒绝、DND、关系下降或 conversation ended。
- thread 必须使用有界 code、有效 message ID、TTL，并继续由 EAP 决定是否形成真实候选。

## 迁移影响

- 不回写历史 v2 行；v3 使用新 revision/字段或独立兼容视图。
- v2 消费者继续读取原字段；新增三态与 thread code 需要显式 adapter。
- 在真实模型完成 ≥500 Shadow、误判门和用户控制验收前，不得启用 Advisory/Active。
- 本提案不授权 CDS 写 Presence、创建主动候选或投递消息。

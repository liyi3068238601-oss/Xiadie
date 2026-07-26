# ADR-0060：LIFE 模型提议、程序裁决与事实分层

- 状态：Accepted for LIFE construction
- 日期：2026-07-26
- 关联：LIFE.0、冻结的 `cognitive-decision-v1`、`specialty-adapter-contract-v1`

## 决策

1. LIFE 所有模型能力遵循 Local Candidate Builder → LLM Proposal → Deterministic Validator/Policy → LIFE Reducer。模型永远不直接写正式状态、发送消息、执行工具或授予权限。
2. 事实层严格区分 `simulated_world`、`observed`、`agent_action`、`conversation`、`external_fact`；生命周期严格区分 `planned`、`materialized`、`performed`、`inferred`、`skipped`、`cancelled`、`revoked`。
3. `agent_action/performed` 必须绑定未来 ToolRegistry 的真实成功证据。LIFE v1 没有 ToolRun 正式所有权，因此无证据时只能使用模拟、观察、对话或计划层。
4. LIFE 领域协议复用 CDS `DecisionRun`、来源 revision/hash、模型路由、预算、一次结构化修复、熔断和诊断，不新建第二套通用决策账本。
5. 应用前必须重新读取所有来源并核对 revision/hash；非候选 ID、低置信度、来源变化、未认证模型越级和提示注入均 fail closed。
6. 原始模型输出不落库。经过 Schema 净化的 LIFE 正式产品数据可以保存，但必须同时保存来源、协议和算法版本。
7. LIFE 是 LifeClock、SelfState、LifeEvent、DailySchedule、PersonalGoal、ImportantDate、Diary、ContinuityThread、SelfTimeline 和 BoundaryProfile 的唯一写入者。Affect/EAP、MEM、CTX 和 ToolRegistry 所有权不转移。

## 失败与回滚

- 模型、网络或预算失败时使用确定性回退或保持现状，不阻塞聊天和启动。
- LIFE 总开关停止未来推进，不删除历史。
- 回滚 LIFE 不删除聊天、记忆、关系、情绪或 EAP 投递数据。

## 反例

- “模型认为已经做完”不能产生 `performed`。
- 日记中的句子不能成为用户长期事实。
- 高 bond、积极心情或 PersonalGoal 不能覆盖用户边界或获得工具权限。

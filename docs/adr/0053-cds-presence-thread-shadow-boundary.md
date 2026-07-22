# ADR-0053：PresenceAndThreadObserver 只读 Shadow 边界

- 状态：Accepted，等待 CDS.3 独立 Review
- 日期：2026-07-22
- 关联：CDS.3、`conversation-presence-v2`、ADR-0051/0052

## 决策

1. CDS 注册专属 `presence_thread_observer` 输入/输出 Schema，但最高模式固定为 Shadow，fallback owner 与 application owner 均为 EAP。
2. 输出只允许有界 Presence 状态、三态 expect-return、closure、thread code、活动、follow-up hint 与 response need；每个非沉默结果必须绑定有效 source message ID。
3. CDS 不写 `conversation_presence`，不创建 `proactive_candidates`，不投递消息，也不从未知沉默推导拒绝、关系下降或会话结束。
4. 冻结 EAP v2 继续作为生产 fallback 和唯一写者。Shadow 差异只形成 `conversation-presence-v3` 提案，未经独立评测、迁移与 EAP 授权不得应用。
5. 本阶段用 660 轮纯合成固定集验证协议和兼容边界，不调用真实 Provider，不声称任何真实模型已经通过该 decision kind 的质量认证。

## 结果

- 三项完成门达到 0% / 100% / 0%，source message 绑定 100%。
- 元讨论优先、晚安不虚构返回承诺、未知沉默保持 unknown。
- 测试离开与返回序列使用有界 `test_result` thread code 验证线程连续性。

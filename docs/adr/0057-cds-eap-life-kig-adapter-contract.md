# ADR-0057：CDS EAP/LIFE/KIG 最小适配契约

- 状态：Accepted；等待 CDS.11 独立 review
- 日期：2026-07-26
- 关联：CDS.11、ADR-0051/0053、EAP 冻结协议组、共享专项所有权矩阵

## 决策

1. CDS.11 只冻结 `specialty-adapter-contract-v1`：`RevisionRef`、`CandidateEnvelope`、`DecisionResult` 采用 `TypedDict`，未来 LIFE 来源与 KIG 候选提供者采用 Python `Protocol`。本阶段不创建 LIFE/KIG 实现、领域表、worker 或生产消费者。
2. LIFE 的 `life_event`、`diary_entry`、`important_date`、`personal_goal`、`self_timeline` 只允许成为带 `id/revision/content_hash` 的来源，不得伪装成共享候选。KIG 的 `knowledge_object` 与 `pwm_projection` 只有经过来源、revision 和 SHA-256 校验后才可成为有限候选。
3. 共享 `DecisionResult` 必须绑定 `cognitive-decision-v1`、来源快照 hash 和候选集合，且该跨专项契约永远不能授予领域应用权。正式写入仍由各领域 Validator/Reducer 复核并执行。
4. `eap-decision-run-adapter-v1` 只读取 CDS 账本中 `application_owner=eap` 的 DecisionRun，并返回无正文、无候选 ID 的稳定诊断投影；该投影的 `application_allowed` 永远为 false。EAP 的候选、授权、强度、投递和反馈路径不变，CDS 不新增发送器。
5. 未回复压力继续由 EAP 确定性状态机计算。未来生活叙事规划只允许后台优先级，断网或应用退出期间不得调用模型。生活事件与接触事件以 kind/id/revision 作为稳定幂等身份。
6. Schema 保持 62。接口契约没有实际字段缺口，不占用 Schema 63。

## 验证

- EAP Shadow DecisionRun 完成后进行 32 路并发 adapter 读取，所有投影一致。
- adapter 读取前后逐行比较 `conversation_presence`、`proactive_candidates`、`proactive_decisions`、`proactive_deliveries`、`proactive_feedback`、`life_proactive_seeds`，领域写入为 0。
- 非 EAP application owner 的 DecisionRun 被拒绝；诊断投影不含候选 ID、Prompt、正文或原始模型输出。
- 契约测试覆盖 LIFE source-only、KIG validated-candidate、来源变化、越候选选择、跨专项 application grant、后台优先级、断网/退出和事件幂等身份。

## 后续边界

本 ADR 不代表 LIFE/KIG 已施工，也不授权任何 CDS DecisionKind 晋级。LIFE 开工时实现自己的领域对象与 reducer；KIG 只能在 LIFE 冻结后实现 SourceRef/PWM。任何适配字段不足必须先提出新版本和 ADR，不能向现有 TypedDict 偷加正文或领域写权限。

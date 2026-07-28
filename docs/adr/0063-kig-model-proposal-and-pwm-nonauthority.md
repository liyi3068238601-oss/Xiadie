# ADR-0063：KIG 模型只提议，PWM 不是事实权威

- 状态：Accepted for KIG construction
- 日期：2026-07-27
- 关联：KIG.0、冻结的 `cognitive-decision-v1`

## 决策

1. KIG 的信息分类、QueryPlan、rerank、支持度、语义版本关系、Claim 与实体关系抽取全部复用 CDS DecisionRun；模型输出只是候选提议。
2. 程序只接受输入白名单中的 source/candidate ID，并在应用前重新验证来源 revision/hash/status/privacy；来源变化、越权 ID、无效枚举或低置信结果 fail closed。
3. PWM Claim、Entity、Relation、WorldEvent 与 StateAssertion 是可重建的个人世界投影，不取代聊天、文件、记忆、LIFE 或 ToolRun 的权威事实。
4. 模型推断默认不能独立支持事实回答；答案必须回到可访问的权威来源 EvidenceLink。资料不足时明确不确定，不生成伪引用。
5. 敏感属性自动抽取默认关闭；高影响冲突、实体合并/拆分和用户纠正必须经过确定性门或用户确认。
6. 所有 KIG DecisionKind 初始为 Shadow。晋级必须满足共享 Provider 认证、固定集、盲评、反馈、预算和回滚门，不能因单模型表现良好直接 Active。

## 失败与回滚

模型、网络、预算或解析失败时使用确定性检索/排序或保持现状，不阻塞普通聊天。删除 PWM 派生投影后可从仍有效的权威来源重建。

## 明确拒绝

- 模型直接写 Claim/Entity/Relation 或修改来源。
- PWM 节点在无来源时作为事实回答证据。
- KIG 自建第二套通用模型运行、审计、授权或投递框架。

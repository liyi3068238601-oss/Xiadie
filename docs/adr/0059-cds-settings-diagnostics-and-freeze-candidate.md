# ADR-0059：CDS 设置、诊断与冻结候选

- 状态：Accepted as freeze candidate；待 CDS 总体独立 review 后正式冻结
- 日期：2026-07-26
- 关联：CDS.13、ADR-0051～0058、共享 Promotion Policy

## 决策

1. 普通设置只展示“更稳妥地理解当前对话、按需整理回忆与资料、从反馈中调整谨慎程度”等自然能力，不展示协议名、DecisionKind 或模型认证术语。
2. 高级设置可控制总开关、每个 DecisionKind 的模式、fast/reasoning/creative 模型角色和无正文诊断显示。模式只能等于或低于注册表上限；当前九个 DecisionKind 上限全部为 Shadow。
3. 模型角色只能绑定已启用 Provider 中已登记的模型。无效 Provider、虚构模型和超过冻结上限的模式均 fail closed。
4. 一键回退关闭全部模型决策、清空角色覆盖并恢复各领域确定性 fallback；它不删除审计、反馈或领域数据。
5. `cognition-diagnostics-v2` 只返回协议/注册表/设置版本、计数、延迟、fallback 与错误码，不返回正文、Prompt、原始模型输出、候选 ID 或来源快照。
6. EAP `eap-decision-run-adapter-v1` 保持字段和语义不变；新增 `eap-decision-run-diagnostic-v2` 提供 `error_code` 与 `latency_ms`，避免静默破坏 v1 消费者。
7. Schema 63 是 CDS 最终施工 Schema 候选。LIFE 的首个必要迁移号为 64，但必须在 CDS 独立 review 为 0 个未解决 P0/P1、协议正式冻结并更新基线后才能占用。

## 晋级结论

本阶段冻结的是协议与安全边界候选，不是运行模式晋级。当前只有一个真实 Provider；CDS.10 的小型叙事观察集仅 50% accuracy；CDS.12 两模型配对一致率 50%。这些证据不足以支持 Advisory 或 Active，因此全部 DecisionKind 继续 Shadow。

## 回滚

- 用户面：高级设置中的“一键回退到原有逻辑”。
- 运行时：关闭时不调用 Provider，直接生成带 `cognition_decision_disabled` 的可诊断 fallback。
- 数据：保留 Schema 63 和无正文审计，不执行破坏性降迁移。

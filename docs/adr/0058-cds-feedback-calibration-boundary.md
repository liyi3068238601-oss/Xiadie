# ADR-0058：CDS 反馈与个体化校准边界

- 状态：Accepted；等待 CDS 总体独立 review
- 日期：2026-07-26
- 关联：CDS.12、ADR-0051/0052/0057、共享 Promotion Policy

## 决策

1. Schema 63 新增 body-free 的 `cognition_feedback_signals`、`cognition_calibration_profiles` 与 `cognition_calibration_events`。它们只保存枚举、DecisionKind、revision、有限参数 delta 和时间，不保存用户文字、Prompt、候选正文或原始模型输出。
2. 反馈按 `recall`、`proactive`、`relationship`、`memory` 四域及具体 DecisionKind 隔离。召回反馈不能改变主动策略，关系反馈不能改变记忆参数。
3. `quick_reply`、`later_reply`、`unanswered`、`rejected`、`corrected` 保持独立语义；另有 recall/memory 使用的 `helpful`、`not_helpful`、`missing`、`wrong_source`。
4. 可调白名单仅为 `selection_bias` 与 `caution_bias`，分别限幅至 `[-0.20, 0.20]` 与 `[0, 0.40]`。`application_owner`、`fallback_owner`、`privacy_class`、模式上限、来源 revision、候选白名单、validator 和协议版本永远不可由反馈修改。
5. 相同反馈在并发下只应用一次；回滚只复位一个 DecisionKind 的参数，不删除反馈审计，也不影响其他决策器。
6. 校准 profile 当前只作为 Shadow 建议元数据，不接入生产领域写路径。所有 DecisionKind 继续保持 Shadow，不因 profile 或模型认证自动晋级。

## Provider 证据

项目当前只有 DeepSeek 一个启用且具备密钥的真实 Provider，因此无法满足跨 Provider 晋级门。对其两个配置模型执行纯合成 structured probe 和配对测试：`deepseek-v4-pro` 6/6 精确合规，`deepseek-v4-flash` 本轮 3/6 合规，出现 2 次 JSON repair failure 与 1 次模型调用失败；模型间一致率 50%。两者 structured probe 均至少成功一次，但这些证据只支持保持 Shadow，不支持 Advisory/Active。

## 回滚

- profile：按 DecisionKind 调用 rollback，revision 单调增加并保留无正文事件。
- 数据结构：回滚应用版本时保留 Schema 63 表，不静默删除用户反馈历史。
- 生产行为：当前未接线；旧算法和领域写者始终保持实际行为。

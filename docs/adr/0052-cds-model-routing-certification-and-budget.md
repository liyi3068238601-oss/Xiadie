# ADR-0052：CDS 模型绑定认证、位置门禁与预算控制面

- 状态：Accepted；CDS.2 strict review 已通过（0 P0/P1）
- 日期：2026-07-22
- 关联：CDS.2、ADR-0051、`cognitive-decision-v1`

## 决策

1. `fast`、`reasoning`、`creative` 是逻辑角色，不是新的 Provider 类型。角色绑定优先读取 `cognition_model_bindings`，缺省复用 `current_model` 与现有 `providers` 行。
2. binding revision 由 provider、model、role、execution location 与 location revision 共同生成。认证键还包含 decision kind 和 protocol version；任一项变化都不得继承旧资格。
3. 自定义或未认证绑定首次进入认知任务时只运行不含用户数据的 exact-JSON structured probe。失败保持 `unverified`，只走注册 fallback；Shadow 至少需要 `structured_capable`，Advisory/Active 至少需要 `decision_verified`。
4. 含正文任务对 `unknown` 与 `remote` 位置默认拒绝；本地敏感任务还要求 `local_sensitive_verified`。后续若允许远端正文，必须由专属隐私授权协议升级，不得由 CDS 暗中放宽。
5. 超时来自每个 `DecisionKindDefinition`；熔断按 binding + decision kind + protocol 隔离。Provider、超时、解析、认证、预算或熔断失败均返回该 decision kind 已注册的确定性 fallback，不向聊天抛出异常。
6. `CognitionBudgetGovernor` 记录无正文预算事件，控制滚动/每日 token、本地/远端并发、前台压力、网络、电池、取消与优先级。新用户消息只取消尚未开始的低优先级 diary/PWM/offline refinement。

## 数据

Schema 62 继续扩展唯一 `decision_runs`，只增加 `logical_role`、`provider_location_revision` 与 `certification_level`。另建认证、熔断和预算事件三张控制面表；它们不保存 Prompt、用户正文、候选正文或原始模型输出，也不是平行 DecisionRun。

## 后果

- 当前生产注册表仍只有合成 `protocol_probe` 且最高 Shadow，CDS.2 不改变聊天生成和任何领域事实。
- 模型切换、Provider 位置修订和协议升级会自然失去旧认证，需重新 probe/评测。
- 远端正文授权、真实 decision kind 的 Advisory/Active 晋级以及诊断细粒度权限分别留给后续专项和 CDS.13。

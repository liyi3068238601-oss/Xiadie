# LIFE.1 CDS DecisionRun 接入报告

- 日期：2026-07-26
- 协议：`cognitive-decision-v1` / `decision-kind-registry-v1`
- LIFE 输入/结果：`life-decision-input-v1` / `life-decision-result-v1`
- 模式上限：Shadow
- 数据库迁移：无；当前仍为 Schema 63

## 结论

LIFE 复用 CDS 唯一 `decision_runs` 与 `decision_run_events`，没有新增平行通用账本。六类 LIFE 任务均具有有限候选、专属结构化输入/输出、确定性 skip fallback、8 秒超时和短期无正文诊断契约。

应用前必须由 LIFE 来源所有者重新读取 `kind/id/revision/content_hash`。来源集合、revision 或 hash 任一变化都会拒绝旧结果；即使模型返回有效结果，Shadow 模式也不会授予写入权。

## 隐私与失败边界

- 必要摘要和不可信 JSON 只作为瞬时输入，不写入 DecisionRun。
- 原始模型输出、Prompt 和用户正文不落库。
- 仅记录模型标识、位置、token、延迟、错误码、警告、候选/选中数量和版本 hash。
- Provider 不可用、格式错误、提示形额外字段或 validator 异常均确定性降级为 skip，不阻塞聊天或应用启动。

## 验证

- LIFE.1 专项：9 passed。
- LIFE.1 + CDS 认知协议相关回归：26 passed，1 warning。
- 固定回放：6 个纯合成样本，覆盖全部 LIFE 决策白名单。

阶段独立 Review 按用户要求在 LIFE 全专项完成后统一进行；本报告是 review-ready 施工证据，不代表外部 Review 已通过。

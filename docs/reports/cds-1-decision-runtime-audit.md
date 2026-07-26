# CDS.1 DecisionRun 复用审计与 review 处置

> 日期：2026-07-22
>
> 施工基线：`c14555b`（CDS.0）
>
> 状态：CDS.1 施工完成，等待独立 review；未进入 CDS.2。

## 1. CDS.0 strict review 处置

外部 strict review 结论为通过：0 个 P0、0 个 P1、3 个 P2，批准进入 CDS.1。结合当前 Git 历史复核后，结论予以采纳，但修正两项范围归属：六个 EAP 协议的 `FROZEN` 状态及 `timeline_simulator.production_turn` 的显式 `if` 已存在于 CDS.0 的前置合并提交 `6b8aa47`，不是 `c14555b` 新增；这不影响 CDS.0 验收结论。

| Review 建议 | 决定 | 实际处置 |
|---|---|---|
| P2-1：`outcomes.detail` 缺少字段白名单 | 采纳其未来运行时风险，不回写冻结 CDS.0 报告 | CDS.1 账本和诊断只返回固定元数据字段；不保存 detail、正文、Prompt 或原始模型输出；测试检查底层表与 HTTP 输出 |
| P2-2：CTX `may_select` 语义过窄 | 延后 CDS.7 | 这是 ContextPlanner 的任务级语义策略；修改 CDS.0 标签会污染旧算法配对基线 |
| P2-3：Relationship 缺模型可用场景 | 延后 CDS.8 | CDS.0 明确测量无模型 `unknown_fallback`；模型可用对照应与 RelationshipMeaning 专属 Schema 和固定集一起加入 |

## 2. Schema 56 现状审计

| 对象 | 当前事实 | CDS.1 决定 |
|---|---|---|
| `decision_runs` | Schema 56 唯一共享表；已有任务/协议、单来源 revision/hash、幂等、状态、重试、Provider/Model、延迟/token、错误与 warning | 原表扩展，不建立 `cognitive_decision_runs` 或 `cds_decision_runs` |
| `app.proactive.run_ledger` | 已有 create/get/transition、乐观状态转换、重试预算、旧表只读 adapter | 保持旧签名兼容；增加 CDS 元数据、结果白名单、事件及只读诊断 |
| 真实消费者 | `proactive.cognition_service` 创建 `companion_cognition` run，并由 `companion_cognition_results` 保存领域结果 | 不修改冻结 EAP 协议或结果表；新增 CDS 只作为共享 adapter/runtime |
| 公共事件 | Schema 56 没有 `decision_run_events`；状态变化只有当前行 | Schema 61 增加从属于同一 run 的无正文状态事件，不是平行账本 |
| 历史领域 run 表 | affect、memory observer、episode/saga consolidator、archivist、knowledge import/delete/embed、conversation summary 共九类 | 所有权保持原领域；不迁移、不双写，必要时只读适配 |

## 3. 字段复用/补差矩阵

| 规范字段 | 复用/补差 |
|---|---|
| `decision_kind` | 复用 `task_kind`，不增加同义列 |
| `protocol_version` | 复用原列 |
| `source_revision` | 兼容原列；新 CDS run 同时写 `source_snapshot[] + snapshot_hash` |
| `provider_id/model/latency/token/error/status` | 复用原列；`completed_at` 映射规范 `finished_at` |
| `policy_version/mode/provider_location` | Schema 61 新增 |
| 多来源逐项快照与聚合 hash | Schema 61 新增；应用前逐项及聚合双重复核 |
| candidate count/hash、selected count、action、confidence、reason | Schema 61 新增，只存无正文聚合与枚举 |
| prompt/schema/validator/fallback/model binding/temperature/top_p | Schema 61 新增，用于可复现审计 |
| retention/privacy/expiry | Schema 61 新增；HTTP 诊断隐藏过期 run 与事件 |
| raw model output / Prompt / 用户或候选正文 | 明确禁止存储，无对应列 |

上述缺口无法由 Schema 56 原字段无歧义表达，因此按计划占用首个可用 Schema 61。Schema 48～60 不回写。

## 4. CDS.1 实现边界

- `CommonDecisionHeader` 固定 protocol、decision kind、policy、request、mode 和多来源快照。
- `DecisionKindRegistry` 强制每个 kind 使用专属输入/结果类型及版本化 hash。
- 候选只以 ID/source kind/content hash 进入协议；模型选择必须属于白名单。
- JSON 只允许一次有界修复，失败走注册 fallback。
- Shadow、Advisory、Active 均有程序门禁；当前唯一生产注册项 `protocol_probe` 最高只允许 Shadow。
- `/api/cognition/diagnostics` 为只读、无正文、严格字段白名单接口。
- 本阶段不调用 Provider、不改变聊天或领域状态，也没有 CDS Active decision kind。

## 5. 下一步门禁

CDS.1 独立 review 必须确认 0 个未解决 P0/P1，且非候选 ID 应用、来源变化后应用、协议失败影响聊天、重复应用四项均为 0，才能进入 CDS.2。未确认前停止施工。

施工自验：CDS.1 专项与兼容测试通过；后端全量 `957 passed, 1 warning`，前端 `41 passed`，Vite production build 188 modules，改动范围 Ruff 通过。新增运行时未接入聊天调用链，完成门四项均由程序门禁和回归测试保持为 0。

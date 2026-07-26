# CDS、LIFE、KIG 专项所有权与共享施工契约

- 版本：v1.0
- 日期：2026-07-22
- 状态：CDS 已通过最终独立 Review 并正式冻结；LIFE 协议门已解除
- 适用顺序：`CDS → LIFE → KIG`
- 解释优先级：冻结协议与 ADR > 本矩阵 > 专项计划 > 阶段施工记录

## 1. ConstructionBaseline

每个专项的第 0 阶段必须把以下记录写入阶段报告；字段不完整时只能审计，不能新增迁移或生产写路径。

```text
ConstructionBaseline
├─ repository
├─ predecessor_pr
├─ base_branch
├─ base_commit_sha
├─ schema_version
├─ frozen_protocols
├─ test_baseline
├─ plan_version
└─ recorded_at
```

当前前置基线：

| 字段 | 当前值/规则 |
|---|---|
| repository | `liyi3068238601-oss/Xiadie` |
| predecessor_pr | EAP PR `#1`，已合并 |
| base_branch | `main` |
| integration state | EAP 已技术冻结并合入 `main`；CDS.0～13 已施工并通过最终独立 Review，待合入目标基线后填写 LIFE.0 ConstructionBaseline |
| base_commit_sha | CDS ConstructionBaseline：`6b8aa47134f8a9a55131c73bb1148e6912421c4f` |
| schema_version | ConstructionBaseline 60；CDS.1/CDS.2 为 61/62；CDS.12 当前 63；Schema 48～60 不回写 |
| frozen_protocols | CTX v1；EAP 六协议；CDS `cognitive-decision-v1`、`decision-kind-registry-v1`、`specialty-adapter-contract-v1`；以 Protocol Registry、ADR 和冻结报告为准 |
| test_baseline | ConstructionBaseline：后端 `937 passed, 1 warning`；当前：后端 `2304 passed, 1 warning`、前端 `47 passed`、Vite 189 modules |
| plan_version | CDS/LIFE/KIG v0.3；本矩阵 v1.0 |
| recorded_at | 2026-07-22；各专项开工时重新记录 |

正式开工只允许两种方式：

1. 前置 PR 已合并，以 `main` 的不可变合并提交作为基线；这是默认方式。
2. 用户明确批准从前置专项的固定 commit SHA 开工，并记录偏离原因、迁移号所有权和后续合并策略。

禁止从旧 `main` 开工后再合入前置大分支，也禁止两个专项并行占用迁移号。

## 2. 唯一所有权矩阵

| 对象/能力 | 唯一所有者与最终写入者 | 可读/可提议者 | 冻结或目标协议 | 删除语义 |
|---|---|---|---|---|
| Conversation Presence | EAP | CDS/LIFE/KIG 只读；EAP Observer 提议 | `conversation-presence-v2` | EAP 管理 |
| Affect / Relationship | Affect/EAP Reducer | CDS/MEM 可提议 | `affect-observer-v1`、`relationship-meaning-v1` | 不因派生层删除而自动删除 |
| Proactive Candidate/Delivery/Feedback | EAP | LIFE/KIG 只提供来源化种子 | EAP 冻结协议组 | EAP 状态机与用户清除规则 |
| ContextPackage / 最终预算装配 | CTX | CDS/KIG 只提优先级或 Retrieval 建议 | context v1；行为改变须 v2 ADR | 可重建派生包 |
| DecisionRun / 通用决策运行时 | CDS；Schema 56 现有 repository 为复用起点 | 所有领域注册任务并读取诊断 | `cognitive-decision-v1`（CDS 冻结目标） | 按保留策略清理诊断，不删领域事实 |
| Fragment/Episode/Saga | MEM | CDS/KIG 只产生领域提案 | 现有 MEM validator/reducer | MEM 生命周期 |
| KnowledgeDocument/Chunk/Search/Citation | 现有 Knowledge；KIG 只做补差治理 | CDS 质量评测；CTX 消费结果 | 现有 knowledge search/citation contract | Knowledge 删除级联 |
| LifeEvent/Schedule/Diary/ImportantDate | LIFE | EAP/KIG/CTX 只读或消费种子 | LIFE v1 目标协议 | LIFE 撤销、删除与压缩规则 |
| SourceRef/Evidence/PWM | KIG | CTX/MEM 消费派生结果 | KIG-R / KIG-P 目标协议 | 来源失效传播；派生层可重建 |
| ToolRun/真实外部执行 | ToolRegistry（未来专项） | LIFE/KIG 只读证据 | 当前只保留 adapter 位 | 工具审计所有者管理 |

任何专项都不得因为“需要读取”而成为第二个正式写入者。

## 3. Adapter 与迁移契约

| 所有者 | adapter_version | source_revision_format | fallback_owner | temporary_chat_behavior | remote_transfer_policy | migration_owner |
|---|---|---|---|---|---|---|
| CTX | `context-adapter-v1` | 对话/组件 revision + hash | CTX 固定预算 | 仅当前会话、无跨会话读取 | 继承 CTX 来源授权 | CTX 新协议 ADR |
| EAP | `eap-decision-run-adapter-v1`；诊断 v2 | source kind/id/revision/hash | EAP 确定性安全门 | 不形成跨会话 Presence/关系/主动事实 | EAP 设置与 Provider 策略 | EAP 协议升级 |
| CDS | `cognitive-decision-v1` / `specialty-adapter-contract-v1` | `source_snapshot[]` + aggregate hash | DecisionKind 注册的领域 fallback | 无持久化应用；只允许短期无正文诊断 | 按 decision_kind 隐私级别 | CDS 通用表；领域字段归领域专项 |
| MEM | `memory-adapter-v1` | memory id/revision/hash | MEM 既有算法 | 不读写长期记忆 | 继承记忆远传策略 | MEM |
| Knowledge | `knowledge-adapter-v1` | document/chunk revision/hash/locator | 现有 FTS/Dense 降级 | 文件逐次授权，不进入长期派生层 | transmission policy/grant | Knowledge/KIG 补差阶段 |
| LIFE | `life-adapter-v1` | event/state/schedule revision/hash | LIFE 确定性 reducer | 不生成长期 LifeEvent、Goal、Date、Diary | 日记/生活数据单独授权 | LIFE |
| KIG | `source-ref-v1` | adapter registry 返回的 revision/hash | 原系统继续工作 | 不抽取 Claim/Entity/Relation/PWM | 逐来源隐私与授权 | KIG |

迁移号严格串行：CDS 最终冻结 Schema 为 63；LIFE 可在锁定已合并 predecessor commit 和 LIFE.0 ConstructionBaseline 后使用首个确有必要的 Schema 64；KIG 使用 LIFE 最终版本 + 1。没有实际字段缺口不得为了“占号”创建空迁移。

## 4. DecisionKindRegistry 规范

通用执行器只定义 `CommonDecisionHeader`，每个任务必须注册专属输入和结果 Schema，禁止万能自由 JSON。

```text
DecisionKindRegistry
├─ decision_kind
├─ input_schema_version / input_schema_hash
├─ output_schema_version / output_schema_hash
├─ validator / validator_version
├─ fallback / fallback_version / fallback_owner
├─ application_owner
├─ privacy_class
├─ max_candidates / timeout / result_ttl
├─ model_binding_revision
└─ mode
```

依赖多个来源的运行必须保存 `source_snapshot[]` 与 `snapshot_hash`，逐项包含 `kind/id/revision/content_hash`。应用前逐项复核并复核聚合 hash。为保证可复现性，DecisionRun 至少记录：

```text
prompt_template_hash
input_schema_hash
output_schema_hash
validator_version
fallback_version
model_binding_revision
temperature
top_p
candidate_snapshot_hash
```

原始模型输出默认不落库。

## 5. Decision Promotion Policy

### 5.1 Shadow → Advisory

必须同时满足：

- 固定评测集和算法版本已冻结，每个关键分层达到计划规定的最低样本数；
- 新旧算法使用同一输入做配对比较；
- 非候选 ID、来源失效应用、越权写入、重复应用等零容忍指标均为 0；
- 至少两个目标 Provider 完成评测；只有一个可用 Provider 时保持 Shadow 并记录限制；
- 独立 Review 为 0 个未解决 P0/P1。

### 5.2 Advisory → Active

还必须满足：

- 真实 Shadow 样本达到专项门槛且关键子场景均有覆盖；
- 盲评显著优于旧算法，报告总体值、重要子场景、置信区间或样本量，不只报告平均值；
- 延迟、token、并发和失败率未超过预算；
- 一个 feature flag 即可回滚，旧算法至少保留一个发布周期；
- 当前模型已取得对应 decision_kind 的 `decision_verified` 认证。

“自然度 ≥ 90%”统一定义为 `acceptable / 有效样本`。acceptable 可有轻微瑕疵但不影响使用；机械重复、事实错位、越界、明显打扰或角色失真均为 unacceptable。主观评测隐藏新旧来源，至少两轮独立评审，分歧样本仲裁。

## 6. 模型认证等级

认证按 `model binding + decision_kind + protocol version` 保存，用户切换模型后不得继承旧模型资格。

| 等级 | 允许范围 |
|---|---|
| `unverified` | 普通聊天；后台决策仅 Shadow 或使用 fallback |
| `structured_capable` | 通过最小结构化探测，可用于低风险 Advisory |
| `decision_verified` | 通过该 decision_kind 固定评测，可按晋级规范 Active |
| `local_sensitive_verified` | 额外允许处理已授权日记、私密知识和生活数据 |

自定义 OpenAI-compatible 模型首次用于认知任务时必须执行最小结构化探测；失败时保持旧算法。认证不替代传输授权。

## 7. CognitionBudgetGovernor

CDS 负责通用预算与调度契约，领域专项只声明任务成本、优先级和是否可取消：

```text
rolling_token_budget / daily_background_budget
max_concurrent_remote_calls / max_concurrent_local_calls
foreground_latency_budget
network_state / battery_mode
cancellation / task_priority
```

默认优先级：当前聊天 > 本轮召回/重排 > 必要对话后观察 > EAP 时效候选 > LIFE 当前时段物化 > 离线续演 > 日记 > MEM 整理 > KIG Claim/PWM 维护。用户再次发消息时，可取消尚未开始的日记、PWM 和离线细化任务；已进入原子写入段的任务只能安全完成或回滚。

## 8. 临时聊天、保留、删除、导出与恢复

### 临时聊天

- CDS：只做当前轮无持久化决策；DecisionRun 不落库或只留带短 TTL 的最小诊断。
- LIFE：不生成长期 Goal、ImportantDate、Diary、ContinuityThread 或 LifeEvent。
- KIG：不抽取 Claim、Entity、Relation、WorldEvent，不进入长期 PWM；知识文件逐次授权。

### 诊断保留

DecisionRun、RetrievalTrace 等元数据统一具备 `retention_class/expires_at/privacy_scope/aggregate_after_expiry`。失败诊断默认 30 天，Shadow 对照 30～90 天，冻结验收样本可长期版本化保留但不得含正文；原始模型输出默认不保存。

### 导出与恢复

```text
export_manifest.json
├─ schema_version / subsystem_versions / protocol_versions
├─ source_checksums
├─ included_data_classes / excluded_private_classes
└─ dependency_order
```

恢复顺序固定为：原始聊天/文件/记忆 → LIFE 权威账本 → CDS 元数据 → KIG 派生层重建。删除始终由权威所有者执行并向派生层传播；KIG/PWM 删除不得反向删除原始来源。

## 9. 机械施工门禁

- 修改非本专项所有对象的生产写路径：阻断。
- 使用尚未合并或未锁定 SHA 的前置分支开工：阻断。
- 未经协议升级直接修改冻结 Schema/枚举/语义：阻断。
- 在临时聊天产生长期派生事实：阻断。
- 未认证模型进入 Active：阻断。
- 无有效来源、非候选 ID、来源 revision 失效仍应用：阻断。
- 没有回退与单开关回滚路径：阻断。

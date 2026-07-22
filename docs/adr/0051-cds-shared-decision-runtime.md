# ADR-0051：CDS 复用共享 DecisionRun 并以 Schema 61 补齐协议元数据

- 状态：Accepted；CDS.1 strict review 已通过（0 P0/P1）
- 日期：2026-07-22

## 决策

CDS 不建立第二套通用 run 或 event 账本。Schema 56 的 `decision_runs` 与 `app.proactive.run_ledger` 继续作为唯一共享运行账本；Schema 61 只补充当前表无法表达的字段，并新增其从属的 `decision_run_events` 状态事件表。

Schema 61 增加以下能力：

- `policy_version` 与 `shadow/advisory/active` 模式；历史 EAP 行兼容标记为 `legacy`；
- `source_snapshot_json`、逐来源 revision/hash 与聚合 `snapshot_hash`；
- 候选快照 hash、候选数、选择数及白名单验证结果；
- prompt/schema/validator/fallback/model binding 与采样参数；
- 只含动作、置信带、原因码、错误码、token 和延迟的无正文诊断；
- `retention_class/expires_at/privacy_scope/aggregate_after_expiry`；
- 同事务写入的公共状态事件。

`CommonDecisionHeader` 与 `DecisionKindRegistry` 由 `cognitive-decision-v1` 提供。每个 decision kind 必须注册专属输入和结果 dataclass、Schema hash、验证器、fallback、所有者、隐私级别、候选上限、超时、TTL、model binding 与最高获准模式。通用执行器不接收自由 `context/candidates/effects` JSON。

首个注册项 `protocol_probe` 只用于合成、无正文的协议验证，并固定为 Shadow。后续真实领域 decision kind 由对应 CDS 阶段注册；在模型认证阶段完成前，生产注册表没有 Advisory 或 Active 项。

## 安全边界

- 模型只能返回本轮候选 ID；非候选 ID 强制 fallback，应用率为 0。
- 应用前逐项复核 source kind/id/revision/content hash，再复核聚合 hash。
- JSON 最多进行一次有界结构提取；仍失败时使用注册 fallback。
- Shadow 永不影响真实行为；Advisory 只返回建议；Active 还需要注册表授权、来源有效和调用方显式 application gate。
- 原始模型输出、Prompt、用户正文和候选正文不落 `decision_runs`、事件或诊断 API。
- 诊断使用固定字段白名单，并隐藏过期记录；领域事实的生命周期仍由领域所有者管理。

## 兼容与回滚

现有 `companion_cognition` 消费者继续使用原 repository API，不需要修改冻结的 EAP 协议。Schema 60 及以前的行由列默认值兼容；九类历史领域 run 表不迁移、不重写，只能通过现有只读 adapter 暴露公共身份字段。

回滚功能时可关闭/移除 CDS 注册与诊断入口，旧 EAP consumer 仍可使用扩展后的共享表。SQLite 不执行破坏性降级；正式发布前仍需按项目路线完成备份、恢复和迁移演练。

## 理由

Schema 56 已提供共享身份、幂等、重试、状态和模型计量，但无法表达 CDS v0.3 要求的多来源复核、专属 Schema 绑定、三模式、候选审计、诊断 TTL 和复现实验参数。这是可验证的真实字段缺口，因此占用首个可用 Schema 61；扩展原表比新建平行账本更能保持 EAP 消费者兼容和单一审计来源。

## 验收

- 旧 repository 与 `companion_cognition` 回归通过。
- Schema 61 迁移、唯一共享表、事件审计与字段完整性受测试锁定。
- 非候选 ID、来源变化、重复请求、JSON 修复和三模式门禁均有测试。
- 只读诊断 API 不包含来源/候选正文、快照 hash 或原始模型输出。

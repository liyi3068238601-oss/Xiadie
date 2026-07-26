# CDS 最终兼容矩阵（冻结候选）

> 日期：2026-07-26；Schema 63；待 CDS 总体独立 review

| 边界 | 版本/状态 | 唯一应用所有者 | CDS 权限 | 兼容与回退 |
|---|---|---|---|---|
| 通用决策 | `cognitive-decision-v1` / `decision-kind-registry-v1` | 各 DecisionKind 领域 owner | 运行、校验、无正文账本 | 九种决策最高 Shadow；领域 fallback |
| 来源快照 | `decision-source-snapshot-v1` | 来源领域 | revision/hash 复核 | 变化即 fail closed |
| 模型绑定 | `cognition-binding-v1` | CDS 配置层 | fast/reasoning/creative | 无效绑定拒绝；否则跟随当前模型 |
| 反馈校准 | `cognition-feedback-v1` / profile v1 | CDS 元数据 | 两个限幅参数 | 不接生产应用；按决策器回滚 |
| 设置/诊断 | settings v1 / diagnostics v2 | CDS | 模式上限、无正文聚合 | 全局一键回退，不删审计 |
| EAP 读取 | adapter v1 + diagnostic v2 | EAP 保留候选/投递/反馈写权 | 只读 EAP-owned DecisionRun | v1 不变；v2 增量字段 |
| LIFE/KIG 预留 | `specialty-adapter-contract-v1` | LIFE/KIG 各自领域 | 只验证 revision/hash 与候选信封 | 无领域表、无生产消费者、无应用授权 |
| CTX | context v1 | CTX | 只提 Shadow 优先级 | 未证明收益，不提交 context v2 |
| MEM | 既有 validator/reducer | MEM | 只提 conflict/retention/Episode/Saga proposal | CDS 不写正式 Fragment/Episode/Saga |
| Knowledge | 现有 KnowledgeResult/EvidenceWindow | Knowledge/CTX | 质量评测与无正文候选信封 | 不定义 KIG RetrievalBundle |

迁移序列：CDS ConstructionBaseline 60 → CDS 61/62 → CDS 反馈校准 63 → LIFE 首个必要迁移候选 64。64 只是在独立 review 后可用，不代表 LIFE 已开工。

# ADR-0055：CDS 记忆冲突与保留纯 Shadow 边界

- 状态：Accepted；CDS.9 独立复审通过
- 日期：2026-07-25
- 关联：CDS.9、ADR-0051/0052、`fragment-conflict-v1`、`fragment-retention-v1`

## 决策

1. CDS 注册 `memory_conflict_proposal` 与 `memory_retention_proposal` 两个独立 DecisionKind；各自绑定专属输入/输出 Schema、validator 与纯确定性 fallback，最高模式固定为 Shadow。
2. 冲突提案只表达 compatible、possible conflict、conditional difference 与 supersedes；明确否定由同级或更强的新来源覆盖旧来源，自动或系统注入来源不得覆盖用户确认来源，任何 supersedes 只能指向输入候选中的旧 Fragment。
3. 保留提案只表达 keep、cool、freeze 与 reconsolidate；仅用户确认的新证据可以建议 frozen 记忆再巩固，系统注入或自动来源不能触发恢复。
4. 两类结果必须保持 `advisory_only=true` 与 `tombstone_allowed=false`。CDS 不调用 `scan_conflicts`、`assess_and_transition`、`reactivate_fragment` 或其他 MEM 写入函数。
5. MEM 继续拥有候选、Validator、Reducer、Fragment、Episode、Saga 和 tombstone。冲突 fallback 调用 `memory_conflicts.classify_projection`，保留 fallback 调用 `archivist.project_lifecycle`，两者均与生产路径共用纯投影，不调用事务写入。
6. Schema 保持 62，不增加迁移、表、列或第二套记忆状态。
7. 只读 adapter 从真实 `memory_fragments` 读取并绑定 `lifecycle_revision`、正文与状态聚合 hash、status、enabled、sensitivity 和 observation_source；来源变化后共享运行时必须跳过旧结果。
8. Shadow 会写共享 `decision_runs` 与 `decision_run_events` 账本，但不得写 MEM 领域表；报告必须分开统计两类写入。

## 证据

- 280 个纯合成场景由 14 组组成，覆盖两个 DecisionKind；提案精确匹配率 100%。
- 弱来源覆盖、仅注入恢复、tombstone 提案和 MEM 领域表写入均为 0；共享 Shadow 账本有预期写入。
- 评测使用真实 Fragment adapter 和独立 `cds9-memory-safety-oracle-v3`，oracle 不读取 fixture 的 expected 字段。
- 评测报告不保存输入正文、Prompt 或原始模型输出，只保存 case ID、枚举、计数和 fixture hash。

## 晋级条件

CDS.9 必须经独立 review 确认 0 个未解决 P0/P1。任何 Advisory 或 Active 讨论都必须另立协议，重新验证来源 revision/hash，并由 MEM 所有者提供正式 application adapter；本 ADR 不授权生产应用。

# ADR-0064：MEM 实体保持权威，PWM 实体为单向派生投影

- 状态：Accepted for KIG construction
- 日期：2026-07-27
- 关联：KIG.0、MEM Fragment/Episode/Saga、PWM

## 决策

1. `memory_entities` 继续是相处记忆领域的权威实体，现有 Fragment/Episode/Saga 关系和生命周期不迁移到 PWM。
2. `pwm_entities` 只表示跨知识来源的可重建派生实体，使用独立 `pwm_` 前缀、白名单类型、来源链接、置信度和生命周期。
3. MEM 可以向 KIG 提供只读 SourceRef；KIG 可以产生“建议 MEM 关联/纠正”的 proposal，但不得直接写 `memory_entities` 或关系表。
4. 用户在 MEM 中的纠正、合并、删除与隐私清除由 MEM 执行，并通过来源 revision/hash/status 变化使 PWM 依赖 stale 或 revoked。
5. PWM 合并或拆分不反向改变 MEM 实体；同名不等于同一实体，自动合并必须受来源、类型、别名、时间和冲突硬门约束。
6. 删除依赖顺序为：权威来源先执行自己的规则，KIG 再撤销派生链接和投影；删除 PWM 永不删除 MEM 数据。

## 失败与回滚

MEM adapter 不可用时，相关 PWM 投影停止作为有效证据但不猜测来源状态。KIG 回滚只移除 `pwm_` 派生数据和 proposal，不触碰 MEM 权威表。

## 明确拒绝

- 把 `memory_entities` 复制迁移为 PWM 后废弃原表。
- PWM reducer 直接修改 Fragment、Episode、Saga 或关系。
- 仅凭名称相似自动合并用户实体。

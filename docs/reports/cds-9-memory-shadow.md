# CDS.9 记忆冲突、保留与再巩固纯 Shadow 评测

- 样本：280 个纯合成变体，覆盖 14 个规则模板；不含用户数据，不调用 Provider。
- Fixture SHA-256：`15443e640c7cf4d63ecb56f2af569dc73520f4cc9c7334e32c67732db6c86904`
- DecisionKind：冲突 160；保留 120。

## 完成门

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| 提案精确匹配率 | 100.00% | 100% |
| 弱来源覆盖率 | 0.00% | 0 |
| 仅注入证据恢复率 | 0.00% | 0 |
| tombstone 提案率 | 0.00% | 0 |
| advisory_only 保持率 | 100.00% | 100% |
| Shadow 共享账本写入表数 | 2 | >0 |
| MEM 领域表写入数 | 0 | 0 |

## 边界

- CDS.9 阶段未新增迁移、表或列；阶段基线为 Schema 62，当前项目为 Schema 76。
- memory_conflict_proposal 与 memory_retention_proposal 使用独立输入/输出 Schema，均固定 Shadow。
- 独立 oracle：`cds9-memory-safety-oracle-v3`，不读取 fixture expected。
- fallback 复用 MEM 纯投影；Shadow 只写共享 DecisionRun 账本，MEM 仍是唯一 application owner。
- 自动或系统注入来源不能覆盖用户确认；仅注入证据不能恢复 frozen 记忆。
- CDS 不写 Fragment、关系、生命周期、Episode 或 Saga 正式状态，也不能产生 tombstone。

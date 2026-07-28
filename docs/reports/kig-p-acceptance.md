# KIG-P 最终验收

- 协议：`kig-v1`（检索治理继续使用 `kig-retrieval-governance-v1`）
- Schema：80
- 实现 HEAD：`96021838418d5c5d9d26b269784447a099a68cc3`
- 数据：纯合成，不含用户数据，Provider 调用 0

## 场景与质量

| 场景 | 数量 | Knowledge 召回 | Memory 召回 |
|---|---:|---:|---:|
| `single_document` | 100 | 100.00% | — |
| `multi_document` | 100 | 100.00% | — |
| `cross_store` | 100 | 100.00% | 100.00% |

- 引用 allowlist 准确率：100.00%。
- 版本/纠正场景：100，正确率 100.00%。
- 实体自动 exact merge：100，精确率 100.00%；回滚恢复率 100.00%。
- 检索延迟 P50/P90：27.610/29.738 ms。

## Chunk 压力阶梯

| Chunk | 建库 ms | 查询 P50/P90 ms | 探针召回 |
|---:|---:|---:|---:|
| 10,000 | 14.112 | 0.005/0.035 | 100.00% |
| 100,000 | 137.207 | 0.010/0.024 | 100.00% |
| 250,000 | 253.958 | 0.013/0.035 | 100.00% |

首版目标规模校准为 25 万 Chunk；所有查询限制 5 条返回，不以扩大结果集换召回。

## 硬预算

- 每来源 Claim：64 条后拒绝第一个超额写入。
- 单实体 alias：16 条后拒绝第一个超额写入。
- 单次消歧：2 条；维护检查：100 条。
- 低置信候选 TTL、每日实体、孤立节点归档同属 `pwm_budget_policy`，由数据库计数与维护 worker 执行。

## 零容忍

- `unsourced_pwm_objects` = 0
- `performed_without_tool_run` = 0
- `unconfirmed_owner_deletions` = 0
- `sensitive_attribute_auto_extractions` = 0
- `reality_lore_cross_scope_merges` = 0
- `memory_pwm_bidirectional_overwrites` = 0

## Provider 与降级

- 离线：安全跳过 Shadow 写入；KIG-R 和 owner systems 保持可用。
- 未授权远程正文：调用前阻断。
- Provider/模型切换：旧模型证书不继承，必须按指纹重新认证。
- 预算不足：有界跳过，不扩大候选或偷偷降级隐私。

## 结论：`pass`

所有自动维护仍只生成候选；PWM 可整层重建，不拥有 Knowledge/MEM/LIFE/EAP/Tool 的权威写入权。

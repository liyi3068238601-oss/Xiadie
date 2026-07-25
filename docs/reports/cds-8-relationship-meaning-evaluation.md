# CDS.8 RelationshipMeaning 兼容评测

- 样本：120 个纯合成场景；不含用户数据，不调用真实 Provider。
- Fixture SHA-256：`a81e89687a210c23f1895b17214fee3e9022541f05528b7c3d0d4c572600ae92`
- 冻结 Schema 校验通过率：100.00%
- 共享 DecisionRun 终态率：100.00%
- EAP 建议应用率：100.00%
- 真实 enqueue/worker 应用率：100.00%
- Provider 边界调用率：100.00%
- 终态不变量满足率：100.00%

## 完成门

| 指标 | 命中/分母 | 结果 | 门槛 |
|---|---:|---:|---:|
| 普通问答导致 bond 增长率 | 0/12 | 0.00% | ≤1% |
| 沉默导致 bond/trust 下降率 | 0/12 | 0.00% | 0 |
| 单轮超限关系变化率 | 0/108 | 0.00% | 0 |

## 兼容性

- 标签精确匹配：100.00%。
- 幂等复用：100.00%；重复应用变化率：0.00%。
- trust 证据约束验证：100.00%。

## 边界

- 确定性结构化替身先经过现有 Companion Cognition 与 relationship-meaning-v1 Schema，再进入共享 DecisionRun 和 EAP 应用链。
- EAP 保持唯一关系写入者；Affect 与 Relationship 所有权未合并。
- 未修改冻结生产协议、Schema、迁移或聊天模型路径；未发现需要 relationship-meaning-v2 的兼容缺口。

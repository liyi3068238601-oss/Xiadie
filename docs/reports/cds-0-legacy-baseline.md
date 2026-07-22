# CDS.0 旧算法离线基线报告

- 固定提交：`6b8aa47134f8a9a55131c73bb1148e6912421c4f`（PR #1 合并后的 `main`）
- Schema：60；测试基线：`937 passed, 1 warning`
- 评测协议：`cognitive-decision-eval-v1`；fixture SHA-256：`1ecad02a68c1cce99948c0e9842bf8462b9e747c8ddced8e3f9bb8e122c7d02c`
- 样本：300 条纯合成场景，不含用户数据；不调用真实 Provider

## 总体指标

| 样本 | 精确匹配 | 误选 case | 漏选 case | 平均延迟 ms | P95 ms | 估算 token 总量 |
|---:|---:|---:|---:|---:|---:|---:|
| 300 | 63.67% | 33.00% | 18.00% | 0.209549 | 1.330395 | 7224 |

## 分轨指标

| 轨道 | 样本 | 精确匹配 | 误选数 | 漏选数 | 平均延迟 ms | 平均 token |
|---|---:|---:|---:|---:|---:|---:|
| `context_fixed_budget` | 50 | 10.00% | 180 | 0 | 1.171232 | 132.48 |
| `history_intent` | 50 | 60.00% | 10 | 10 | 0.000432 | 0.0 |
| `knowledge_gate` | 50 | 100.00% | 0 | 0 | 0.077252 | 12.0 |
| `memory_retention` | 50 | 100.00% | 0 | 0 | 0.006254 | 0.0 |
| `presence` | 50 | 92.00% | 4 | 4 | 0.001600 | 0.0 |
| `relationship_fallback` | 50 | 20.00% | 40 | 40 | 0.000522 | 0.0 |

## 结论

- 本报告冻结的是现有确定性/保守回退行为，不把它包装成 CDS 新算法。
- Presence、Knowledge、History、CTX、Relationship fallback 与 Archivist 均直接调用当前生产纯函数或门控函数。
- Relationship 无可用模型时必然回退 `ordinary_exchange`；语义事件的漏选是已知基线，不在 CDS.0 修复。
- CTX v1 固定比例可能在受限预算下保留语义无关组件；CDS.7 只可先做 Shadow proposal，不能直接改冻结装配器。
- 所有误选/漏选留给后续 CDS 阶段配对比较；本阶段不改变聊天、数据库或任何冻结协议。

逐样本结果、版本和无正文诊断见同名 JSON。

# 知识自然召回固定集评测报告

- 协议：`knowledge-recall-eval-v3` / `knowledge-recall-decision-v1`
- 合成样本：52 条（不含用户真实对话或知识正文）
- Action accuracy：98.08%
- Reason accuracy：98.08%
- Trigger precision / recall / F1：96.77% / 100.00% / 98.36%
- 检索命中率：100.00%

## 性能（毫秒）

| 路径 | avg | P50 | P90 | P99 |
|---|---:|---:|---:|---:|
| 总耗时 | 23.961538 | 27.0 | 31.9 | 34.49 |
| 确定性跳过 | 0.0 | 0.0 | 0.0 | 0.0 |
| FTS/dense 检索 | 22.039115 | 25.0055 | 27.761 | 30.9385 |
| 策略查询 | 1.71125 | 2.2415 | 3.4505 | 3.75376 |

## 阈值结论

- exact term 高置信最小长度：3。
- entity 中置信最小长度：2。
- dense 自动升为 high：不允许。
- dense 建议阈值：None。
- 确定性 high 自动注入：允许。
- 纯语义自动升档：关闭。

## 未通过样本

| case | 期望 | 实际 | 检索命中 |
|---|---|---|---|
| negative_programming | skip/no_candidates | retrieve/semantic_candidate | 否 |

## 可重复性

fixture SHA-256：`1b9beb3fc3ff947243f3e14bb8f19778f9d68df4358b5fa23989586d51415a3d`。JSON 报告保留逐样本数值和环境版本，不保留真实查询、用户数据或知识库正文。

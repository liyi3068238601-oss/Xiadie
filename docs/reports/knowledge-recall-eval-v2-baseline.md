# 知识自然召回 K.3 固定集评测报告

- 协议：`knowledge-recall-eval-v2` / `knowledge-recall-decision-v1`
- 合成样本：23 条（不含用户真实对话或知识正文）
- Action accuracy：82.61%
- Reason accuracy：82.61%
- Trigger precision / recall / F1：81.25% / 100.00% / 89.66%
- 检索命中率：100.00%

## 性能（毫秒）

| 路径 | avg | P50 | P90 | P99 |
|---|---:|---:|---:|---:|
| 总耗时 | 18.565217 | 25.0 | 28.8 | 30.78 |
| 确定性跳过 | 0.0 | 0.0 | 0.0 | 0.0 |
| FTS/dense 检索 | 16.604609 | 22.47 | 26.0566 | 27.80718 |
| 策略查询 | 1.761609 | 2.267 | 2.8752 | 3.305 |

## 阈值结论

- exact term 高置信最小长度：3。
- entity 中置信最小长度：2。
- dense 自动升为 high：不允许。
- dense 建议阈值：0.472169。
- 自动注入：关闭；K.3 仍保持 shadow。

## 未通过样本

| case | 期望 | 实际 | 检索命中 |
|---|---|---|---|
| private_remote | ask/local_only_remote_provider | retrieve/semantic_candidate | 是 |
| unrelated_weather | skip/no_candidates | retrieve/semantic_candidate | 是 |
| unrelated_companion | skip/no_candidates | retrieve/semantic_candidate | 是 |
| emoji_only | skip/no_candidates | retrieve/semantic_candidate | 是 |

## 可重复性

fixture SHA-256：`73339358569577793f6ec574d023753811019a1e0ef16e364851f2061f62cbb2`。JSON 报告保留逐样本数值和环境版本，不保留真实查询、用户数据或知识库正文。

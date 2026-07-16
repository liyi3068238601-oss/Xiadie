# 知识自然召回 v2 校准前后对比

评测固定为 `knowledge-recall-eval-v2`，fixture SHA-256 为
`73339358569577793f6ec574d023753811019a1e0ef16e364851f2061f62cbb2`，共 23 条合成样本。两次运行使用相同 FTS、BGE-M3、文档和预期结果；区别仅为是否启用 K.3 的纯 dense 候选下限。

| 指标 | 阈值前 | K.3 校准后 | 变化 |
|---|---:|---:|---:|
| Action accuracy | 82.61% | 100.00% | +17.39pp |
| Reason accuracy | 82.61% | 100.00% | +17.39pp |
| Trigger precision | 81.25% | 100.00% | +18.75pp |
| Trigger recall | 100.00% | 100.00% | 0 |
| Trigger F1 | 89.66% | 100.00% | +10.34pp |
| 相关文档命中率 | 100.00% | 100.00% | 0 |

证据分布：13 个相关样本的 top dense 最低值为 `0.477699`，3 个无关 dense 样本的最高值为 `0.466639`，两者中点为 `0.472169`。因此该中点只用于影子阶段过滤纯 dense 弱候选；有 FTS 命中的候选不受影响，显式检索链路不使用这个下限。

尽管本固定集校准后全部通过，正例 13 条、负例 3 条仍不足以允许 semantic 自动提升为 high confidence。`semantic_auto_high_enabled` 和 `automatic_injection_enabled` 均保持关闭，K.5 前必须继续扩大样本并复核真实 P95/误触发率。

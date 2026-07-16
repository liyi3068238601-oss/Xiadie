# 知识自然召回评测报告（模板）

## 版本与范围

- 评测协议：
- 决策协议：
- fixture SHA-256：
- 合成样本数：
- FTS 版本：
- embedding 版本及是否可用：
- 与上一个报告比较：

## 核心指标

| 指标 | 当前 | 上一版 | 变化 | 门槛 | 结论 |
|---|---:|---:|---:|---:|---|
| Action accuracy | | | | | |
| Reason accuracy | | | | | |
| Trigger precision | | | | | |
| Trigger recall | | | | | |
| Trigger F1 | | | | | |
| 相关文档命中率 | | | | | |

## 分路径性能

分别填写确定性规则、FTS、dense、融合、策略查询和总耗时的 avg/P50/P90/P99。冷启动与热启动必须分开，不能只报告平均值。

## 分数与阈值证据

列出正例/负例的 top dense、top fusion、score gap、term strength 分布。只有样本量、精度与召回均达到门槛，才能把某一特征提升为 high confidence；否则保持 medium/low 与 shadow。

## 失败样本

逐条记录合成 case ID、期望 action/reason、实际 action/reason、是否命中相关文档以及失败类别。不得复制用户真实查询、知识正文或绝对文件路径。

## 阈值结论

- 采用：
- 拒绝：
- 继续观察：
- 是否允许自动注入：否（除非 K.5 的独立门槛全部通过）。

## 回归与审查

- 旧 explicit 检索测试：
- 空库/模型缺失/向量失败：
- 重复内容/相邻切片：
- 自然预算：
- review 采纳、调整、拒绝项：

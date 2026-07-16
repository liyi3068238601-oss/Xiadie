# ADR-0039：数据校准的影子召回选择

- 状态：已接受
- 日期：2026-07-17
- 阶段：K.3

## 背景

K.2 只要 dense 返回候选就会建议召回，而向量检索即使面对无关问题也会返回排序结果。没有固定集、分数证据和去重规则时，不能安全地把自然召回接入真实模型上下文。

## 决策

1. 固定 `knowledge-recall-eval-v2`，当前包含 23 条合成样本，覆盖应召回、不应召回、显式禁止、情绪陪伴、实体边界、语义改写、重复来源、远传策略、提示注入、英文术语、数字时间、双重否定和 FTS 无词项。评测不读取用户对话或开发知识库。
2. 报告同时保存 FTS/dense 数量、RRF fusion、top score、score gap、term strength、候选准入数、检索/策略/总耗时以及 action/reason/命中指标。报告协议为 `knowledge-recall-report-v1`。
3. 固定集显示 13 个相关样本 top dense 最低为 0.477699，3 个无关样本最高为 0.466639。中点 0.472169 作为 `knowledge-recall-thresholds-v1` 的纯 dense 影子候选下限；有 FTS 命中的候选和 explicit 检索不使用该下限。
4. exact term 长度至少 3 才为 high；长度至少 2 为 medium entity。标题实体只检查融合排序前两个已准入候选，避免低位无关标题制造命中。
5. exact content hash 跨来源聚类，相邻切片仅在字符 3-gram Jaccard 至少 0.65 时去重。聚类保留全部来源文档 ID 于瞬时检索结果，以便策略判断选择可发送来源；RecallDecision 仍不持久化这些 ID。
6. 自然召回使用独立 700 token、最多 4 条的选择预算。K.3 只统计选择结果，`injected_count` 仍为 0。
7. 23 条样本虽在校准后全部通过，但只有 13 个 dense 正例和 3 个 dense 负例，不足以将 semantic 提升为 high。至少需要 30 个正例和 15 个负例且 precision/recall 均不低于 0.90；当前 `semantic_auto_high_enabled=false`、`automatic_injection_enabled=false`。
8. 新增全局/会话共用的无正文聚合统计，提供 action 比例、reason 计数、向量可用率、超时率和 P50/P90/P99，不保存或返回查询正文。

## 后果

- 固定集从阈值前 action accuracy 82.61%、trigger F1 89.66% 提升到校准后 100%，相关文档命中率保持 100%。这些数字只代表合成固定集，不能外推为真实用户准确率。
- 自然召回继续处于 shadow；K.4 只建设授权安全边界，K.5 仍需扩大样本并重新审查是否允许真实注入。
- dense 下限与模型/embedding 版本绑定；模型、pooling、量化或固定集变化后必须生成新阈值版本，不能沿用本数值。

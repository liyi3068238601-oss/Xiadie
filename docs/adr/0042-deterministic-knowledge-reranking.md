# ADR-0042：知识检索采用确定性规则重排与轻量多样性选择

- 状态：接受
- 日期：2026-07-19
- 适用协议：`knowledge-search-v2`

## 决策

当前不引入独立神经 reranker。RRF 合并后的完整候选池使用有上限的内容覆盖、标题匹配和定位完整度加分，再执行内容哈希去重、相邻切片聚类与轻量 MMR 选择。存在多个 collection 时，自然召回每个 collection 最多两条，显式检索最多三条；单一 collection 不受此多来源上限误伤。

## 原因

项目已经随包提供 BGE-M3 embedding。再加入 cross-encoder reranker 会增加模型体积、冷启动、内存和冻结后端兼容风险；现有 52 条固定集尚不能证明这些代价能带来额外收益。`knowledge-search-v2` 在不增加安装资源的情况下，将首个相关来源 MRR 从约 0.9778 提升到 1.0000，检索命中率保持 100%，本机平均检索耗时未增加。

规则加分被限制在小范围内，不能完全覆盖 RRF 基础相关性。多样性选择从完整候选池执行，严格遵守来源上限和字符预算；旧实现先截断后选择，实际上无法稳定产生多样性收益。

## 重新评估条件

只有同时满足以下条件才重新评估本地神经 reranker：

1. 匿名或合成困难集扩大后，确定性方案的 MRR、nDCG 或人工相关性明显不足；
2. 候选规模足以让二阶段模型产生稳定收益；
3. 提供冻结后端、安装体积、冷启动、峰值内存和 P90 延迟数据；
4. 本地运行、正文不外传、向量失败可降级等边界保持不变。

## 证据

- `docs/reports/knowledge-recall-eval-v3-calibrated.json`：`knowledge-search-v1` 对照。
- `docs/reports/knowledge-recall-eval-v3-search-v2.json`：`knowledge-search-v2` 结果。
- `backend/tests/test_knowledge_search.py`：完整候选池重排、严格来源上限和字符预算测试。
- `backend/tests/test_knowledge_recall.py`：查询清理与受限实体延续测试。

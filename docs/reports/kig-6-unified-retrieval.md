# KIG.6 混合召回与统一候选施工报告

- 日期：2026-07-27
- Schema：74（本阶段无迁移）
- 核心契约：`RetrievalRequest` / `RetrievalFilters` / `RetrievalCandidate` / `RetrievalBatch`

## 已交付

1. 六源统一候选：Knowledge、Memory、History、Life、Task/ToolRun、Lore 均输出来源 revision/hash/status/privacy/locator 与短 excerpt，正文只存在于本次内存批次。
2. 复用现有能力：Knowledge 继续使用 FTS+Dense+RRF、邻居扩展与既有去重；其他来源使用其权威库现有只读索引或投影。
3. metadata hard filter 支持 source ID、document ID、tag、revision、status 与日期范围，并在接纳前实时复核 KIG.1 SourceRef。
4. 每源默认上限 6、硬上限 20，总上限 60；各源独立执行和失败，诊断不包含查询、excerpt 或异常正文。
5. 源内规范化 exact 去重后按来源轮询，防止单一来源占满批次；不同来源的相同证据保留，供后续 Evidence/冲突判断。
6. Knowledge Dense 不可用或失败时标记 lexical fallback，FTS 仍返回候选；单源故障不阻塞其他源。

## 验收证据

- `backend/tests/test_kig6_unified_retrieval.py`：12 项，覆盖候选契约、来源伪装拒绝、故障隔离、独立上限、metadata/date/version/status gate、去重、多样性、邻居、Dense fallback 及六类真实 adapter。
- KIG.0～KIG.6、Knowledge、Memory、History 来源域联合回归：`137 passed, 1 warning`。
- 扩大到 CDS、CTX、Knowledge Recall/Search 与 KIG.0～KIG.6 的核心回归：`893 passed, 1 warning`。
- 每个进入批次的候选均通过当前 SourceRef 逐字段校验；伪造 source、revision/hash、status 或 locator 无法进入批次。

## 边界与回滚

- 本阶段只构建候选，不写 RetrievalBundle、不注入 Context、不执行 LLM rerank；这些分别由 KIG.7～KIG-R 后续阶段接线。
- LIFE 只读取 SelfTimeline 中可追溯到 LifeEvent 的投影；Diary/Goal 等扩展接线留在 KIG.12，不绕过其所有权和隐私语义。
- Task v1 只把正式 ToolRun 作为权威执行来源；普通任务状态的完整治理仍留在 KIG.12。
- 回滚移除 `kig_retrieval.py` 及 Knowledge 结果新增的 `created_at` 元数据即可；无迁移、无来源数据变化。

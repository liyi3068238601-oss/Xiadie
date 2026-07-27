# KIG.0 ConstructionBaseline 与能力审计

- predecessor：LIFE PR #3 merge `f16d80ab0d2457065dc65d7d284d3cbf3584f5ee`
- Schema：71；KIG 首个可用迁移：72
- 冻结测试基线：后端 `2428 passed, 1 warning`；前端 `50 passed`；Vite 190 modules；Electron contract 3 项
- 合成固定集：60 条；fixture SHA-256 `63f99d122ad55185296c44576424e908356b1db46b61c6cd8f6d7bea39080bb7`；真实 Provider 调用 0

## 60 场景当前基线

| 场景 | 数量 | Knowledge 召回率 | Memory 召回率 |
|---|---:|---:|---:|
| `knowledge_memory` | 20 | 100.00% | 100.00% |
| `multi_document` | 20 | 100.00% | — |
| `single_document` | 20 | 100.00% | — |

## 指标

- Knowledge 召回率：100.00%。
- Knowledge+Memory 双源分别召回率：100.00%。
- 现有 Knowledge citation allowlist 准确率：100.00%。
- Knowledge 延迟 P50/P90：8.940/9.594 ms。
- Memory 延迟 P50/P90：8.027/8.449 ms。
- Knowledge 注入 token 平均/P90：245.3/290.0。
- 跨源统一 Evidence 支持率：0%；当前只能由 CTX 并列装配，不能冒充 KIG 已实现。

## 能力矩阵

| 能力 | 状态 | 唯一所有者 | 代码证据 |
|---|---|---|---|
| `knowledge_import_parse_chunk` | [x] | Knowledge | `knowledge.py/knowledge_parser.py/knowledge_chunker.py/knowledge_worker.py` |
| `knowledge_fts_dense_search_v2` | [x] | Knowledge | `knowledge_search.py/knowledge_embeddings.py` |
| `knowledge_citation_locator_delete_grants` | [x] | Knowledge | `knowledge_context.py/knowledge_cleanup.py/knowledge_grants.py` |
| `context_hard_budget` | [→] | CTX | `context_assembler.py/context_budget.py` |
| `fragment_episode_saga` | [→] | MEM | `memory.py/episodes.py/sagas.py` |
| `life_authoritative_ledger` | [→] | LIFE | `life_events.py/self_timeline.py` |
| `task_toolrun_sources` | [~] | Task/ToolRegistry | `tasks and tool_logs exist; ToolRegistry not implemented` |
| `lore_readonly_sections` | [→] | Lore | `lore.py` |
| `unified_source_ref_registry` | [ ] | KIG | `no KIG tables at Schema 71` |
| `cross_source_query_plan_evidence` | [~] | KIG | `parallel CTX blocks exist; no unified candidate/evidence contract` |
| `pwm_projection` | [ ] | KIG | `no pwm_ tables at Schema 71` |
| `web_result_live_adapter` | [-] | Future ToolRegistry | `compatibility slot only` |

## `[~]` 最小补差与回滚

### `task_toolrun_sources`

- 已有：Task CRUD 与已完成 tool_logs 可查询。
- 缺失：尚无正式 ToolRegistry 或来源 adapter。
- 最小补差：KIG 只读并验证既有行，未来 ToolRegistry 继续拥有写入权。
- 回滚：移除 KIG adapter，不修改 Task/ToolRun 权威行。

### `cross_source_query_plan_evidence`

- 已有：CTX 可为 Knowledge、Memory、History、Life 与 Lore 并列分配预算。
- 缺失：尚无统一 candidate、QueryPlan、EvidenceLink 或支持度校验。
- 最小补差：新增 KIG 信封和 adapter，同时保留 CTX 最终预算所有权。
- 回滚：关闭 KIG bundle，继续既有 CTX 并列装配。


## 审计结论

- KnowledgeDocument、Chunk、导入、解析、删除、引用和 search v2 已完整存在，KIG 必须复用，禁止重建第二主链。
- CTX、MEM、LIFE 与 Lore 保持单一写入者；KIG 只读来源并持久化最小派生依赖。
- Task 与 tool_logs 可作为来源，但正式 ToolRegistry 尚未施工，KIG v1 只能验证已有 ToolRun 行。
- 缺口集中在统一 SourceRef、跨源候选/证据、QueryPlan、版本/新鲜度与 PWM 派生投影。
- `web_result` 只保留兼容位；KIG v1 不联网搜索、不抓取网页、不注册研究执行器。

## 回滚

KIG.0 仅新增合成 fixture、审计脚本、测试、报告和 ADR，不创建 Schema 72 或生产写路径；可整提交回滚且不影响用户数据。

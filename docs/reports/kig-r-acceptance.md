# KIG-R 冻结验收报告

- 协议：`kig-retrieval-governance-v1`
- Schema：76
- 合成场景：10 组（不含用户数据）
- 安全门：pass
- 模型质量门：external_kig7_certification_required
- 发布门：pending_model_quality

| 零容忍指标 | 分母 | 违规数 |
|---|---:|---:|
| forged_source_ref_accepted | 10 | 0 |
| invented_citation_clickable | 10 | 0 |
| stale_citation_clickable | 10 | 0 |
| unsupported_citation_clickable | 10 | 0 |
| unconfirmed_high_impact_relation_accepted | 10 | 0 |
| unauthorized_remote_tool_excerpt | 10 | 0 |
| unauthorized_knowledge_excerpt | 10 | 0 |
| conditional_false_conflict | 10 | 0 |
| recency_only_supersession | 10 | 0 |
| shadow_proposal_active | 10 | 0 |
| deterministic_fallback_empty | 10 | 0 |

安全门与模型质量门彼此独立。安全门通过不能替代 KIG.7 实配模型盲评；在后者有有效覆盖率与人工相关性提升证据前不得把 `retrieval-rerank-v1` 晋级或声称 KIG-R 已冻结。

# KIG-R 冻结验收报告

- 协议：`kig-retrieval-governance-v1`
- Schema：76
- 合成场景：10 组（不含用户数据）
- 安全门：pass
- 模型质量门：pass
- 发布门：pass

| 零容忍指标 | 分母 | 违规数 |
|---|---:|---:|
| forged_source_ref_accepted | 10 | 0 |
| invented_citation_clickable | 10 | 0 |
| stale_citation_clickable | 10 | 0 |
| unsupported_citation_clickable | 10 | 0 |
| unconfirmed_high_impact_relation_accepted | 10 | 0 |
| unauthorized_remote_tool_excerpt | 10 | 0 |
| unauthorized_knowledge_excerpt | 10 | 0 |
| unknown_privacy_scope_excerpt | 10 | 0 |
| conflicting_confirmed_pair_applied | 10 | 0 |
| conditional_false_conflict | 10 | 0 |
| recency_only_supersession | 10 | 0 |
| shadow_proposal_active | 10 | 0 |
| deterministic_fallback_empty | 10 | 0 |

安全门与模型质量门彼此独立。模型指纹认证已通过：`deepseek` / `deepseek-v4-pro` / `b445dd9e271d6ade6eb4be3577b11ef57a5280f7c6ba2ca7a266f3527aa5bd03`。 未匹配该 Provider、模型、协议、Prompt 或固定集指纹的其他模型仍必须保持未认证 Shadow/确定性回退。

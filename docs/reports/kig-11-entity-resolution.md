# KIG.11 可逆实体解析

- Schema 78 保存 proposal 与 operation journal；merge 会迁移 entity/alias/claim/relation/source-link/event/state-assertion，journal 只记录 body-free 恢复元数据。
- reality/lore 与 entity type 必须一致；高影响对象及所有 LLM 提议均要求用户确认。
- 仅 exact canonical/alias、同 scope/type、非高影响、置信 1.0 的 deterministic proposal 可由系统应用。
- split/rollback 使用原操作快照恢复关系迁移；100 个合成 exact merge 精确率与回滚恢复率均为 100%。
- `memory_entities` 只通过 `memory_alias_sync` proposal 接线，无自动合并或双向覆盖。

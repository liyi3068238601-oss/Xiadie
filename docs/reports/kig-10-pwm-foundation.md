# KIG.10 PWM 来源化底座

- Schema 77；七张 `pwm_` 业务投影表与预算计数表。
- 所有实体、alias、Claim、Relation、WorldEvent、StateAssertion 和 source link 均绑定实时 `SourceRef`/`derived_dependencies`；不复制 owner 正文。
- 所有写入固定 `shadow`；模型 Claim 固定 `model_inferred/candidate`，聊天事实支持链不读取 PWM。
- 实体、Predicate、event layer 与执行状态全部白名单；敏感画像自动抽取 fail-closed。
- 默认硬预算：64 Claim/source、128 entity/day、30d candidate TTL、16 alias/entity、8 disambiguation、100 maintenance、90d orphan archive。

回滚 Schema 77～80 或关闭 `pwm_enabled` 只移除派生层，不删除 Knowledge/MEM/LIFE/EAP/Tool 权威数据。

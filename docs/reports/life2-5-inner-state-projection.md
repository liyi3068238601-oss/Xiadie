# LIFE2.5 InnerStateProjection 施工与验证报告

日期：2026-07-30  
协议：`inner-state-projection-v1`  
Schema：保持 82，无新增迁移  
发布状态：`shadow`

## 实际实现

- 每次聊天请求从当轮 Affect/Relationship 预览、最多 3 个开放 Goal、2 个开放 Saga、3 个最近 LIFE Event 与 3 个相关 ShortMemo 生成不可变值对象。
- 输出只允许 `affect_band`、`relationship_boundary`、来源对象 ID 和 `calm/warm/concise/gently_curious/offer_help` 五项表达旗标；没有自然语言心声、用户正文、ShortMemo 正文或 chain-of-thought。
- `gently_curious` 与 `offer_help` 由关系边界和当前意图共同确定；`defensive/highly_guarded` 不生成这两项。Focused Work 固定生成 `concise`。
- `source_snapshot_hash` 由有界状态字段和选中来源 ID/revision 确定性生成，仅存在于请求内；不进入 Persona 公共元数据、数据库、缓存或日志。
- Persona 编译器执行第二层协议校验，并将 Projection 独立发布门与 Persona 静态模型证书分离。Shadow 候选可用于比较，但不会改变已认证的生产 prompt。

## 验证结果

- Projection、Persona、CTX 专项：22 passed。
- 公共聊天 + ShortMemo + Projection 联合回归：56 passed。
- 相同来源快照输出一致；来源集合改变时 hash 改变且对应 ID 消失。
- 空来源不生成；数组上限、枚举、ID 格式、未知字段和隐藏正文均 fail closed。
- 构建前后 Schema 均为 82，SQLite 表集合完全一致；没有持久化 `StructuredInnerState`。

## 保守结论

现有权威对象足以提供首版请求内表达投影，没有证据支持新增持久化心理状态表。Projection 保持 Shadow，待 LIFE2.6 组合验收及整体 Review 后再决定是否启用；若不启用，删除请求接线即可完整回退，不需要数据库降级。

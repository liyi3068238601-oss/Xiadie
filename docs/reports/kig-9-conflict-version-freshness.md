# KIG.9 冲突、版本与新鲜度施工报告

- 日期：2026-07-28
- Schema：76
- 协议：`version-relation-input-v1` / `version-relation-result-v1` / `freshness-state-v1`
- 阶段结论：完成，进入 KIG-R 冻结门

## 已交付

1. `kig_source_governance` 保存来源 hash/revision、authority、适用范围、版本标签、有效期和用户确认 revision；`kig_version_relations` 保存两端 SourceRef、关系、范围、置信度、决策来源、影响级别和确认状态。两表均不保存正文或 excerpt。
2. VersionRelation 两端写入 `derived_dependencies`；owner 来源变化、撤销或删除可使派生关系失效，KIG 不复制权威事实。
3. 确定性顺序固定为 exact hash/owner revision、已证明同对象后的 semver、条件/时间范围、用户 authority 与日期。仅有“版本更大”或“时间更新”不会推断替代。
4. FreshnessState 支持 current、possibly_stale、deprecated、superseded、expired、unknown；exact duplicate、完整/部分替代和有效期均有独立处理。
5. 用户纠正 > 用户确认 authoritative > ToolRun > 官方来源 > 导入资料 > 模型提案。用户 authority API 要求显式 `user_confirmed=true`，来源 revision/hash 变化后旧标记自动退出 active 读取。
6. 语义关系运行在 CDS Shadow，候选必须精确为两个 SourceRef，模型只可生成 proposal；高影响 contradict/divergent 缺少确认位会被 Validator 拒绝。确认 API 使用 optimistic revision，未确认 proposal 不影响聊天排序。

## 聊天接线

- `kig-retrieval-governance-v1` 串联程序化 Query Plan、统一多源 RetrievalCandidate、确定性融合、Version/Freshness 治理、Evidence bundle 与 ContextAssembler。
- KIG 不应用 KIG.7 模型重排结果；真实聊天只使用独立确定性融合和已确认关系。普通、关闭和歧义查询保持旧聊天路径。
- 远端 provider 默认排除 ToolRun 证据；sensitive 来源在 transfer boundary 丢弃；Knowledge 仍由原有逐文档 grant 控制。
- 未决语义冲突写入 RetrievalBundle，生成后 Validator 把无保留的确定句降为冲突表述。Knowledge K1 和跨源 E1 均执行实时来源与句子支持度校验。

## 自动验收

- KIG.9 专项：11 项；KIG-R chat pipeline：8 项；覆盖 body-free schema、hash/semver、非同对象版本、条件兼容、recency 反例、用户纠正、Shadow、确认、optimistic revision、有效期、依赖、远端权限和真实 SSE 聊天持久化。
- KIG/Knowledge/CTX/API/CDS.9/CDS.10 扩大回归：`687 passed, 1 warning`。
- CDS.9/CDS.10 动态报告已在 Schema 76 重新生成并与测试内 build_report 完全一致。
- 零容忍观测：未确认高影响关系应用数 0；LLM relation Active 数 0；不同条件误冲突数 0；仅按新日期自动替代数 0；未授权远端 ToolRun excerpt 数 0。

## 回滚

移除 `kig_pipeline` 聊天调用即可恢复原 Knowledge/Memory/CTX/LIFE 行为；Schema 76 两表仅含可重建治理元数据。回滚不删除 owner 数据，也不要求回滚 Schema 72～75。

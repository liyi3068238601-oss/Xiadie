# CDS.0～CDS.10 review 后代码审计

> 日期：2026-07-26  
> 审计起点：`43a9cc1`  
> 结论：CDS.4～9 不需推倒重做；CDS.10 动作矩阵与 CDS.6 SSE 最终事件需要定点返工。CDS.11 尚未开工。

## 1. 已核实的阶段边界

- CDS.4 RecallPlanner、CDS.5 CandidateReranker、CDS.7 ContextPlanner、CDS.9 Memory proposal 与 CDS.10 Narrative proposal 的 DecisionKind 均保持最高 Shadow。
- CDS.6 只在现有 KnowledgeResult/CTX 路径实现原子 EvidenceWindow；CDS.8 复核冻结 RelationshipMeaning/EAP 写入器，没有建立第二写者。
- Schema 保持 62；CDS.4～10 没有新增领域状态表或把 application owner 转移给 CDS。
- CDS.9 初版 review 的 5 BLOCK/3 WARN 已在 `17fba82` 前完成实质返工，生产预筛/生命周期与 fallback 共用纯投影，来源变化 fail closed。
- 当前 Python 3.12.13 自带 SQLite 3.50.4，外部总体 review 所述 SQLite 3.40.1/contentless-delete 测试阻断在本机已不成立。

## 2. 新发现并返工的问题

### CDS.10 Episode/Saga 语义动作矩阵

初版 validator 能阻止非候选成员、不连续 Episode、低置信度选中、不安全 revive 和 merge 执行，但仍可接受以下内部矛盾结果：

- `same_goal=false` 或 `causal_chain=false`，同时 `form_episode`；
- form/skip 与 `goal_mismatch`、`causal_chain_missing`、`bounded_narrative` 原因不一致；
- Saga 普通转移与 `merge_requires_review` 等原因不一致；
- 重复 turning-point ID。

已先加入失败测试，再收紧 validator 和不读取 fixture expected 的独立 oracle。oracle 版本升为 `cds10-narrative-safety-oracle-v3`；重新生成报告后 240/240 精确匹配、安全违规 0、MEM 领域写入 0。

### CDS.6 SSE 最终正文重复提交

当前服务端依次发送 `final` 与含完整正文的 `done`，旧前端兼容逻辑会调用两次 `onFinal`。现有 ChatView 只会重复赋同一文本，但未来副作用型消费者可能重复执行。已增加流级 `finalSeen` 状态：新协议只调用一次，旧服务端只有 done 时仍能回填最终正文。

## 3. 采纳的非阻塞 review 建议

- ADR-0056 更新为独立 review 通过，并明确 8 条人工合成标签只用于观察，不作为晋级证据。
- `_source_hash` 注明排除 `last_lifecycle_evaluated_at` 的原因：异步评估时间不改变叙事语义或来源内容。
- CDS.6 动态预算文档示例改为与 `int(context_window * 0.3)` 一致的实际数值。

## 4. 保留但不阻断的事项

- CDS.5 没有独立大规模 fixture/report，只有专属测试和 strict review；这是证据完整性 P2，不影响当前 Shadow 安全边界。若未来讨论 Advisory，必须先补独立配对语料和报告。
- CDS.5～8 没有各自 ADR，但施工记录、冻结计划和评测报告可追溯；无需为文档数量返工代码。涉及协议升级或所有权变化时必须另立 ADR。
- CDS.10 原始叙事小样本 accuracy 仅 50%。这不阻断“安全 Shadow 协议已完成”，但明确阻断该 decision kind 的 Advisory/Active 晋级和任何真实质量声明。

## 5. 验证结果

- CDS.4～10 联合专项：`1317 passed`。
- 后端全量：`2284 passed, 1 warning`；唯一警告为既有 Starlette `httpx2` 迁移提示。
- 前端：`45 passed`；TypeScript 与 Vite production build 189 modules。
- CDS.10 重生成报告：240/240、oracle v3、安全违规 0、MEM 领域写入 0，质量样本 `promotion_evidence_eligible=false`。

## 6. Post-fix review

外部窄范围复审以 0 P0、0 P1、1 P2 通过，51/51 独立检查成功，允许进入 CDS.11。P2 要求的 oracle 三项直接负向覆盖已补齐；SQLite 版本观察以当前实际运行的 Python 3.12.13 / SQLite 3.50.4 和本轮 2284 项全量结果为准。

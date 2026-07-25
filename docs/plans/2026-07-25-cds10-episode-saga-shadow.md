# CDS.10 Episode/Saga Shadow 实施计划

## 目标

在 Schema 62 和现有 MEM 写路径不变的前提下，增加 EpisodeBoundaryProposal 与 SagaTransitionProposal 两个纯只读 Shadow 决策协议。

## TDD 步骤

1. 先固定双 DecisionKind 注册、候选白名单、严格动作矩阵、低置信度跳过、恢复来源与 merge 禁止执行测试。
2. 实现纯投影 fallback 与严格 validator，并复用 Episode/Saga 既有确定性评分。
3. 增加单事务只读 adapter，只接受真实 pending/qualified 候选并复核当前资格，绑定候选、Fragment、Episode 与目标 Saga 的完整 revision/hash 链。
4. 固定 240 个规则安全场景与带人工标签的原始叙事回归语料；oracle 独立检查 provenance、绑定和成员结构，质量语料经真实候选路径计算诚实指标且不宣称 holdout。
5. 通过共享 DecisionRun 执行 Shadow，核对共享账本预期写入与 MEM 领域零写入。
6. 生成 JSON/Markdown 报告，更新 ADR、专项总计划与注册导入。
7. 运行 CDS.10 专项测试、相邻 CDS 回归、编译检查和 diff 检查。

## 回滚

删除 CDS.10 新增模块、测试、生成器、fixture、报告和 ADR，并撤销注册导入及总计划勾选。无需迁移或数据恢复。

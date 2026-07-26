# CDS.4 RecallPlanner Shadow 施工与兼容审计

> 日期：2026-07-22  
> 基线：`6db29ed`（CDS.3）  
> 状态：CDS.4 施工完成，等待独立 review；未进入 CDS.5。

## 1. CDS.3 strict review 处置

review 结论为 0 P0、0 P1、2 P2，允许进入 CDS.4。两项 P2 均采纳：强 presence 信号优先于已有 open thread；固定集补齐 meal/shower return。扩展后的 CDS.3 固定集为 15 组、900 轮，Shadow 精确匹配 100%，完成门保持 0%/100%/0%。

## 2. 冻结路径与所有权审计

- 当前聊天仍分别调用现有 memory digest、显式 history recall、knowledge retrieval 与 Lore retrieval；Episode/Saga 没有新的聊天触发器。
- `recall_planner` 只在 CDS 注册表登记，`main.py` 仅导入完成注册，不调用 `execute_registered_decision`。
- CTX 继续拥有 ContextPackage 与最终预算；Knowledge/MEM/Lore/Episode-Saga 继续拥有权限、候选、正文与生命周期。
- 本阶段未新增 Schema，未改检索、引用、装配、记忆或 Saga 写入路径。

## 3. 有界协议

输出只包含十类任务、五类来源需求、query intent、最多 8 个查询词和硬拒绝标志。validator 逐项检查候选快照、有效 source message、来源枚举、需求等级、查询长度、禁止检索和只扩候选边界。

明确禁止检索会返回空来源、空查询与 `skip`；Shadow 结果不能应用。Active header 在创建 run 前被统一 CDS 门禁拒绝。

## 4. 600 轮合成对照

固定集由 12 组 × 10 个语义表达 × 5 个表面变体组成，覆盖 ordinary、emotional、current task、past decision、exact quote、document fact/analysis/comparison、relationship、Lore 与两类禁止检索。数据为纯合成，不含用户正文，不调用真实 Provider。

| 指标 | 结果 |
|---|---:|
| Shadow 任务与来源需求精确匹配 | 100% |
| 必需来源召回率 | 100% |
| 明确禁止后的来源违规率 | 0% |
| 查询建议有界率 | 100% |
| source message 绑定率 | 100% |
| 冻结旧触发器来源精确匹配 | 8.33% |

旧路径对所有非空聊天轮次尝试 memory，且不能统一表达 Episode/Saga 需求；该差异只证明对照协议能描述现有缺口，不证明启用 Planner 必然改善真实回答。

## 5. 停线门

当前只完成离线参考策略和安全协议校准。实际模型尚未参与 600 轮评测，主聊天没有行为变化。CDS.4 必须经独立 review 确认 0 个未解决 P0/P1，方可进入 CDS.5；不得据此直接升 Advisory/Active 或让 Planner 注入上下文。

施工自验：后端全量 `1591 passed, 1 warning`；前端 `41 passed`，TypeScript 与 Vite production build 185 modules；Python 编译、Electron 语法与 `git diff --check` 通过。当前环境未安装 Ruff，因此未把 Ruff 声称为本轮通过项。

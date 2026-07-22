# CDS.0 ConstructionBaseline 与边界冻结

> 记录日期：2026-07-22
>
> 状态：CDS.0 施工完成，等待独立 review；未进入 CDS.1。

## 1. ConstructionBaseline

| 字段 | 冻结值 |
|---|---|
| Repository | `liyi3068238601-oss/Xiadie` |
| Predecessor PR | GitHub PR #1，已合并 |
| Base branch | `main` |
| Base commit SHA | `6b8aa47134f8a9a55131c73bb1148e6912421c4f` |
| Schema | 60 |
| 测试基线 | 后端 `937 passed, 1 warning`；前端 `41 passed` |
| 计划版本 | CDS v0.3 |
| 下一可用 Schema | 61，仅在后续阶段确认存在真实字段缺口时使用 |

本阶段没有数据库迁移、聊天链路修改、Provider 请求或真实用户数据处理。上述 SHA 是施工前置合并提交，基线报告生成器显式固定该值，不随施工分支的后续提交漂移。

## 2. 当前算法与协议版本

| 评测轨道 | 当前生产函数/门控 | 冻结版本 |
|---|---|---|
| Presence | `proactive.presence.detect_presence_signals` | `conversation-presence-v2` |
| Relationship fallback | `proactive.cognition.unknown_fallback` | `companion-cognition-v1` |
| Knowledge gate | `knowledge_recall.evaluate` | `knowledge-recall-decision-v1` / `knowledge-recall-thresholds-v2` |
| History intent | `_EXPLICIT_RECALL` 确定性匹配 | `conversation-history-index-v1` / `conversation-history-score-v1-shadow` |
| Context fixed budget | `context_assembler._bounded_components` | `context-package-v1` / `context-budget-v1` / `xiadie-conservative-v1` |
| Memory retention | `archivist.protection_reasons`、`retention_score` | `fragment-retention-v1` |

这份冻结只用于后续配对比较，不把旧算法重新命名为 CDS 新能力，也不解除任何现有 Shadow 或安全边界。

## 3. 离线评测集

- 协议：`cognitive-decision-eval-v1`。
- 总量：300 个纯合成场景，六条轨道各 50 个。
- 标注：每个场景同时给出 `must_select`、`may_select`、`forbidden_select`，三者互斥且候选 ID 受白名单约束。
- 固件 SHA-256：`1ecad02a68c1cce99948c0e9842bf8462b9e747c8ddced8e3f9bb8e122c7d02c`。
- 隐私：固件不含用户数据；报告只保存场景 ID、标签、版本、聚合指标和逐例判定，不保存输入正文或模型原始输出。

固件由 `backend/scripts/generate_cds0_evaluation_fixture.py` 确定性生成；结构、不相交标签、数量、哈希和报告覆盖由 `backend/tests/test_cds0_baseline.py` 锁定。

## 4. 旧算法基线

| 指标 | 结果 |
|---|---:|
| 场景数 | 300 |
| 精确匹配率 | 63.67% |
| 出现误选的场景率 | 33.00% |
| 出现漏选的场景率 | 18.00% |
| 误选项 / 漏选项 | 234 / 54 |
| 平均 / P95 本地判定延迟 | 0.209549 ms / 1.330395 ms |
| 估算输入 token | 7,224 |

完整分轨和逐例无正文证据见 `docs/reports/cds-0-legacy-baseline.json` 与同名 Markdown。延迟是本次本机离线测量值，只作为相同环境下的方向性对照；token 是保守估算，不冒充 Provider usage。

已知差距保留为后续阶段的对照目标：无模型时 Relationship 必然保守回退；CTX v1 固定预算比例可能保留语义无关组件；History 与 Presence 的确定性规则存在边界误选和漏选。CDS.0 不修复这些差距。

## 5. 验收、回滚与下一步

- [x] 前置 PR 已合并并记录不可变 SHA。
- [x] Schema 60、测试基线、计划版本及当前协议已冻结。
- [x] 300 个合成场景及三类标签已落盘并受测试保护。
- [x] 旧算法误选、漏选、延迟和估算 token 已记录。
- [x] 聊天行为、数据库和冻结协议未改变。
- [ ] 独立 review 完成并确认可进入 CDS.1。

若 review 否决本阶段，可仅回退 CDS.0 新增的生成器、评测固件、测试和两份报告以及对应文档记录；无需数据迁移或运行时恢复。未取得下一阶段确认前停止施工。

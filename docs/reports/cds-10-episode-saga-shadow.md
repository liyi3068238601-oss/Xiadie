# CDS.10 Episode/Saga 叙事判断纯 Shadow 评测

- 样本：240 个纯合成场景，覆盖 12 个规则模板；不含用户数据，不调用 Provider。
- Fixture SHA-256：`9513d7a792b4702ea261fde8d83acaf1c1b9b6b74d6d9373c7d0658349c0e403`

## 完成门

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| 提案精确匹配率 | 100.00% | 100% |
| 成员来自候选集合 | 100.00% | 100% |
| 低置信度选中率 | 0.00% | 0 |
| 高影响 merge 自动执行率 | 0.00% | 0 |
| Shadow application_allowed 率 | 0.00% | 0 |
| 安全违规数 | 0 | 0 |
| MEM 领域表写入数 | 0 | 0 |

## 边界

- CDS.10 阶段未新增迁移、表或列；阶段基线为 Schema 62，当前项目为 Schema 81。
- EpisodeBoundaryProposal 与 SagaTransitionProposal 使用独立输入/输出 Schema，均固定 Shadow。
- 独立 oracle：`cds10-narrative-safety-oracle-v3`，不读取 fixture expected。
- adapter 只接受真实 pending/qualified 候选，复核资格并强制绑定候选及 Fragment/Episode/Saga 完整来源链 hash（含 Fragment→Episode、Episode→Saga 反向归属）；MEM 继续是唯一 application owner。
- Episode 资格门检查 Fragment 未归属任何正式 Episode；Saga 资格门检查 Episode 未归属除目标 Saga 之外的任何正式 Saga；任何归属变化使来源 hash 失效。
- Episode 所选边界必须连续；Saga 非 skip 提案至少包含 2 个成员。
- merge_suggestion 始终 high_impact 且 execution_allowed=False；revive 仅接受 user_confirmed 来源。

## 原始叙事回归语料

- 角色：`labeled_raw_narrative_regression`；SHA-256：`b72ce4d56a89c68ec0e67f28ab56bcc1ce530c8f59960a92186bf3c6a7610147`。
- 标签：人工编写的合成标签，未经过独立评审。
- 用途：只观察规则与标签在真实候选路径上的差异，不作为 Shadow→Advisory/Active 晋级证据。
- 候选路径：`real_database_candidates`；样本：8；正确/错误：4/4。
- Accuracy：50.00%。
- Macro precision / recall / F1：38.89% / 50.00% / 43.33%。

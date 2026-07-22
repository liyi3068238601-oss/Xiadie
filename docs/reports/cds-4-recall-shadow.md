# CDS.4 RecallPlanner Shadow 评测

- 样本：600 轮纯合成输入；不含用户数据，不调用真实 Provider。
- Fixture SHA-256：`863512758e66d38998a1c9bfc67b81cde70dbdfb43fb430f6834912ace3f5d37`
- Shadow 任务与来源需求精确匹配：100.00%
- 冻结旧触发器来源精确匹配：8.33%

## 安全门

| 指标 | 结果 |
|---|---:|
| 必需来源召回率 | 100.00% |
| 明确禁止后仍选择来源 | 0.00% |
| 查询建议有界率 | 100.00% |
| source message 绑定率 | 100.00% |

## 边界

- Planner 只输出 SourceKind、任务/查询意图、需求等级与最多8个查询词。
- 不执行检索、不生成候选、不读取正文、不注入 ContextPackage。
- CTX/Knowledge/MEM/Lore/Episode-Saga 保留权限、候选和最终预算裁决权。

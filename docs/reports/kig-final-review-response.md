# KIG.10～KIG.15 独立 Review 处置记录

- Review 输入：项目外 `E:\Xiadie\review\kig-final-review\kig-final-review.html`
- Review 结论：0 P0、0 P1、3 P2、2 项设计观察
- 最终实现/回滚点：`96021838418d5c5d9d26b269784447a099a68cc3`
- Schema：80；KIG-R Schema 76 冻结边界未变

## 采纳决策

| 项目 | 决策 | 实施结果 |
|---|---|---|
| P2-1 Shadow `persist_payload` 非事务性 | 采纳 | 中途失败时精确补偿本批 Entity、Claim、Relation、Event、SourceLink、`derived_dependencies` 与实体预算计数；原异常继续向上返回 |
| P2-2 entity detail 用 `LIKE` 搜索 JSON | 采纳 | 改用 SQLite `json_each()` 精确值匹配，并纳入 location/participant/object 三类事件关联 |
| P2-3 merge preview 用 `LIKE` 搜索 JSON | 采纳并扩大到完整同类路径 | preview、merge 应用与 operation snapshot 全部改用同一 JSON 精确成员语义 |

## 暂不采纳的设计观察

1. Shadow 提取动态 token 预算：当前 12 Entity/24 Claim/24 Relation/12 Event 硬上限和 `max_tokens=1600` 保持不变。调整需要重新评估截断率、成本和敏感信息误抽取率，不作为冻结补丁。
2. 知识变更后触发维护：默认 weekly 与手动 scan 保持不变。该能力属于 KIG 维护调度所有权，不并入 CIE；未来另行设计防抖、资源预算和聊天隔离门。

## 验证

- P2 定向回归：`18 passed`。
- 后端全量：`2560 passed, 1 warning`。
- 前端：`52 passed`；TypeScript/Vite 生产构建 190 modules。
- Electron lifecycle contract：`3 passed`。
- `kig-p-acceptance-v1`：`release_gate=pass`；报告 `implementation_head` 与最终回滚点一致。

最终结论：3 个 P2 已关闭，0 个未解决 P0/P1/P2；KIG v1 可保持冻结并作为 CIE 前置基线。

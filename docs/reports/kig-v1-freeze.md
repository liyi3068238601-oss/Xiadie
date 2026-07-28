# KIG v1 冻结声明

KIG-R 的不可变边界保持：实现 rollback `a18fd04a3759663f88d6a8041529fea14645c281`、Schema 76、协议 `kig-retrieval-governance-v1`。KIG-P 从 Schema 77 追加到 Schema 80，不修改 48～76 历史迁移。

KIG v1 新增冻结面：

- PWM：`pwm-projection-v1` / `pwm-extraction-shadow-v1`
- Entity resolution：`pwm-entity-resolution-v1`
- Owner proposal：`kig-system-proposal-v1`
- Maintenance：`kig-maintenance-v1`
- 最终验收：`kig-p-acceptance-v1`

KIG-P 初始实现为 `5b6054d5cc57a5d09cbe305045487a527e760071`；独立 Review 修复后的最终不可变实现/回滚点为 `96021838418d5c5d9d26b269784447a099a68cc3`。PWM 是可重建导航投影，不成为 Knowledge、MEM、LIFE、EAP 或 Tool 的权威写入者。

## 最终验证

- 后端全量：`2560 passed, 1 warning`；唯一警告为 TestClient 依赖弃用提示。
- 前端：`52 passed`；TypeScript 与 Vite 生产构建通过，`190 modules transformed`。
- Electron：lifecycle contract `3 passed`。
- KIG-P 独立验收：`release_gate=pass`，报告实现 HEAD 与上述回滚点一致；300 个检索、100 个版本纠正、100 个实体合并/回滚场景通过，1 万/10 万/25 万 Chunk 探针召回均为 100%。

## Review 结论

最终独立 Review 为 0 个 P0/P1、3 个 P2。三项 P2 全部采纳：Shadow 提取批次在中途失败时补偿本批实体、来源链接、依赖与预算；实体详情、merge preview、merge 应用和回滚快照统一使用 SQLite `json_each()` 精确匹配事件成员。两项设计观察不改变冻结范围：动态 Shadow token 预算需要新的成本/质量校准，知识变更触发维护仍归 KIG 后续维护，不转移给 CIE。详见 `docs/reports/kig-final-review-response.md`。

收口前还修复了实体合并遗漏 Event/StateAssertion、operation journal 保存派生正文、跨 owner proposal 夹带正文、临时聊天跨会话污染及临时标记写锁问题；对应回归均已纳入全量测试。已知非阻断项仅为 TestClient 弃用提示与 Vite 对既有 Live2D 普通脚本的打包提示。

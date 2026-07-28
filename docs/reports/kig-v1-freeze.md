# KIG v1 冻结声明

KIG-R 的不可变边界保持：实现 rollback `a18fd04a3759663f88d6a8041529fea14645c281`、Schema 76、协议 `kig-retrieval-governance-v1`。KIG-P 从 Schema 77 追加到 Schema 80，不修改 48～76 历史迁移。

KIG v1 新增冻结面：

- PWM：`pwm-projection-v1` / `pwm-extraction-shadow-v1`
- Entity resolution：`pwm-entity-resolution-v1`
- Owner proposal：`kig-system-proposal-v1`
- Maintenance：`kig-maintenance-v1`
- 最终验收：`kig-p-acceptance-v1`

KIG-P 不可变实现/回滚点为 `5b6054d5cc57a5d09cbe305045487a527e760071`。PWM 是可重建导航投影，不成为 Knowledge、MEM、LIFE、EAP 或 Tool 的权威写入者。

## 最终验证

- 后端全量：`2558 passed, 1 warning`；唯一警告为 TestClient 依赖弃用提示。
- 前端：`52 passed`；TypeScript 与 Vite 生产构建通过，`190 modules transformed`。
- Electron：lifecycle contract `3 passed`。
- KIG-P 独立验收：`release_gate=pass`，报告实现 HEAD 与上述回滚点一致；300 个检索、100 个版本纠正、100 个实体合并/回滚场景通过，1 万/10 万/25 万 Chunk 探针召回均为 100%。

## Review 结论

最终 Review 为 0 个未解决 P0/P1。收口中修复了实体合并遗漏 Event/StateAssertion、operation journal 保存派生正文、跨 owner proposal 夹带正文、临时聊天跨会话污染及临时标记写锁问题；对应回归均已纳入全量测试。已知非阻断项仅为 TestClient 弃用提示与 Vite 对既有 Live2D 普通脚本的打包提示。

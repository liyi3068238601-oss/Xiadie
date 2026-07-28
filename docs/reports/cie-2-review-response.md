# CIE.2 独立 Review 处置

- Review：`E:\Xiadie\review\cie2-final-review\cie2-final-review.html`
- 结论：通过，0 个未解决 P0/P1；允许冻结 CIE.2 并进入 CIE.3。

## 建议处置

- P2-1 采纳并延后至 CIE.4：当前 `retrieval` 事件是同步准备完成后的生成前取消检查点，不宣称能够中途打断同步检索。CIE.4 将用户可见语义统一为更诚实的 `preparing/generating`。
- P2-2 立即采纳：前端停止/补充、控制面收口、测试、验收、ADR 与文档作为独立 CIE.2 提交。
- P2-3 采纳并延后至 CIE.6：加入真实 Provider 端到端取消响应时间及离散度；当前微秒指标只代表进程内控制面。
- OBS-1 记录：单进程完成重放 TTL 5 分钟、活动请求 TTL 10 分钟，不承诺跨重启幂等；多 worker 或恢复需求必须另立迁移和 ADR。
- OBS-2 记录：当前本地 API 是单用户、统一本机 token；未来多用户化时，取消端点必须增加会话主体归属校验。

## 验证更正

Review 报告记录“后端全量跳过”，但在最终自审修复完成后已实际执行全量测试：`2583 passed, 1 warning`。前端为 `61 passed`，TypeScript 与 Vite 生产构建通过（191 modules）。因此 Review 后仅文档状态变化，不重复执行后端全量。

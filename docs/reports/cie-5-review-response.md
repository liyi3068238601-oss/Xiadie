# CIE.5 独立 Review 处置

- Review：`E:\Xiadie\review\cie5-review\cie5-review.html`
- 结论：通过，0 P0 / 0 P1，2 P2，允许进入 CIE.6。
- 日期：2026-07-29

## 处置

1. **采纳 P2：Unicode 混淆注入归一化。** 在注入正则前执行 NFKC，并移除零宽字符和 BOM；新增全角 `ｉｇｎｏｒｅ` 加零宽字符用例。字段白名单、低权限渲染与 CTX 类型门继续作为纵深防御。
2. **不采纳 P2：跨来源读锁。** KIG 已在候选预检后对 owner SourceRef 做第二次 revision/hash/status/privacy 复核，变化时以 `evidence_changed_during_governance` 拒绝。为了减少极小概率的无效计算而跨 Message/Memory/LIFE/Knowledge/ToolRun 等 owner store 持长读锁，会扩大耦合和聊天阻塞，不符合现有短事务原则。
3. **contributor 热重载继续延期。** 当前只允许受信任应用代码在启动期注册。动态插件代码签名、沙箱和注册权限属于未来独立插件安全专项，不能由 ContextContribution 协议顺带开放。

CIE.5 Review 已关闭，ADR-0070 与验收报告转为冻结状态。

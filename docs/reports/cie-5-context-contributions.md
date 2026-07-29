# CIE.5 第三方 ContextContribution 验收

- 协议：`context-contribution-v1`
- Schema：81（未占用 82）
- ADR：ADR-0070
- 机器报告：`docs/reports/cie-5-context-contributions.json`
- 状态：独立 Review 通过并冻结（0 P0 / 0 P1）

## 已实现

- 受信任代码可注册有 kind/privacy 白名单和独立超时的 contributor；新来源默认关闭，用户逐来源启用后才收到本轮查询，候选仍一律视为不可信。
- 候选包含 source、kind、revision/hash、TTL、privacy、priority、token estimate、受限 payload、幂等 ID 与 KIG 证据。
- KIG 拒绝未注册来源、非法字段、system/developer 形状、注入文本、hash 不符、token 低报、过期候选、陈旧/撤回证据、临时会话越界和未授权远传。
- CTX 只接收治理类型，按优先级与独立预算保留完整 JSON 记录，不把第三方数据变成新的高权限消息。
- 高级上下文诊断不返回正文，并提供逐 contributor 开关、最近状态、耗时和无正文拒绝计数。
- 单一 contributor 超时、异常或非法输出降级为空；真实 `/api/chat` mock 路径证明异常不影响 done。

## 验收结果

- 第三方自由 Prompt 注入率：0。
- 过期/陈旧贡献应用率：0。
- 未授权远传率：0。
- 重复 ID 应用率：0。
- 单一 contributor 失败影响基础聊天率：0。
- 诊断正文持久化率：0。
- ContextPackage 超预算率：0。

自动验证：CIE.0～5、CTX 预算/控制及 KIG 来源/证据/新鲜度针对性后端共 104 项通过；前端 71 项通过；TypeScript/Vite 生产构建 192 modules 成功。未调用 Provider，未新增数据库迁移。

独立 Review 的两项 P2 处置见 `docs/reports/cie-5-review-response.md`：采纳 Unicode NFKC/零宽归一化；不引入跨 owner store 长读锁。允许进入 CIE.6。

## Review 建议重点

1. contributor 超时线程、异步异常、禁用与非法返回是否都只降级自身，且不会延迟或中断基础聊天。
2. 任意 candidate payload 是否可能通过字段嵌套、role/message 形状或指令文本进入 system/developer 权限层。
3. KIG 是否在每轮重新核对证据 revision/hash/status/privacy，并正确处理本地/远端和临时会话边界。
4. 多个高 token、高 priority 候选是否由 CTX 稳定排序并按完整 JSON 记录裁剪，绝不突破硬预算。
5. 诊断与开关 API/UI 是否完全无正文，且关闭 `cie_enabled` 时不调用 contributor。

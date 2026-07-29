# ADR-0070：CIE 第三方上下文贡献治理边界

- 状态：已冻结（独立 Review 0 P0 / 0 P1）
- 日期：2026-07-29
- 协议：`context-contribution-v1`
- Schema：81（CIE.5 不占用 82）

## 决策

第三方能力只能由受信任的应用代码注册为进程内 contributor；注册行为既不等于信任其输出，也不等于用户同意向它披露本轮输入。每个新注册来源默认关闭，只有用户逐来源启用后才收到查询。每个候选必须声明稳定 ID、source、kind、revision、SHA-256、创建/过期时间、privacy、priority、token estimate、受限 candidate payload 与 KIG SourceRef 证据。协议不提供网络端点让任意调用方注册 contributor，也不允许候选提交 message 列表、role、system/developer Prompt 或工具权限。

每个 contributor 独立执行，默认 200 ms、最大 1 秒，最多返回 8 项；同步适配器移到工作线程，异步适配器受 `wait_for` 约束。超时、异常、禁用或非法返回只使该来源降级为空，不阻塞其他来源和基础聊天。

## KIG → CTX 所有权

KIG 是唯一治理门：它复核 contributor 注册权限、协议与字段白名单、幂等 ID、TTL、payload hash、token 低报、提示注入特征、Provider 位置、临时会话边界，以及证据在 owner store 中的当前 revision/hash/status/privacy。事实候选没有证据、证据过期/撤回或远传范围不允许时必须拒绝。

独立 Review 后补强：注入检测前执行 Unicode NFKC 并移除零宽字符/BOM。证据继续采用预检与提交前二次复核；不跨 owner store 引入长读锁。

CTX 只接受 `GovernedContribution` 类型，不接受任意 mapping。候选按 priority 稳定排序，在 `ContextPackage` 的 `third_party_context` 独立份额中按完整 JSON 记录裁剪；永不截出半条 JSON。它只作为 system Prompt 中明确标记的“低权限、不可信候选数据”出现，不产生新的 system/developer message，不改写持久聊天链。

## 数据与诊断

- 候选正文只存在于单轮内存，不写入 messages、Memory、Knowledge、KIG 派生表或诊断。
- contributor 开关复用现有 settings；无需新表或迁移。
- 只读诊断仅返回注册版本、开关、超时、状态、耗时、候选/接受/拒绝计数和原因码。
- contributor ID、请求 ID、hash、locator 与计数可以用于治理；异常文本和 candidate payload 不进入诊断。

## 回滚

关闭 `cie_enabled` 会完全跳过收集与治理；也可逐 contributor 关闭。移除注册或 CIE.5 接线即可恢复 CIE.4 行为，不需要数据转换或 Schema 回滚。

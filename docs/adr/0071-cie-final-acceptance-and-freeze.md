# ADR-0071：CIE v1 整体验收与正式冻结

- 状态：已冻结（独立 Review 0 P0 / 0 P1）
- 日期：2026-07-29
- 最终验收协议：`cie-final-acceptance-v1`
- Schema：81

## 决策

CIE v1 由一个总门 `cie_enabled` 和五个独立、可降级能力组成：消息积累 `turn-envelope-v1`、生成控制 `cie-cancel-control-v1`、实证图片 `vision-probe-v1`/`cie-image-attachment-v1`、客户端节奏 `reply-presentation-v1`、第三方上下文 `context-contribution-v1`。关闭总门恢复 CIE.0 的单消息、单生成、文本 SSE 和本地文本附件路径；各子能力失败不得改变消息、Memory、Knowledge、LIFE、KIG、EAP 或 Tool 的既有所有权。

最终完成门采用零容忍安全指标，不用平均体验分掩盖消息丢失、跨会话合并、幽灵回复、重复持久化、图片越权、vision 假声明、第三方 Prompt 注入、过期贡献或 CIE 失败影响基础聊天。体验延迟和自然度只作补充报告。

## 验收边界

- 5/20/100/500 轮、取消/重放、图片目标变化和 ContextContribution 攻击矩阵使用纯合成数据。
- 在线/断网、前后台、休眠恢复和时钟回拨由确定性模拟、API 与 Electron 生命周期 contract 验证；验收不会真实断开用户网络、强制系统休眠或回拨系统时钟。
- Windows 当前源码使用隔离数据目录实际启动后端、Vite 与 Electron，确认服务健康和进程持续存活，再清理本次创建的进程与临时目录。
- 当前配置的 DeepSeek 已在 CIE.3 真实 vision 探针中返回不支持；CIE.6 不重复消耗 Provider token，也不把模型名称当能力证据。

## 冻结条件

后端全量、前端测试/构建、Electron contract、当前源码 Windows 烟测与发布资源验证必须通过，且独立 Review 为 0 个未解决 P0/P1。Review 前协议为候选冻结；Review 通过后只允许兼容修复，新增能力进入后续专项和新协议版本。

## Review 收口

2026-07-29 独立 Review 确认 0 个 P0/P1，专项测试 7/7、零容忍指标 10/10 和验收矩阵 36/36 通过，允许正式冻结。两个 P2 均不改变冻结结论：异常退出残留图片的周期清理由后续维护调度设计处理；回放 payload 的观察器元数据精简须在保持重放契约兼容的前提下另行评审。

# CIE.4 回复节奏与输入状态验收

- 协议：`reply-presentation-v1`
- Schema：81（未占用 82）
- 固定集：CIE.0 `rhythm` 20 条纯合成样本
- 机器报告：`docs/reports/cie-4-reply-presentation.json`
- 状态：独立 Review 通过并冻结（0 P0 / 0 P1）

## 验收结果

- 文本重组差异率：0。
- 重复发送率：0。
- 代码块破坏率：0。
- 用户打断后未展示片段泄漏率：0。
- 语义改写模型调用：0。

## 实现证据

- `splitPresentationUnits()` 只返回原字符串切片，保护围栏代码、行内代码、URL、Markdown 链接、小数/版本号和引号闭合。
- `ReplyPresentationBuffer` 对展示单元做短间隔排队；服务端 final 到达时清空队列并整体替换为权威正文。
- 用户补充消息只有在服务端接受取消后才丢弃未展示队列；会话切换、卸载、abort、cancel 和 error 均清除计时器。
- 阶段标签使用自然体验文案，不把 retrieval 等内部技术阶段冒充用户可理解的思考过程。
- CIE 关闭时不建立缓冲，继续原始 delta fallback。
- Review 收口后，权威 final 到达即把展示阶段设为 completed；协议 state 保留内部枚举，用户界面继续统一映射自然文案。

## 自动验证

- `frontend/tests/replyPresentation.test.mjs`：逐字重建、保护结构、节奏无重复、终态替换、用户打断及 20 条固定集零容忍门。
- 前端共 70 项通过；TypeScript 与 Vite 生产构建通过（当前 192 modules）。
- CIE.4 为纯客户端改动，没有 Provider 调用或 Schema 82；后端只需复核 CIE.3 Review 的轻量 GC 加固，不需要为本阶段重复跑全量。

## 独立 Review 结论

五项审查重点全部通过，0 P0 / 0 P1。两项 P2 的处置见 `docs/reports/cie-4-review-response.md`；允许进入 CIE.5。

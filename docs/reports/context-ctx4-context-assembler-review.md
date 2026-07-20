# CTX.4 统一 ContextAssembler 与摘要注入完工 Review

- 日期：2026-07-20
- 阶段：CTX.4
- Schema：43（本阶段无迁移）
- 协议：`context-package-v1` + `context-budget-v1` + `conversation-summary-v1`
- 结论：实现完成，等待独立 strict review 后进入 CTX.5

## 产品边界

本阶段只改善遐蝶延续旧对话的能力。普通聊天界面不展示摘要正文、revision、token、内部推理或技术状态；
用户只会感受到较早决定能自然延续。原始对话仍完整保存在本地，未因摘要注入而改写或删除。

## CTX.3 Strict Review 处置

独立 review 对 CTX.3 给出通过结论，0 个新增返工缺陷。面向 CTX.4 的五条 P0 建议全部采纳：

| 建议 | 处置 | 落地 |
|---|---|---|
| source_end 后保留原文、覆盖原文去重、摘要失效安全降级 | 采纳 | 来源复核后只移除覆盖范围；失效走 CTX.1 |
| ContextAssembler 不直接信任摘要或检索器 | 采纳 | 纯模块只接收结构化候选，不导入检索/摘要服务 |
| 5/20/100/500 轮与半轮边界预算测试 | 采纳 | 新增参数化合成测试和半轮拒绝测试 |
| regenerate 成功/失败与 revision 一致 | 采纳 | 成功失效、失败保留、生成中新增不污染均有回归 |
| 摘要不可用不能恢复全历史 | 采纳 | 缺失、hash 变化、边界错误统一安全裁剪 |

## 实现摘要

1. 新增 `backend/app/context_assembler.py`，成为聊天模型请求的唯一上下文组装入口。
2. active revision 使用前重新校验协议、状态、连续完整轮次、消息数和 source hash；摘要不能覆盖当前消息。
3. 有效摘要进入人格 system prompt 的“较早对话连续性摘要”区，并明确它是过去对话的派生数据而非本轮指令。
4. 摘要覆盖范围不再作为 raw messages 发送；source_end 后原文按 CTX.1 最近完整轮次规则保留。
5. 摘要、长期记忆 digest、知识和 Lore 各自限额，总可选 system 区不以填满模型窗口为目标。
6. `main.py` 只取得候选和处理授权/审计；记忆实际注入集合变化时重新组装，避免请求与 meta 不一致。
7. context meta 增加无正文的 summary revision、覆盖消息数、最近原文轮数和各组件 token 计数。

## 专项验证

- 5、20、100、500 轮：全部成功，且 `reserved_total_tokens <= context_window_tokens`。
- 覆盖去重：摘要涵盖的旧 user/assistant 原文不再出现在实际模型 messages。
- 边界：source_end 落在 user 消息、source hash 变化或摘要缺失时拒绝使用摘要。
- 降级：100 轮长会话在无可信摘要时仍会裁剪，不发送全历史。
- 组件竞争：长摘要、知识、记忆、Lore 与长用户输入同时存在时，当前用户消息受保护且预算成立。
- 短会话/mock：消息形状与既有体验一致。
- regenerate：成功替换后 revision 失效；模型失败时 revision 保持 active。

## 全量验证结果

- 后端：`python -m pytest -q`，469 passed。
- 前端：`npm.cmd test`，33 passed。
- 前端生产构建：TypeScript + Vite，188 modules transformed，成功。
- Electron：`node --check desktop/main.js` 与 `node --check desktop/preload.js`，成功。
- 已知非阻塞提示：Starlette/httpx 弃用 warning；Vite 的 Live2D core 非 module 提示，均为既有提示。

## 保留边界

- 本阶段没有跨会话搜索；那属于 CTX.5。
- 本阶段不修改 Fragment、Episode、Saga、Archivist 的召回、分层或生命周期。
- 组件限额使用确定性保守比例；相关性评分和跨会话动态预算需由 CTX.5/CTX.7 评测校准。
- 不新增普通前端诊断展示，符合用户“尽可能贴近陪伴、聊天、伴侣核心”的决定。
- 用户未提交的 `docs/reports/knowledge-recall-eval-v3-search-v2.md` 未编辑、未覆盖、不会纳入本阶段提交。

## 下一阶段入口

独立 review 无未解决 P0/P1 后进入 CTX.5。下一阶段只增加“相关会话 → 少量完整原始轮次”的两阶段召回，
不得把所有会话正文拼入模型，也不得把普通历史问题污染为长期 Fragment。

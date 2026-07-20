# CTX.5 跨会话历史回忆完工 Review

- 日期：2026-07-20
- 阶段：CTX.5
- Schema：44
- 协议：`conversation-history-index-v1` + `conversation-history-score-v1-shadow` + `context-package-v1`
- 结论：实现完成，等待独立 strict review 后进入 CTX.6

## 产品边界

本阶段让“新建话题会话”不再等同失忆。遐蝶可在用户明确询问共同过往时找到真实原始问答，并以自然
伴侣式语言继续交流。普通聊天界面没有新增历史来源、评分、token、检索过程或技术卡片。

“12×8”仍只属于原始对话档案，不会被写成长期人格记忆。Fragment、Episode、Saga、Archivist 及其
生命周期未被修改。

## CTX.4 Strict Review 处置

评审结论为通过，0 个未解决 P0/P1。五项 CTX.5 建议全部采纳：

| 建议 | 处置 | 落地 |
|---|---|---|
| 跨会话结果使用独立动态预算 | 采纳 | `cross_session_recall` 独立组件，不挤占当前原文 |
| 真正两阶段检索与完整轮次/locator | 采纳 | 先 session、后 message，扩展完整 user/assistant 轮次 |
| 新建、归档、永久删除生命周期明确 | 采纳 | 新建不隔离；归档可召回；永久删除同步清索引 |
| 固定评测、shadow、可解释无正文诊断 | 采纳 | 普通问答默认 shadow；权重和评测协议均有版本 |
| 不同来源身份与真实定位 | 采纳 | current/history/memory/knowledge 分型，返回真实 locator |

## 实现摘要

1. schema 44 新增两个可重建 FTS5 trigram 索引和无正文 recall event ledger。
2. 新增 `history_recall.py`，只在本地进行 query cleaning、候选会话排序、候选轮次排序和多样性选择。
3. 默认 `explicit_only`；明确回忆请求可注入，普通问答只做 shadow 评测。
4. ContextAssembler 对跨会话候选重新验证、完整块裁切，并使用独立动态预算。
5. system prompt 将历史轮次标为低权限真实过往资料，不允许把它冒充长期记忆、知识或本轮指令。
6. 后端提供索引重建和无正文事件查询入口；SSE 只返回 ID、标题与 locator，不复制历史正文。

## 专项验证

- 会话 A 的 `12×8` 能在会话 B 的明确回忆请求中找回完整真实轮次。
- 项目、生活、闲聊等候选并存时只选择相关来源；较旧强相关优先于较新无关会话。
- 中文同义表达、代码符号、日期、短查询和标题信号进入固定评测集。
- 新会话不清索引；长期记忆开关不影响历史召回；已有长期记忆不因会话删除而调用或改写。
- 归档会话可召回；永久删除后原文、摘要和两个索引均失效。
- title 更新、active summary 激活/失效会同步索引；全量索引可以重建。
- ContextAssembler 拒绝当前会话、缺半轮、重复或伪造来源候选；当前消息与最近完整轮次受保护。
- recall event、诊断和 ContextPackage meta 不含 query 或历史正文。

## 保留边界

- 普通问答的自动历史注入尚未启用；先积累 shadow 结果并在 CTX.7 固定评测中校准。
- 第一版是本地词法/解释性信号检索，不把 embedding 或模型判断放进主聊天阻塞路径。
- 普通 UI 不展示历史来源；“参考过往聊天”开关与高级诊断属于 CTX.6。
- 用户未提交的 `docs/reports/knowledge-recall-eval-v3-search-v2.md` 未编辑、未覆盖、不会纳入本阶段提交。

## 全量验证结果

- 后端：`python -m pytest -q`，484 passed。
- 前端：`npm.cmd test`，33 passed。
- 前端生产构建：TypeScript + Vite，188 modules transformed，成功。
- Electron：`node --check desktop/main.js` 与 `node --check desktop/preload.js`，成功。
- 已知非阻塞提示：Starlette/httpx 弃用 warning；Vite 的 Live2D core 非 module 提示，均为既有提示。

## 下一阶段入口

独立 review 无未解决 P0/P1 后进入 CTX.6。下一阶段只增加用户可控开关、重建与高级诊断/隐私说明，
不得把技术细节常驻展示在聊天界面，也不得修改现有长期记忆设置语义。

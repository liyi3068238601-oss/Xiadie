# KIG.8 证据、引用与支持度施工报告

- 日期：2026-07-28
- Schema：75
- 协议：`knowledge-retrieval-bundle-v1` / `claim-support-v1`
- 阶段结论：完成，可进入 KIG.9

## 复用与补差

1. Knowledge 文档继续使用既有 `knowledge_message_citations`、K1 白名单、Chunk locator 和 `/api/knowledge/citations/{id}` 原文入口；KIG 不复制知识 Citation，也不成为知识正文的新写入者。
2. 只有消息、记忆、LIFE、ToolRun 与 Lore 的跨源缺口进入 `kig_evidence_links`。表内只保存 SourceRef 快照、excerpt hash、locator、relation 和校验状态，不保存 excerpt 或 owner body。
3. `KnowledgeRetrievalBundle` 作为 KIG→CTX 的唯一结构化交接。ContextAssembler 自己验证协议/字段/数量并在原有知识预算中裁剪，KIG 不绕过 ContextPackage 直接拼接模型请求。

## 生成后安全门

- citation key 必须属于本轮 E1～E12 白名单。
- 每条证据在验收时重新解析 owner SourceRef，并逐项比较 status、revision、hash、privacy 和 locator。
- `claim-support-v1` 切分 AnswerClaimSegment，区分事实、比较、时间、建议、观点等，并产生 supported、partially_supported、conflicted、insufficient 或 not_checkable。
- 产品名、版本号等锚点必须精确一致；仅“主题相似”不能支持不同产品/版本的断言。
- partial/conflict/insufficient 没有不确定性措辞时自动增加明确限定；伪造、失效、unsupported citation 不进入可点击来源条。
- 原文打开实时读取 owner store；来源变化、撤销或删除时只显示“来源不可用”，不会泄漏历史快照正文。

## 自动验收

- KIG.8 专项测试 10 项：Schema body-free、Knowledge 复用边界、prompt injection、CTX 结构化接线/预算、伪引用、同主题不支持、partial/conflict 限定、无证据、来源变化、持久化/API/UI。
- KIG.0～8、Knowledge、CTX、API 核心回归：`311 passed, 1 warning`。
- 前端：`51 passed`；TypeScript 与 Vite 构建通过，190 modules transformed。
- 零容忍观测：invented citation 可点击数 0；unsupported citation 可点击数 0；失效来源快照正文回放数 0；Knowledge Citation 重复写入数 0。

## 回滚

回滚应用代码后可保留 Schema 75 空表；这些表只含派生元数据并由消息级联清理。Knowledge、Memory、CTX、LIFE、ToolRun 与 Lore 的权威数据及既有行为不依赖这些表。

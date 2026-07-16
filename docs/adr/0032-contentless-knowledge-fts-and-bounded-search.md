# ADR-0032：Contentless 知识 FTS 与受限本地检索

- 状态：Accepted
- 日期：2026-07-16
- 阶段：知识库 F.5
- 关联版本：schema 31

## 背景

F.4 已产生稳定 chunk 和 locator，但 document 仍停在非 indexed 状态。索引必须一次性切换可见性、支持中文
短词、在删除或禁用后立即退出召回，并且不能为了 FTS 再保存一份完整正文。稳定 chunk ID 是 TEXT，不能
直接套用依赖整数 rowid 的 external-content 同步模式。

## 决策

1. schema 31 使用 `content=''`、`contentless_delete=1` 的 FTS5 表。FTS rowid 只临时对应当前
   `knowledge_chunks.rowid`；API、引用和审计永远使用 ADR-0031 的确定性 TEXT chunk ID。
2. FTS 不复制正文，只写 `knowledge-fts-terms-v1` 派生词项。中文连续文本生成单字与相邻双字词项，英文、
   数字和下划线词按 casefold 后的完整词处理。协议变化必须提升 index 版本并重建。
3. 查询不得直接进入 FTS MATCH。服务端使用同一词项器，去重后最多取 16 个词项，并用逐项引号和 AND
   组合；空白、纯符号、超过 256 字符、超过 20 个 document 或超过 10 个标签过滤项的请求被拒绝。
   document 标签保存为受 JSON 数组约束的本地元数据，F.5 支持任一标签命中过滤，编辑入口留给 F.7。
4. worker 在事务外读取 chunks，逐项验证 ordinal、chunker 版本和正文哈希并生成词项。短写事务重新确认
   chunk rowid/ID/哈希未变化，清除该 document 旧词项、插入全套新词项并核对 FTS 行数等于 chunk_count，
   最后同时设置 document `indexed/indexed_at/index_version` 与 run `completed`。
5. document 只有在上述事务提交后才可检索。索引失败或进程中断保持非 indexed 并进入有限重试；取消检查
   在准备前后执行。取消清除 FTS、chunks、artifact 和派生元数据，但保留用户导入的原始副本。
6. `knowledge_chunks` 的 BEFORE DELETE 触发器删除对应 contentless FTS row。检索还必须连接 document 与
   collection，并只接受 `document.status='indexed'`、`indexed_at IS NOT NULL`、当前 index_version 和
   collection active 的行；
   因此删除/禁用状态提交后立即不可召回，即使后续物理清理尚未结束。
7. 本地检索支持 collection、document 和标签范围，最多返回 12 个主命中，总正文预算默认 4000、最大
   8000 字符。可选 `context_window=1` 只扩展
   同 document 的相邻 ordinal；所有结果按 chunk ID 去重，每条保留自己的原始 locator 和 `context_of`。
8. F.5 API 返回真实正文与 locator 是为了本地检索验证，但不会自动进入模型提示。F.6 必须另建低权限资料
   区块、token 预算、引用校验与提示注入审计，不能把本 API 的存在误报为已接入对话。

## 后果

- 中文双字词和单字、英文词可以在纯本地 SQLite 中检索，且原始 FTS 语法无法注入 MATCH 表达式。
- contentless FTS 依赖支持 `contentless_delete=1` 的 SQLite；当前打包运行时和测试环境必须在构建验证中覆盖。
- 词法 FTS 不等于语义检索。远程或本地 Embedding 仍受 ADR-0029 约束，不在 F.5 范围内。

## 验证

- 自动测试覆盖 contentless 行为、中英文词项、collection/document/状态过滤、预算、去重、相邻 locator、
  原子完成、取消、事务回滚、chunk_count 不一致、删除触发器、API 鉴权和旧库升级。
- 后端全量 293 项、前端 21 项、生产构建和 Electron 主进程语法检查通过后，F.5 才勾选完成。

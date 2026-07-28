# KIG.2 文档与原子索引治理施工报告

- 日期：2026-07-27
- Schema：73
- 结论：通过；KIG.3 可开工

## 审查结论

既有 Knowledge 已具备文档、解析产物、稳定 Chunk、FTS、Dense、重试删除、索引版本和搜索契约，均继续作为权威实现。唯一阻断验收的缺口是 reindex 在任务入队时先删除活动 Chunk/FTS/Dense，导致重建期间和失败后不可查询。

## 补差实现

- `knowledge_rebuild_chunks` 按 import run 保存旁路候选；不建立第二套 KnowledgeDocument。
- staged parser/chunker metadata 存于原 `knowledge_import_runs`，活动文档元数据在切换前不改变。
- 文档在 rebuild 全程保持 `status=indexed`；`rebuild_status` 独立呈现 building/failed/idle。
- 最终切换在同一个 SQLite 事务内完成：校验 staging、清理旧 FTS/Dense、替换活动 Chunk、写入新 FTS、核对数量、更新文档 revision、完成 run。
- 任一步失败或取消只清理 staging，活动 Chunk/FTS 和原文件不变。
- `governance_status` 提供 archive/restore；搜索、Dense、引用原文和 KIG SourceAdapter 都执行该门禁。
- impact preview 返回 Chunk、Embedding、Citation、KIG derived dependency、活动任务及是否退出检索/保留原文件。

## 验收证据

- 重建解析、切片两个阶段运行时，指定文档旧 FTS 每阶段仍可命中。
- 单事务切换后 `active_index_revision + 1`，内容 hash 和原文件不变。
- 强制 parser 首次失败并耗尽重试后，文档仍 indexed、旧 Chunk ID 不变、旧 FTS 可命中。
- archive 后指定文档召回为 0，Chunk 和原文件仍在；restore 后恢复命中。
- Dense 不可用时 `retrieval_mode=fts`；既有测试覆盖 FTS 无词时的 vector fallback。
- Knowledge/KIG 相关回归：193 passed，1 warning。

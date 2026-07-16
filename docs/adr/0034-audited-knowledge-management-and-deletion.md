# ADR-0034：可审计的知识管理、重建与删除闭环

- 状态：Accepted
- 日期：2026-07-16
- 阶段：知识库 F.7
- 关联版本：schema 33

## 背景

F.6 已把 indexed 文档接入对话，但项目只有导入列表和取消入口。数据库预留了 `delete_pending/delete_failed`，
实际没有 DELETE API、物理清理 worker、标签编辑或重建入口。只从数据库删除一行既无法保证文件清理，也会让
运行中的导入任务与删除竞争。

## 决策

1. DELETE 接口不假装同步完成，返回 202 和独立 deletion run。请求事务先把文档切到 `delete_pending`、清 FTS、
   撤销 queued/recovery 导入任务并向 running 任务发出协作取消；从该事务提交起检索与引用来源均不可用。
2. 删除 worker 只处理没有 running/cancel_requested 导入任务的文档。它以受约束 storage/artifact key 定位应用
   内文件，幂等删除原文和解析 artifact，再在一个数据库事务中清 FTS、chunk、artifact 元数据和 document。
3. 文件或数据库失败只记录稳定错误码，不记录底层异常、文件名、路径或正文；document 进入 `delete_failed`，
   deletion run 进入 failed，用户明确重试后才回到队列。worker 中断的陈旧 running run 也按失败处理。
4. deletion run/event 不以 FK 指向 document，物理删除后保留最小审计；citation 同样保留定位快照，但真实来源
   校验返回 410，快照不能冒充正文。
5. 重建索引先撤销当前可检索状态、清 FTS/chunk，再创建 trigger=reindex 的独立 import run，复用原文哈希校验、
   解析、切片和索引流水线。失败/取消不会恢复旧索引。
6. 标签由受限 PATCH API 管理，最多 10 项、每项 40 字符，去空白并按 casefold 去重。列表支持转义后的字面文件名
   搜索及 collection/状态筛选；同名不同内容继续以完整 SHA-256 和短指纹区分。
7. 管理界面的删除确认明确“只清应用内副本，外部原文件/备份不受影响”。检索审计 UI 只读取短查询指纹、计数、
   token 和关联可用性，不暴露查询正文。
8. schema 33 为 retrieval 完成/失败状态增加 finished_at 更新触发器，避免后续产生无完成时间的终态记录。

## Review 建议处理

- 采纳删除状态机、标签编辑和只读检索审计。
- 保持严格 `[资料:Kx]` 格式；宽松识别 `[K1]` 或 `[1]` 容易把普通文本误当来源。
- 消息删除后的审计以 `session_available=false` 展示；长期归档/保留策略延期，不在 F.7 混入新的生命周期系统。
- review 声称已有 DELETE 端点、聊天显式 `BEGIN IMMEDIATE` 与 `bc4398f` 源码不符，不作为实现前提。

## 验证

- 覆盖 schema 隐私、标签边界、特殊字符文件名、同名不同内容、重建退出召回/恢复、完整残留清理、I/O 失败重试、
  queued 导入取消、引用快照 410、API 筛选/标签/审计/删除。
- 后端 305 项、前端 24 项、生产构建与 Electron 语法检查全部通过后才勾选 F.7。

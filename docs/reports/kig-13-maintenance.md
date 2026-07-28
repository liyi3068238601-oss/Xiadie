# KIG.13 非破坏性维护

- Schema 80 新增 MaintenanceCandidate 和检索反馈；worker 与聊天隔离，支持 off/daily/weekly。
- 确定性检查覆盖文件 hash 重复、缺 metadata、rebuild failure、stale document、orphan chunk 与失效 derived dependency。
- 语义重复、旧版本、entity merge/split 仅允许 `llm_proposal`。
- 每项候选固定 `requires_confirmation=1`；确认候选不会执行删除，owner 删除入口保持唯一。
- 扫描批次硬上限 100；异常只记录日志，不阻塞陪伴聊天。

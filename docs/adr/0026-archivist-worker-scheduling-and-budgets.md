# ADR-0026：Archivist worker、懒调度与有限预算

- 状态：Accepted
- 日期：2026-07-16
- 阶段：记忆系统 E.4
- 关联版本：schema 25

## 背景

E.3 已提供单个 Fragment 的确定性、原子生命周期转换，但没有后台调度。直接在聊天请求中扫描会增加延迟，
无界扫描会长期占用 SQLite 写锁，进程中断还会让任务状态不可解释。项目已有 Saga Consolidator 的可靠
worker 模式，应复用其任务状态机，同时保持 Fragment 与 Episode/Saga 慢生命周期的阶段边界。

## 决策

1. schema 25 新增 Fragment 的 `last_archivist_evaluated_at`、`archivist_runs` 和
   `archivist_run_events`。事件仅保存状态、原因、预算和计数，不保存 Fragment ID、正文、标签或模型输出。
2. worker 使用幂等 enqueue、`BEGIN IMMEDIATE` 串行认领、最多三次 5/10 分钟指数退避、五分钟陈旧
   恢复、协作取消和优雅停机。停止时 running 进入 `recovery_pending`，工作线程在下一条前检查状态。
3. 启动和空闲时仅在 `last_archivist_run` 距今至少 20 小时后入队；时间 bucket 保证同一窗口幂等，错过的
   窗口不补跑。只有 completed/skipped 才推进成功时间。
4. 默认单轮最多扫描 50 条、转换 10 条、运行 2000ms、模型调用 0 次，数据库硬上限为
   200/100/30000ms/20。预算用尽正常结束，剩余候选等待下一窗口。
5. 候选仅包含已到 active 14 天或 cooling 额外 30 天评估点的 enabled Fragment，先按最近评估时间轮转，
   同轮中 cooling 优先、再按最久未召回排序。评估后单独记录时间，避免受保护的旧记录长期占满预算。
   每条调用 E.3 的独立短事务；revision 冲突计数后跳过，其他失败触发 run 重试。
6. E.4 不调用模型，也不改变 Episode/Saga。预留的模型预算快照只用于未来可选能力；慢生命周期在 E.5
   另行设计，不预埋空的 `slow_lifecycle` trigger。
7. `/api/archivist/runs` 暴露手动入队、列表、带事件详情和取消，供 E.6 管理界面直接消费。

## 后果

- 聊天路径不执行也不等待维护；worker 失败不会阻断对话。
- 单个 Fragment 的状态、FTS 和生命周期事件仍由 E.3 原子提交，run 只汇总结果，不形成第二套状态真相。
- 已成功转换的独立 Fragment 不因后续另一条失败而反向回滚；失败任务重试时状态机会重新评估最新 revision。
- Episode/Saga 生命周期保持未变，避免 E.4 越界提前实现 E.5。

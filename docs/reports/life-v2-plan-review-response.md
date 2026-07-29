# LIFE v2 v0.3 plan-review 响应

- 日期：2026-07-29
- 审查输入：`E:\Xiadie\review\life-v2-plan-review\life-v2-plan-review.html`
- 审查结论：有条件通过，原始发现为 0 P0、2 P1、3 P2
- 响应结论：全部采纳并在计划合同层关闭；当前未解决 0 P0 / 0 P1 / 0 P2
- 冻结计划文件 SHA-256：`cd1a688d0d22d01b4aef79aca03447c63543d67d1405fc1432eda4c4ef932e45`
- 当前观察 HEAD：`c8877f5ec6d00959fbcebc8a111a21cc8fb3671f`；工作树仍含未提交计划文档，因此它不是最终 ConstructionBaseline

## 采纳结果

| Finding | 决定 | 落地合同 |
|---|---|---|
| P1-1 ShortMemo `shadow → active` 缺少过渡语义 | 采纳 | 切换只影响下一条被接收的用户消息；活动请求保留旧快照；首次 Active 请求先清理/校验；Shadow 候选不回放，按 `rollout_epoch` 分界 |
| P1-2 会话级联删除事件语义不完整 | 采纳方案 C | SQLite FK 级联物理删除正文是唯一权威；会话/消息删除不伪造 `source_deleted`；事件不保存来源映射，既有无正文事件按保留策略清理 |
| P2-1 `metadata_json` 无正式 schema | 提前采纳 | 固定 256 bytes 上限和四键白名单，未知键、嵌套值、自由文本及敏感元数据拒绝写入 |
| P2-2 容量拒绝没有合法 `memo_id` | 提前采纳 | Shadow/拒绝仅进入无正文聚合 diagnostics，不写 `short_memo_events`，不制造伪 ID |
| P2-3 WorldBook r1 缓存隔离未定义 | 提前采纳 | r1 使用独立 loader/cache，以 manifest hash、来源门版本和 rollout snapshot 分 namespace，不复用旧 Lore `lru_cache` |

## 复检

- `git diff --check`：通过。
- Review 清单：10/10 已在冻结计划中勾选。
- 运行时代码、数据库和 Schema：未修改，仍为 Schema 81。
- 后端/前端测试：未运行；本次仅修改文档合同，不把历史测试数冒充当前结果。

## 下一步

提交冻结计划后，记录干净 commit SHA、依赖锁与实际基线测试结果，完成 LIFE2.0；随后只进入 LIFE2.1 人格评测基线，不提前施工 Persona/WorldBook/Schema 82。

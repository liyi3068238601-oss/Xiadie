# KIG.1 统一来源治理施工报告

- 日期：2026-07-27
- Schema：72
- 结论：通过；KIG.2 可开工

## 实现边界

- `backend/app/kig_sources.py` 提供 typed `SourceRef` 与 `SourceAdapterRegistry`。
- 7 个 adapter 只读原权威系统：KnowledgeDocument、KnowledgeChunk、Message、MemoryFragment、LifeEvent、ToolRun、LoreSection。
- Schema 72 只新增 `derived_dependencies`。表内仅保存派生对象到来源的 identity、revision/hash、status snapshot、privacy scope、locator 与检查时间，不保存正文、摘要、属性或向量。
- 未建立 `source_refs` 通用来源表，未迁移任何原系统正文或生命周期。
- `GET /api/kig/sources/{source_kind}/{source_id}` 返回无正文的权威元数据；`POST /api/kig/sources/validate` 对全部字段做精确回查。

## 状态传播

| 当前事实 | 派生依赖状态 |
|---|---|
| revision/hash/privacy/locator 改变 | `stale` |
| 权威行不存在 | `missing` |
| 来源撤销或 tombstone | `revoked` |
| 来源关闭、未索引或未完成 | `inaccessible` |
| adapter/检查异常 | `unverified` |

`sweep_dependencies` 每批限制 1～500 条。检查失败只降级依赖状态，不删除或改写权威来源。

## 验收证据

- `backend/tests/test_kig1_sources.py`：5 passed。
- KIG.0/KIG.1、CDS.9/CDS.10、Knowledge Schema、SelfTimeline 相关回归：367 passed，1 warning。
- 后端全量：2434 passed，1 warning，485.12 秒。
- 唯一 warning 为既有 Starlette `TestClient`/httpx 弃用提醒。
- 7 类来源均执行真实定位器校验；测试中的伪造 locator 通过率为 0%。
- 数据库结构断言不存在通用 `source_refs` 表，`derived_dependencies` 不含 `content/body/summary/source_body` 字段。

## 历史基线兼容

KIG.0 runner 固定只应用迁移 1～71，因此后续迁移不会篡改 KIG.0 的 predecessor Schema、能力缺口与报告。CDS.9/CDS.10 动态“当前 Schema”报告已同步更新为 72，其原阶段边界不变。

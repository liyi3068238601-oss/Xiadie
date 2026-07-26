# CDS.11 EAP 适配与 LIFE/KIG 契约审计

> 日期：2026-07-26  
> 阶段：CDS.11 施工完成，等待独立 review  
> Schema：62（无迁移）

## 1. Review 处理

| 建议 | 处理 | 依据 |
|---|---|---|
| Oracle v3 三项直接负向断言 | 采纳 | 已补 `episode_turning_points_duplicate`、`episode_causal_chain_missing_selected`、`episode_skip_without_reason` |
| CDS.6 SSE 修复提交溯源 | 采纳 | CDS.6 施工记录已交叉引用 `c996585` |
| CDS.11 使用 `Protocol`/`TypedDict`，不造领域实现 | 采纳 | 新增纯契约模块；无领域表、worker 或消费者 |
| EAP adapter 并发与唯一写者验证 | 采纳 | 32 路并发读取及六张 EAP 表逐行零变化 |
| 不占用 Schema 63 | 采纳 | Schema 保持 62 |
| 为 SQLite 3.40.1 改造迁移 | 不采纳 | review 使用另一套 Python；当前仓库 `.venv` 为 Python 3.12.13 / SQLite 3.50.4，专项及全量测试可运行 |
| CDS.12 结构探测/参数边界建议 | 推迟 | 属于下一阶段，CDS.11 不提前实现 |

## 2. 实际产物

- `backend/app/specialty_contracts.py` 冻结 body-free revision、候选、结果与 provider 接口。
- `backend/app/proactive/decision_run_adapter.py` 提供 EAP 对 CDS DecisionRun 的只读诊断投影。
- `backend/tests/test_cds11_specialty_contracts.py` 验证所有权、来源绑定、幂等、离线退出、并发和零领域写入。
- ADR-0057 固定单写者、零迁移和后续升级边界。

## 3. 所有权结论

- EAP 仍是 `proactive_candidates`、授权、强度、投递、反馈与 ContactEpisode 状态机的唯一写入者。
- CDS 只拥有共享 DecisionRun/诊断运行时，不持有 EAP 候选 ID，也没有发送 API。
- LIFE 尚未施工；其对象在共享边界只能是 revision/hash 来源。
- KIG 尚未施工；知识/PWM 只能在 KIG 自身校验后提供有限候选。
- 未回复压力继续由 `proactive/episodes.py`、`decision.py`、`delivery.py` 和 `feedback.py` 的程序逻辑计算，不交给 LLM。

## 4. 验证结果

- CDS.11 + CDS.10 P2 + LIFE adapter：`81 passed`。
- 32 个并发只读结果一致，六张 EAP 领域表写入变化为 0。
- Python `3.12.13`，SQLite `3.50.4`。
- 后端全量：`2290 passed, 1 warning`；唯一警告为既有 Starlette `httpx2` 迁移提示。

## 5. 未做事项

- 未注册 LIFE/KIG DecisionKind，未调用真实 Provider。
- 未实现 Narrative Planner、LifeEvent、Diary、ImportantDate、SourceRef 或 PWM。
- 未接入聊天、主动投递、UI 或外部渠道。
- 未晋级任何现有 Shadow 决策器。

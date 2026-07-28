# KIG.5 Query Planner 与多库路由施工报告

- 日期：2026-07-27
- Schema：74（本阶段无迁移）
- 协议：`query-plan-input-v1` / `query-plan-result-v1`
- 模式：CDS Shadow、proposal-only

## 已交付

1. 在 CDS 共享 DecisionRun、CandidateEnvelope、结构化输出校验与审计账本上注册 `kig_query_planner`，没有复制通用模型运行时。
2. 单文档和显式来源查询确定性旁路模型；清晰的时间、版本、实体、原话、冲突和跨库请求同样由规则规划。
3. 来源白名单固定为 Knowledge、Memory、History、Life、Task、Lore；每个计划最多 4 个子查询，每项最多 160 字符。
4. 用户关闭来源后，规则过滤与结果 validator 均拒绝放行；模型不能发明候选来源或扩张显式来源。
5. 仅模糊指代进入经授权的模型路径；模型结果保持 Shadow proposal，模型失败、未授权或重复运行均安全回退且不执行检索。

## 验收证据

- `backend/tests/test_kig5_query_planner.py`：13 项，覆盖显式旁路、六源路由、五类需求、关闭来源、注入、模糊指代、CDS Shadow、重复运行幂等与 Schema 不变。
- KIG.0～KIG.5、CDS、Knowledge Recall/Search 与 Context 相关回归：`881 passed, 1 warning`。
- 最终代码已配置 `deepseek-v4-flash` 两轮共 12 条纯合成 Shadow 样例：2 条明显注入由程序旁路拒绝，10 条进入模型；严格结构结果 3 条，安全回退 7 条；越权来源 0，Shadow 应用放行 0，安全收口率 100%。

## 已知限制与结论

最终实现对应样本中的模型调用一次成功率为 3/10（30%），且两轮结果有明显波动。因此本阶段确认模型故障不会扩大来源、不会阻塞既有检索且不会写状态，但当前结果不能作为 Query Planner 晋级 Advisory/Active 或模型质量达标的证据。确定性清晰查询不产生模型调用，模糊查询在服务不可用或输出不合规时保守退回 Knowledge 或空计划。

回滚只需移除 `kig_query_planner` 注册与调用方；本阶段无数据迁移、无来源正文复制、无索引变化。

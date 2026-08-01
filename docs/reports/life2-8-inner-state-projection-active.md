# LIFE2.8 InnerStateProjection Active 施工报告

- 日期：2026-08-01
- 分支：`agent/life-v2-specialty`
- 施工基线：`bdf683f`
- 结果提交：本报告所在提交
- Schema：82（未新增迁移）
- 状态：施工完成，等待用户 Review

## 1. 结论

InnerStateProjection 已从 Shadow 独立切换为 Active。全新数据库默认 Active；已有数据库通过内部 setter 发布。当前真实数据库从 `shadow` 切至 `active`，重复调用保持幂等。

Projection 仍只是当轮请求内的只读投影，没有数据库实体、缓存或反向写回。ShortMemo 保持独立 Active，Persona profile 仍为已认证 v2.2，WorldBook r1 仍未进入生产。

## 2. LIFE2.7 Review 处理

- 接受“计划书历史 seed 描述可能误导”的 P2：主计划已明确该值是 LIFE2.4 历史冻结值，并注明 LIFE2.7 的新安装 seed 已改为 Active。
- 暂不采纳“把 recall 失效来源清理拆成独立函数”的 P2：当前删除是 ShortMemo 的 fail-closed 生命周期合同，重构不属于 Projection 发布范围；后续若实施，需作为独立 ShortMemo 主题施工。
- 跨 epoch 诊断、8% 预算与敏感正文最小化作为观察项保留，不改变本阶段实现。

## 3. 变更范围

- `backend/app/db.py`：Schema 82 面向全新数据库的 Projection seed 改为 Active。
- `backend/app/inner_state_projection.py`：设置缺失默认 Active、非法值 Off；新增内部幂等 setter。
- `backend/tests/test_life2_5_inner_state_projection.py`：历史合同适配新安装默认值。
- `backend/tests/test_life2_8_inner_state_projection_active.py`：新增发布、回滚、请求边界与聊天降级合同。
- LIFE 主计划与 Persona v2.3 实施计划记录实际施工状态。

`main.py` 和 `persona_v2.py` 无需修改：既有实现已经在请求内只读取一次 rollout，并区分 Shadow comparison candidate 与 Active production prompt。

## 4. 当前运行状态

```text
schema_version=82
database_tables=165
projection_tables=0
life.short_memo.rollout_mode=active
life.short_memo.rollout_epoch=1
life.inner_state_projection.rollout_mode=active
```

切换使用 `inner_state_projection.set_rollout_mode("active")`，没有直接修改 SQLite。切换前后表数量均为 165，未产生 Projection 表。

## 5. 验证结果

首次 LIFE2.5 + LIFE2.8 专项验证：

```text
11 passed, 1 StarletteDeprecationWarning in 13.21s
```

随后扩大到 Persona v2.2、Projection、ShortMemo Active 和 LIFE2.8 聊天集成组合：

```text
23 passed, 1 StarletteDeprecationWarning in 12.78s
```

覆盖：

- 全新 Schema 82 默认 Active，设置缺失默认 Active，非法值回落 Off；
- setter 枚举、幂等以及与 ShortMemo 独立的 Shadow/Off 回滚；
- 同一权威快照的确定性与来源删除后零残留 ID；
- guardedness 对 `gently_curious`、`offer_help` 的约束；
- Shadow 只改变 comparison candidate，Active 才进入生产 prompt；
- 构建中切换 rollout 不改变当前请求已捕获值；
- 构建异常时普通聊天继续完成且 Persona 收到 `projection=None`；
- 投影序列化不包含正文、summary、title、inner_monologue 或未知字段；
- ShortMemo Active 与 Projection Active 不共享发布门或形成写入循环。

`git diff --check` 通过。

## 6. 未运行与剩余风险

- 未运行后端全量测试：本阶段没有改变 Schema、公共装配顺序、SSE、Provider 或冻结协议；按计划使用风险相关定向组合。后端全量将在 LIFE2.10 首次运行。
- 未调用真实 DeepSeek：本阶段发布的是确定性投影与既有编译消费路径，不是 Persona 模型认证阶段。
- 未在用户真实会话中评估语气。追问、主动帮助、工作模式简洁性与内部状态不外露需要人工 Review。
- Projection 没有 epoch；当前合同只要求请求内单次读取与独立幂等发布，不提供历史 rollout 查询。

## 7. 回滚

```powershell
backend\.venv\Scripts\python.exe -c "from app import inner_state_projection as p; print(p.set_rollout_mode('shadow'))"
```

回滚只影响下一轮 Projection 是否进入 Persona，不删除数据，也不改变 ShortMemo、Persona profile 或 WorldBook 状态。

## 8. Review 重点

1. 普通闲聊分享一件事，检查关系允许时的适度追问和主动帮助是否自然，而非每轮固定出现。
2. 在防御或高度戒备关系边界下，确认不会出现 `gently_curious`/`offer_help` 对应的越界表达。
3. 切换到工作模式执行具体任务，确认回答仍结论优先、简洁，不因 Projection 变得撒娇、诗化或偏题。
4. 检查回答不复述 `affect_band`、`relationship_boundary`、内部 ID、hash 或“内心状态”。
5. 可查看日志确认没有 Projection 正文、持久化对象或反向写入；不需要重新运行后端全量测试。

Review 通过前不开始 LIFE2.9。

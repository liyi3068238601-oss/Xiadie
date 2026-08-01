# LIFE2.7 ShortMemo Active 施工报告

- 日期：2026-08-01
- 分支：`agent/life-v2-specialty`
- 施工基线：`a93df0e`
- 结果提交：本报告所在提交
- Schema：82（未新增迁移）
- 状态：施工完成，等待用户 Review

## 1. 结论

ShortMemo 已从 Shadow 独立切换为 Active。全新数据库默认 Active；已有数据库不重放 Schema 82，通过内部 setter 发布。当前真实数据库从 `(shadow, epoch=0)` 切至 `(active, epoch=1)`，第二次幂等调用没有继续增加 epoch，且切换本身没有产生 memo 或事件。

Persona、WorldBook、InnerStateProjection、CIE、CTX、KIG、CDS 和 EAP 均未随本阶段改变发布状态或协议。

## 2. 变更范围

- `backend/app/db.py`：Schema 82 的全新数据库 ShortMemo seed 改为 Active。
- `backend/app/short_memo.py`：设置缺失时的产品默认改为 Active；非法值继续回落 Off。
- `backend/tests/test_life2_4_short_memo.py`：澄清历史 Shadow fixture 测试名称。
- `backend/tests/test_life2_7_short_memo_active.py`：新增 LIFE2.7 发布、快照和聊天集成合同。
- LIFE 主计划和 Persona v2.3 后续计划更新施工状态。

## 3. 当前运行状态

```text
life.short_memo.enabled=1
life.short_memo.rollout_mode=active
life.short_memo.rollout_epoch=1
life.short_memo.remote_extraction_enabled=0
short_memos=0
short_memo_events=0
schema_version=82
```

发布切换使用 `short_memo.set_rollout_mode("active")`，没有直接修改 SQLite。回滚同样必须使用该内部 setter；回滚不会删除已有正文。

## 4. 验证结果

定向后端组合：

```text
34 passed, 1 StarletteDeprecationWarning
```

覆盖：ShortMemo 原合同、LIFE2.7 新合同、LIFE 设置/API、ContextAssembler 和 LIFE2.6 历史验收。

前端合同：

```text
79 passed
```

此外，新聊天集成测试确认：

- 普通持久会话中的近期安排会静默创建一条来源绑定 memo；
- 后续相关聊天能在 prompt 中收到该 memo；
- 临时聊天不创建第二条 memo；
- 产品 enabled 关闭后停止写入和召回，但内部 rollout/epoch 不变；
- 请求中途切换只影响下一轮，已捕获 snapshot 不混用新状态；
- Shadow、Off、秘密值、容量、远端否决/失败、删除、清空和来源级联旧合同继续通过。

## 5. 未运行与剩余观察

- 未运行后端全量测试：本阶段没有改变表结构、聊天流式协议或公共装配顺序，按正式计划使用风险相关定向测试。
- 未调用真实 DeepSeek：ShortMemo 默认本地确定性提取，远端 validator 默认关闭；模型调用不是本阶段发布前提。
- 未在用户真实会话中留下测试 memo。设置页的实际展示、自然措辞和真实日常召回建议作为本阶段人工 Review 重点。

## 6. 回滚

```powershell
backend\.venv\Scripts\python.exe -c "from app import short_memo; print(short_memo.set_rollout_mode('shadow').public())"
```

回滚后下一轮停止正式写入和召回，已有记录保留到 TTL、来源删除、逐条删除或隐私清空。ShortMemo 的回滚不得改变 Persona、Projection 或 WorldBook 状态。

## 7. Review 重点

1. 在普通会话输入一个不敏感的近期安排，确认设置页仅出现一条正文最小化、来源和到期时间正确的 memo。
2. 下一轮自然追问相关事项，检查回答是否使用信息但不暴露内部字段、不宣称永久记忆。
3. 用临时聊天重复测试，确认零写入。
4. 关闭“短期备忘”产品开关后确认停止写入/召回，再打开确认恢复；rollout 应始终保持 Active。
5. 删除来源会话、逐条删除和清空后确认没有可召回残留。

Review 通过前不开始 LIFE2.8。

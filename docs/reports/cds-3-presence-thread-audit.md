# CDS.3 PresenceAndThreadObserver 兼容校准与 review 处置

> 日期：2026-07-22  
> 基线：`0b07f00`（CDS.2）  
> 状态：CDS.3 strict review 已通过（0 P0/P1）；两项 P2 已采纳并完成回归，已进入 CDS.4。

## 1. CDS.2 strict review 处置

外部 review 结论为 0 P0、0 P1、2 P2，4/4 完成门通过，允许进入 CDS.3。

| 建议 | 处置 | 理由 |
|---|---|---|
| P2-1：静态 `model_binding_revision=unbound-v1` 与动态 binding 语义不一致 | 采纳 | 注册定义改用明确的 `cognition-binding-v1` 策略版本；run 仍记录具体 binding hash |
| P2-2：structured probe 复用 2 秒执行超时 | 采纳 | 认证 probe 与 decision timeout 解耦，fast/reasoning/creative 分别为 5/30/15 秒；纯合成测试锁定，不消耗真实 Provider |
| 熔断重启恢复建议 | 保持现实现状 | 每条 open breaker 都有 `open_until`，冷却后首次访问已原子转 half-open，不存在永久 open；启动强制重置反而会破坏有效冷却 |
| budget event 清理建议 | 采纳 | 启动时取消超过 1 小时的陈旧 authorized reservation，并清理超过 30 天的终态无正文事件 |

## 2. 冻结路径审计

- 用户消息入库后仍由 `proactive.presence.detect_presence_signals` 和 `update_presence` 写 v2。
- 用户回归、过期、来源 snapshot、主动候选和投递仍由 EAP reducer/orchestrator/ledger 管理。
- CDS.3 没有修改 Schema、v2 正则、默认期限、reducer、候选或投递代码。
- 新 decision kind 只注册协议，主聊天不调用 `execute_registered_decision`。

## 3. Shadow 协议与评测

- 专属 `presence_thread_observer` Schema 输出 Presence、expect-return、closure、bounded thread、activity、follow-up 和 response need。
- review 后固定集扩为 900 轮，覆盖睡眠、测试/吃饭/洗澡离开与返回、混合离开、已有 thread 下的强信号、元讨论、结束、DND、忙碌、普通聊天和未知沉默。
- Shadow 精确匹配 100%，有效 message ID 绑定 100%；冻结 EAP v2 fallback 对新语义的精确匹配为 18%，差异主要来自新字段、meta context、有界多 thread 和不虚构返回承诺。
- 本结果是离线协议/参考策略校准，不是实际 LLM 质量声明；真实 binding 仍需 CDS.2 认证与独立 Shadow 才能晋级。

完成门：

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| “晚安”误判预计返回率 | 0% | 0 |
| “去测试一下”开放话题识别率 | 100% | ≥95% |
| 未知沉默被写为拒绝率 | 0% | 0 |

## 4. 语义缺口处置

未回写冻结 v2。`docs/reports/cds-3-presence-v3-proposal.md` 仅提出三态 expect-return、meta 优先、有界 thread code 和 message/TTL 绑定的 v3 方向及迁移影响，不代表 EAP 已接受或实施。

## 5. CDS.3 strict review 处置

外部 strict review 确认 0 P0、0 P1，允许进入 CDS.4。两项 P2 均采纳：

1. 已有 `current_open_threads` 不再抢在 sleep/end/DND/busy/extended 与明确离开信号之前；强信号获胜，但除明确结束外保留有界 thread，禁止追问。
2. fixture 新增 `meal_return`、`shower_return`、混合离开和已有 thread 下睡眠场景；返回语句使用完成态“回来了”与计划态“回来”区分。

EAP v2、reducer、候选和投递仍未修改；review 建议没有扩大 CDS 的写权限。

CDS.3 review 后专项回归：900/900 Shadow 精确匹配，完成门仍为 0%/100%/0%；与 CDS.4 合并后的全量施工自验记录在 CDS.4 审计报告和总计划中。

# LIFE v1 冻结记录

- 冻结日期：2026-07-27
- 最终 Schema：71
- 独立 Review：通过，0 P0、0 P1、2 P2、2 个设计观察
- 后端全量回归：`2423 passed, 1 warning`
- 前端与桌面基线：50 tests；Vite 190 modules；Electron lifecycle contract 3 项；Windows NSIS 生命周期验收通过
- KIG 首个可用迁移号：72

## Review 处置

| 项目 | 决定 | 冻结处置 |
|---|---|---|
| P2-1 日记敏感内容弱关键字匹配 | 采纳 | 增加信用卡/银行卡号、身份证号、手机号、邮箱等格式识别并扩充敏感关键字；创建与修订共用同一分类器 |
| P2-2 Provider 一致性未进入晋级门 | 采纳 | 当 Provider 数量达到 2 时，必须提交有效成对一致性报告且 `agreement_rate >= 0.85`；缺失或不足均阻止晋级 |
| OBS-1 persona/diary 目标可自主激活 | 保留现状 | 这是 LIFE 自主生活的产品设计：persona 或 diary reflection 来源且置信度至少 0.85 可激活；临时用户建议仍不是授权来源 |
| OBS-2 日程时区只在计算时验证 | 采纳 | `create_schedule()` 在任何持久化前使用 `ZoneInfo` 验证 IANA 时区，无效值 fail closed |

针对性回归覆盖上述三项代码收口，共 `30 passed, 1 warning`；随后完整后端测试通过，共 `2423 passed, 1 warning`。唯一警告仍是 Starlette TestClient 的既有弃用提示，不是 LIFE 回归。

## 冻结兼容矩阵

| 消费/提供方向 | 冻结契约 | LIFE v1 行为 | 兼容结论 |
|---|---|---|---|
| CDS → LIFE | `specialty-adapter-contract-v1`、`cognitive-decision-v1` | LIFE 只注册 6 类领域决策，复用 CDS Registry/DecisionRun/validator；全部保持 Shadow | 兼容，不修改 CDS 冻结表或通用运行时 |
| LIFE → EAP | `life-adapter-v1` → `eap-decision-run-adapter-v1` | 只提供 source kind/id/revision/hash 绑定的生活种子；EAP 独占候选、授权、投递与反馈 | 兼容，无第二发送器或授权旁路 |
| LIFE → CTX | `context-adapter-v1` | SelfTimeline 仅在明确生活回忆意图下提供有来源、受预算约束的只读区块 | 兼容，不改变 CTX 总预算所有权 |
| LIFE → KIG | KIG 目标 `source-ref-v1` | KIG 只读 LIFE 权威事件/状态/日程 revision/hash；删除与撤销由 LIFE 所有并向派生层传播 | KIG 可在合并基线后施工，不得反向改写 LIFE |

## KIG 开工门

LIFE v1 的代码、Schema 和 adapter 已冻结。KIG 计划与只读审计现在可以继续；任何迁移或生产写路径必须等待 LIFE PR 合入 `main`，然后把该不可变 merge commit 写入 KIG.0 ConstructionBaseline。旧专项分支不得作为 KIG predecessor。

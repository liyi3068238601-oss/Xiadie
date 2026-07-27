# LIFE.0 ConstructionBaseline 与实现差距报告

- predecessor：PR #2 merge `0d7a2d08dc07f123d016da26da117fa58f9a48a1`
- 基线：Schema 63；后端 `2304 passed, 1 warning`；前端 `47 passed`；Vite 189 modules
- 固定场景：60 条纯合成场景；fixture SHA-256 `8e0cf6e5a18e0ebe95bb2778953f20490f6e36aea01eb8bea1ca3b95962e7e7e`；不调用真实 Provider

## 当前能力矩阵

| 能力 | 状态 | 唯一所有者 |
|---|---|---|
| `affect_relationship` | implemented | EAP/Affect |
| `context_assembler` | implemented | CTX |
| `memory_episode_saga` | implemented | MEM |
| `life_proactive_seed_adapter` | partial | EAP |
| `life_clock_self_state` | missing | LIFE |
| `life_event_ledger` | missing | LIFE |
| `daily_schedule_goal` | missing | LIFE |
| `important_date` | missing | LIFE |
| `diary_continuity` | missing | LIFE |
| `self_timeline` | missing | LIFE |

## Affect / Relationship 时间基线

| 小时 | contact_need | guardedness | valence | arousal | immersion | bond | trust |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.056774 | 0.625000 | 0.050000 | -0.120000 | 0.000000 | 0.120000 | 0.250000 |
| 8 | 0.107526 | 0.625000 | 0.050000 | -0.120000 | 0.000000 | 0.120000 | 0.250000 |
| 24 | 0.234945 | 0.625000 | 0.050000 | -0.120000 | 0.000000 | 0.120000 | 0.250000 |
| 72 | 0.737965 | 0.625750 | 0.049705 | -0.118000 | 0.000000 | 0.120000 | 0.250000 |
| 168 | 1.000000 | 0.624250 | 0.048911 | -0.118000 | 0.000000 | 0.120000 | 0.250000 |

## 审计结论

- 当前没有 LifeClock、SelfState、LifeEvent、DailySchedule、PersonalGoal、ImportantDate、Diary 或 SelfTimeline 领域实现。
- 现有 `life_proactive_seeds` 只是 EAP 拥有的候选入口，不是 LIFE 事实表，也不具备发送权。
- 当前 lifespan 已有十类 worker；LIFE 必须复用单一生命周期编排，不创建主动发送器、第二套情绪/关系或第二套记忆。
- 60 场景中 45 条 LIFE 领域能力尚缺失；15 条决策安全场景仅有 CDS 邻接门禁，不能当作 LIFE 已实现。
- 离线连续性定义为下次启动时的有界模拟补算；应用完全退出期间不调用 Provider、不访问网络、不执行工具、不投递消息。
- 参考项目只作产品理念分析；未导入或复制其代码、Prompt 或资源。

## 回滚

LIFE.0 只新增测试、合成 fixture、ADR 与报告，不新增迁移或生产写路径；回滚提交不会影响用户数据。

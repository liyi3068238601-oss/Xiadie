# LIFE.3 LifeClock / SelfState 验收报告

- 日期：2026-07-26
- Schema：65
- 算法：`life-state-reducer-v1`
- 租约 TTL：30 秒，可 heartbeat，可过期接管

## 连续状态

生活状态按不超过 5 分钟的小步连续推进，不按日期随机重置。energy、focus、rest need 与 social openness 均限制在 0～1；活动有惯性与 45 分钟最小持续时间。相同状态、时间、时区和调制输入产生完全相同结果。

现有 affect/relationship 只通过只读快照调制 LIFE；读取时不推进、不写入，LIFE 不拥有 bond/trust。普通聊天保持原响应路径，也不会清除生活状态。

## 单 materializer 与时间安全

同一 SQLite 数据库的单例租约包含 process instance、boot session、token、acquired/expires/heartbeat。未过期租约拒绝第二实例；崩溃残留租约到期后可接管。物化必须持有有效 token。

Windows 睡眠、休眠与重启体现为可靠 wall time 的正向跨度；倒时钟超过 5 分钟或时区改变时不反向/跨区推进，而是记录 `conservative_hold`。Windows frozen Python 优先使用 ZoneInfo；缺少 IANA tzdata 时，UTC 与中国标准时使用固定兼容映射。

## 验证

- LIFE.3 专项：14 passed。
- LIFE.2/API/CDS 邻接回归：合计 80 passed，1 warning。
- 1/8/24/72/168 小时：确定、有限、有界。
- 两实例同时物化：租约拒绝率 100%。

阶段独立 Review 按用户要求留待 LIFE 总体 Review。

# LIFE.4 离线世界续演验收报告

- 日期：2026-07-26
- Schema：66
- 默认模式：`continuous_simulated`
- 候选上限：16
- 模型调用硬上限：2；当前确定性实现为 0

## 实际语义

应用退出时只保存 snapshot；完全退出期间没有 LIFE worker、模型、网络、工具或消息投递。下次启动持有数据库 materializer lease 后才创建 CatchUpRequest 并有界补算，因此“世界继续”是启动时模拟，不是后台真实执行。

CatchUpRequest 冻结 catchup ID、区间、时区、日程 revision、状态 revision、算法版本、确定性 seed 与 materialization revision。20 分钟、8 小时、3 天、30 天、180 天分别使用详细、日级、日级、周级、回归过渡策略。所有候选固定为 simulated；日期跨越由 revision-bound 输入进入 `important_date_crossing`。

## 幂等与控制

相同退出 snapshot 与 interval end 产生相同 seed/catchup ID/idempotency key；已 applied 的 request 直接返回，不再次推进状态或插入候选。paused/disabled 在创建 request 前跳过，倒时钟保守跳过。

lifespan 启动时认领并 heartbeat 租约，退出时记录 snapshot、取消 heartbeat 并释放租约。首启锁测试发现并修复了 LIFE 写事务内初始化 EAP 导致的自锁，EAP 只读投影现于 LIFE 写锁前解析。

## 验证

- LIFE.4 专项：14 passed。
- LIFE.3 + LIFE.4：28 passed，1 warning。
- 20 分钟、8 小时、3 天、30 天、180 天均能补算。
- 重复 materialization：0；离线工具/网络/delivery 写入：0。

阶段独立 Review 按用户要求留待 LIFE 总体 Review。

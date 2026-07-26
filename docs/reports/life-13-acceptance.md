# LIFE.13 长期模拟与总验收报告

日期：2026-07-27

状态：技术验收完成，等待 LIFE 总 Review；尚未冻结。

## 自动验收

| 门 | 结果 |
|---|---|
| 后端全量 | 2416 passed，1 个 TestClient 弃用警告 |
| 前端 | 50 passed；TypeScript 与 Vite production build 通过，190 modules |
| Electron | `main.js`/`preload.js` 语法通过；lifecycle contract 3 passed |
| 长期生活 | 180 个连续自然日均完整覆盖 0～1440 分钟，确定性重放一致 |
| 日记 | 30/30 条无完全重复，5 个开场变体且连续线索保留 |
| 重要日期 | 20 个 IANA 时区 × 5 个日期 = 100/100，本地午夜换算正确 |
| 来源混淆 | 5 个世界层 × 20 个来源 = 100/100；仅 3 个有证据的 performed 来源允许“确实完成” |
| 冻结后端 | health、local-only BGE-M3 与模型 SHA-256 通过 |
| Windows 安装版 | NSIS 临时安装、首启、关窗托盘保活、崩溃子进程清理、重启、卸载清理通过 |
| 休眠/唤醒 | Electron suspend/resume contract、LIFE 重启推进、resume guard API 与逾期投递保护通过 |

## 模型与 token

- 普通结构化调用默认最多 500 output token。
- 只有显式 Reasoner 认证可申请最多 2048 output token，调用层仍强制硬上限。
- 单次 CatchUp 最多 2 次模型调用；当前确定性 CatchUp 实际为 0。
- `deepseek-chat` 与 `deepseek-reasoner` 共同合法样本的一致率为 88.33%。
- 当前只有一个可调用 Provider，跨 Provider 报告状态为 `provider_count_insufficient`；所有 LIFE 决策保持 Shadow。

## 多年增长与压缩演练

按每日 1 份日程、每日最多 4 个重要 LifeEvent、每周最多 3 篇日记、每年 20 个用户确认日期/目标估算，五年权威数据仍处于 SQLite 可管理量级：日程约 1,825 份、重要事件最多 7,300 条、日记约 780 篇、日期/目标约 100 条。正文大小由现有字段上限继续约束，实际文件增长以用户内容为主，不以摘要静默替换原文。

`life-retention-v1` 的 dry-run 与 apply 演练均通过：过期失败/已物化候选、完成的 CatchUp、可释放旧退出快照和旧 runtime event 被压缩；最近 32 条 runtime event 与最新退出快照保留。LifeEvent、日记及全部修订、ImportantDate、用户确认 Goal 和它们的来源计数前后完全一致。日程或其他记录只要仍被日记、LifeEvent、共享 Episode 引用，就不进入压缩集合。导出先于用户请求的删除；恢复后重建 SelfTimeline 等投影，不覆盖权威 revision。

## Windows 构建

- 安装包：`遐蝶-Setup-0.1.0.exe`
- 大小：564,780,737 bytes
- SHA-256：`F5F61D0F22D59F7AA9873F581AADF93C267FEF5053575555C073470CCC42241A`
- 签名：未签名个人构建；不得视作正式分发产物。

仍待用户总体 Review 的门：生活连续性、日记与主动表达的人工自然度，以及确认 0 个未解决 P0/P1。完成前不勾选 LIFE v1 冻结，也不启动 KIG 迁移。

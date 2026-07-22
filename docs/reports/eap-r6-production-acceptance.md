# EAP.R6 生产路径总验收记录

- 日期：2026-07-22
- 状态：独立 strict review 已通过，EAP 专项正式冻结
- 范围：EAP.R0～R6；R6 施工当时未修改项目外 LIFE/KIG 原版计划
- 冻结结论：strict review 确认 0 个未解决 P0/P1；六个 EAP 协议与 Schema 60 已标记为 FROZEN。

## 1. Review 处理结论

R5 review 的 3 个 P2 中，仅“`proactive_rejected_expression_acts` 为无消费者设置”成立，已删除该死设置，表达拒绝继续以 grounded feedback 权重为唯一来源。其余两项不采纳：自然语言反馈在没有已确认 Delivery 时本来就返回空；历史与 Delivery 列表均使用字段白名单，不返回 payload、正文、hash 或 lease。review 重复提出的 `_claim_source` 旧行问题也与当前实现不符，函数会在更新后重新读取 claimed 行。

额外采纳一项 review 目标：`feedback.list_history` 原有逐 Delivery 查询反馈的 N+1，已改成一次 `IN (...)` 批量读取，并用 SQL trace 回归固定查询数。

## 2. 生产路径与异常矩阵

`TimelineSimulator.production_*` 现在写入真实会话消息，并调用生产 Presence、自然反馈、source enqueue、orchestrator、decision、intensity、expression、Delivery claim/begin/ack 和 feedback repository；旧纯领域辅助方法只保留兼容测试。

| 场景 | 结果 | 自动化证据 |
|---|---|---|
| 15 分钟 / 8 小时 / 24 小时 / 3 天 / 30 天 | 仅 8 小时有效问候形成一次可见投递；过早不消费、过期不补发 | `test_production_path_long_horizons_are_traceable_and_expire_safely` |
| 时区变化 | 相同 epoch 按当前本地时区重新计算 quiet hours | `test_timezone_change_recomputes_quiet_hours_without_changing_epoch` |
| 时钟回拨 | 持久化可靠时钟水位；回拨期间处理数为 0 | `test_clock_rollback_suppresses_processing_until_time_recovers` |
| Windows 休眠/唤醒 | Electron `powerMonitor` 通知后端；恢复后 5 分钟 fail-closed，过期任务不瞬发 | `test_windows_sleep_resume_guard_defers_overdue_local_delivery`、system-resume API 回归 |
| 断网 / Provider 失败 | EAP 本机通道不依赖网络；观察 Provider 独立失败不阻断主聊天 | production disconnect 与 observer failure 回归 |
| 应用崩溃 | invocation 前可重新 claim 一次；invocation 后确认未知则失败且永不重试 | `test_claim_crash_recovers_once_but_invocation_crash_never_retries` |
| SQLite busy / 编程错误 | busy 保守返回 0；非数据库编程错误继续抛出，避免静默吞错 | `test_database_busy_is_conservative_and_programming_errors_still_surface` |
| EAP hook / 通知 / Live2D 失败 | 主聊天仍产生 `done`；渲染或通知失败只终结对应 Delivery attempt | API 与 Delivery failure 回归 |

## 3. 第 14 节映射

- 主动表达 6 项：Presence expected-return、睡眠/恢复、Episode pressure/连续忽略、表达 act、`allow_more`、`too_frequent`/`wrong_tone` 由 acceptance scenarios、episodes、feedback 与本报告生产矩阵共同覆盖。
- 关系 5 项：ordinary exchange 零 bond、shared appreciation 限幅、沉默零 bond/trust 下降、source revision 幂等、trust basis 硬约束由 cognition、relationship、acceptance scenarios 覆盖。
- 心境 4 项：迟滞、7 维表达不修改事实/安全/工具/权限/边界、低置信中性回退、主聊天质量隔离由 expression、schemas、cognition 与 API failure isolation 覆盖。
- 安全 5 项：关闭、禁提话题、Level 5 禁用、高 bond 不覆盖拒绝、同 Episode/Decision 不重复投递均有自动化回归；生产指标再次验证违规投递 0、重复 0、追溯率 100%。

## 4. 自然度样本：工程人工初评

评分为本轮工程人工初评，不冒充独立用户研究；strict reviewer 可复评。每项 1～5 分，5 为最佳。

| 候选 | 用户可见样本 | 自然 | 克制 | 无回复压力 | 边界安全 | 结论 |
|---|---|---:|---:|---:|---:|---|
| casual greeting | 路过来看看你。你忙你的，有空再聊。 | 4 | 5 | 5 | 5 | 通过 |
| emotional care | 感觉你刚才有些不好受。我在这里，不急着回应。 | 4 | 5 | 5 | 5 | 通过 |
| return follow-up | 你之前提到「测试结果」，结果怎么样？不急，方便时再告诉我。 | 4 | 4 | 5 | 5 | 通过 |
| chat continuation | 刚才的「项目方案」还想继续聊聊。你有空时我再听。 | 4 | 5 | 5 | 5 | 通过 |
| milestone follow-up | 刚想起「项目完成」。有空时，想听听你现在的感受。 | 4 | 4 | 5 | 5 | 通过 |
| Level 2 quiet waiting | （在这里） | 4 | 5 | 5 | 5 | 通过 |

内部英文主题 `light check-in` / `emotional care` 曾可能进入 Level 3/4 载荷，本轮已在不可变 Delivery 生成前按 candidate kind 转成上述用户文案，并增加正文回归。

## 5. 冻结指标

| 指标 | 施工验收值 | 状态 |
|---|---:|---|
| 真实运行时闭环可达率 | 100% | 通过 |
| 关闭/暂停/拒绝后用户可见主动行为 | 0 | 通过 |
| 重复投递率 | 0 | 通过 |
| 普通聊天机械 bond 增长率 | 0 | 通过 |
| 沉默导致 bond/trust 下降率 | 0 | 通过 |
| 无有效来源的非零关系变化 | 0 | 通过 |
| 无有效来源的主动候选/投递 | 0 | 通过 |
| 可见 Delivery 来源可追溯率 | 100% | 通过 |
| Level 5 外部渠道投递 | 0 | 通过 |
| 未解决 P0/P1 | 0 | 通过并冻结 |

## 6. 后续入口

全量门禁：后端 `937 passed, 1 warning`；前端 `41 passed`；TypeScript/Vite production build `188 modules`；改动范围 Ruff、Electron `main.js`/`preload.js` 语法通过。Windows 构建重新生成 `遐蝶-Setup-0.1.0.exe`（564,038,879 bytes，SHA-256 `EBFD66D18E3BFF6B248018E2307E65F3A3D65BDF8386A02A91687E8E20F0F4B4`），frontend/backend/Lore/BGE-M3 packaged resource verification 全部通过；BGE-M3 ONNX SHA-256 为 `0826f8c1ab9edf1801db86c61919d4d108e8bfc0b809ec823ad366882ff0b77d`。

Windows 实机已验证当前 Electron 桌宠、主窗口、设置与主动陪伴控制页加载。运行安装向导前发现 8756 已被一个能返回遐蝶 health、但 Windows 进程表无法映射 PID 的监听者占用；本轮没有强杀未知监听者，也没有自动安装/覆盖用户环境。严格审查如要求“安装目录启动”而非“安装包资源 + 当前 Electron UI”组合证据，应在释放 8756 后单独复跑；这不影响构建产物完整性结论。

独立 strict review 已确认 0 个未解决 P0/P1。P2-3 的显式初始化分支建议已采纳；P2-1 基于“约 1 秒轮询”的前提与生产默认 30 秒轮询不符，不引入会削弱崩溃恢复水位的内存缓存；P2-2 所述 resume guard 延长属于连续恢复事件下的预期 fail-closed 行为，保留实现并纳入运行监控。六个协议与 Schema 60 已正式冻结。后续 `CDS → LIFE → KIG` 权威计划经独立优化后已以 v0.3 纳入 `docs/`，并由共享所有权契约约束。

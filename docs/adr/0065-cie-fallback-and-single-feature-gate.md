# ADR-0065：CIE 冻结 fallback 与单一功能开关

- 状态：Accepted for CIE.0
- 日期：2026-07-28

## 决策

CIE 使用唯一设置键 `cie_enabled`，默认关闭并对缺失、空值或未知值 fail-closed。CIE.0 只建立该控制面，不把它接入聊天热路径，也不启用任何 CIE.1～CIE.5 能力。

冻结 fallback 为当前单消息、单生成、纯文本 SSE 路径；附件继续仅走本地文本提取。消息积累、活动生成取消、原生图片、回复节奏状态机或第三方贡献中的任一能力失败时，必须回到这条路径。

## 原因

多个独立功能开关会形成不可验证的组合状态，也容易让部分能力在回滚后继续写入。先固定唯一总门，可以让后续阶段在同一个默认关闭边界内递增，同时保持 CIE.0 无迁移、无运行时行为变化。

## 后果

- `cie_enabled=0` 是默认和紧急回滚状态。
- `cie_enabled=1` 在 CIE.0 不代表新能力可用；各阶段完成门仍必须单独满足。
- 首个迁移号 81 仍为暂定候选，CIE.0 不占用。
- LIFE v2 的 `InnerStateEvent` 和 `ShortMemo` 不受此开关管理，也不得由 CIE 实现。
- CIE.0 基线使用 `python scripts/run_cie0_baseline.py` 单一入口；传入 `--measure-provider` 时在同一次运行刷新 Provider 延迟，不传时保留已提交的有效实测值。

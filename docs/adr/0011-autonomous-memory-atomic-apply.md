# ADR-0011：自主记忆的原子写入与保守去重

- 状态：Accepted
- 日期：2026-07-15
- 决策者：项目所有者、Codex
- 关联计划：`docs/MEMORY_SYSTEM_DESIGN_FOR_BEGINNERS.md` 阶段 B.3

## 背景

B.2 已能得到通过协议校验的净化候选，但终点只是 `validated`。正式自主记忆要求模型路径
不再等待逐条确认，同时又必须保证来源、Fragment、实体关系、事件与任务状态不会部分成功。

## 决策

- worker 得到净化候选后，在一个 `BEGIN IMMEDIATE` 事务中重新读取来源用户消息、来源助手
  消息和全部证据消息，并再次执行完整协议校验。
- 同一事务依次完成 Fragment、显式实体关系、规则实体关系、`autonomous_created` 事件和
  `memory_observer_runs.status=applied`；任何异常全部回滚，随后在独立小事务中进入恢复态。
- 每条候选使用 `protocol:source_assistant_message_id:item_index` 作为来源幂等键。同 scope/kind
  的内容仅在规范化后完全相等时跨轮复用；不做模糊相似去重，以免把否定、时间变化或纠正
  错当重复，相关冲突留给后续冲突系统处理。
- 自动 Fragment 暂统一进入 L2，importance 保留为独立字段；不根据单次模型分数自动写 L0。
- `sensitivity=sensitive` 的 Fragment 可以保留本地来源链，但默认禁用且不创建实体关系；
  forbidden 内容仍在协议层直接拒绝。
- 模型路径可用时不再创建旧 `memory_candidates`。只有模型起始不可用或重试耗尽时，才调用
  旧关键词候选作为保守兜底；兜底自身失败不能影响聊天或 worker。

## 后果

- 遐蝶现在可以按人格观察结果自主写正式 Fragment，并保留完整来源与审计链。
- 进程中断、重复投递和数据库异常不会产生半条记忆或重复 Fragment。
- B.4 只需要切换展示与管理入口，不再承担正式写入逻辑。

## 回滚

停止记忆 worker 或移除聊天入队即可停止新增自主记忆。已写 Fragment 使用 `source=observer`、
观察器版本和来源幂等键可识别；无需删除旧候选，也不改变手工记忆。

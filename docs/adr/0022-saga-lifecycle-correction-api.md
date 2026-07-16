# ADR-0022：Saga 精确生命周期、双纠错边界与只读关系建议

- 状态：Accepted
- 日期：2026-07-16
- 阶段：记忆系统 D.5

## 背景

D.4 已能自动创建和追加正式 Saga，但生命周期字段还没有唯一写入口，对外也无法审查来源、事件和后台
任务。人物陪伴 Agent 不能因为模型一句模糊判断就结束、删除故事或直接提高关系值；用户纠正文字与
纠正证据归组也必须是两种不同操作。

## 决策

1. `active → completed → archived` 是主路径；`completed/archived → active` 是受控恢复；任一非删除
   状态只有用户或隐私清理能进入 tombstone，tombstone 永不可恢复。active 不能直接 archived。
2. 自动完成必须在写事务内重验当前有序来源链，完成证据必须包含最新 Episode，且每条完成证据都含
   明确结束词。用户可主动结束而不伪造来源证据，此时不产生关系建议。
3. completed Saga 的合格追加候选可以消费新的正式后缀 Episode；若新摘要仍为 active，则同一应用事务
   恢复 active 并保留旧 completed 事件。archived 自动恢复仍禁止。
4. `revision` 是所有 Saga 写 API 的乐观锁。生命周期、正文纠错和来源纠错都使用 `BEGIN IMMEDIATE`
   短事务，重读状态与来源后才提交事件和新 revision。
5. `/correct` 只改标题、摘要、主题、当前阶段或重要度；文本改动标记 `user_edited/manual-v1`，清除模型
   证据但不改变 Episode 关系和来源哈希，并拒绝提示注入文本。
6. `/correct-sources` 明确改变证据链：至少两个正式 Episode，必须按时间排序且不属于其他 Saga；事务内
   重建抽取摘要、分组指纹、Entity、来源快照与整链哈希，并分别审计 removed/added。completed 的完成
   证据被重建时恢复 active。
7. 完成事件可生成不可直接应用的 `shared_saga_completed` 建议，bond 上限 0.02、trust 上限 0.01。
   Saga 服务不写 relationship_state 或 affect_state；恢复、来源纠错和 tombstone 只将建议标记 revoked，
   不删除历史。
8. 开放正式 Saga 列表、详情、时间线、来源、事件、关系建议、摘要模型以及 Consolidator run/cancel API；
   不开放单 Episode Saga 或无来源手工创建入口。

## Review 取舍

- 采纳 D.4 review 的事务内生命周期复核、run/event API、最小两 Episode 和双纠错边界建议。
- 优雅停机部分采纳：取消中的 worker 立即把已认领任务送回 recovery_pending；线程内模型工作可以自然
  结束，但不会继续进入正式应用事务。
- N13/N14 是界面体验问题，继续留给 D.6。

## 后果与验收

状态、纠错和关系建议均可沿 Saga event 与来源 Episode 审计；旧客户端提交会因 revision 冲突得到 409，
不会覆盖新状态。7 项 D.5 专项测试覆盖合法/非法转换、单向删除、自动完成、自动恢复、注入拦截、两类
纠错、跨 Saga 冲突、关系隔离和 API；全量后端 183 项、前端 7 项及桌面生产构建通过。

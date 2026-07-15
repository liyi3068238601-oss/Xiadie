# ADR-0012：自主记忆提示、纠错与旧候选界面边界

- 状态：Accepted
- 日期：2026-07-15
- 决策者：项目所有者、Codex
- 关联计划：`docs/MEMORY_SYSTEM_DESIGN_FOR_BEGINNERS.md` 阶段 B.4

## 背景

B.3 已能自主写正式 Fragment，但聊天完成事件早于后台应用结果，旧界面仍宣称所有内容都需
用户确认，也无法区分普通编辑与“你记错了”这种纠错行为。

## 决策

- schema 12 为观察 run 保存本轮真正新建的 Fragment ID；复用旧 Fragment 不算新记忆。
- 聊天页通过只返回状态和数量的最小结果 API 短轮询，不读取候选正文。只有新建、active、
  enabled 的 Fragment 才显示轻提示；提示五分钟限频且约五秒后消失。
- 记忆页展示 scope、kind、importance、emotion、inner_reason、observer_version、证据数量和
  来源，但不在聊天提示中暴露记忆正文。
- `POST /api/memories/{id}/correct` 使用 `corrected` + `user_correction` 审计语义；普通 PATCH
  继续使用 `updated` + `user`，便于以后建立冲突与纠正关系。
- 旧候选移入默认折叠的兼容区。退役条件由
  `docs/LEGACY_MEMORY_CANDIDATE_RETIREMENT.md` 固定，B.4 不删除表、API 或历史数据。

## 后果

- 用户能感知遐蝶自主记住了事情，但不会被每轮提示打扰，也不会看到敏感正文。
- 自主记忆的选择理由和来源可检查，错误内容可用明确纠错语义维护。
- 旧候选不再占据主流程，同时仍可安全处理历史 pending 数据。

## 回滚

停止前端轮询即可关闭提示；正式 Fragment 不受影响。旧候选兼容区和 API 可以继续保留，
schema 12 的新建 ID 审计字段无需回退。

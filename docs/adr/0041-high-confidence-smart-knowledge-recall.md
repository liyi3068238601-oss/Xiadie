# ADR-0041：仅高置信的智能知识召回

- 状态：已接受
- 日期：2026-07-17
- 阶段：K.5

## 背景

K.2～K.3 已在影子模式中建立自然召回判断、固定集、dense 下限、去重和预算；K.4 已把所有真实知识片段纳入
一次性远传授权。要让陪伴对话自然使用知识库，仍需回答两个问题：哪些判断足以真实注入，以及如何保证 smart 不绕过
explicit 的引用、策略和授权边界。

K.3 的 23 条样本只有 13 个相关 dense 和 3 个无关 dense，不足以支持 pure semantic 升档。K.5 将固定集扩大到
52 条，其中有 30 个相关 dense、15 个无关 dense，并增加 allow/deny/filter 三种授权流转样本。

## 决策

1. 新增 `off`、`explicit`、`smart` 三种召回模式，旧库和新安装均默认 `explicit`。模式变化写入无正文审计事件。
2. `off` 不运行知识检索，即使用户明确要求查文档也不注入；`explicit` 完全保留原有明确请求行为，并继续运行不影响
   回答的后台影子判断；`smart` 同时支持明确请求和自然召回。
3. smart 只把 `confidence_band=high` 且 action 为 retrieve/ask 的候选准备成真实上下文。medium/low 无论分数、策略或
   Provider 如何都不得注入，也不得仅因文档需要授权而被反向提升为 high。
4. 当前 high 只来自确定性规则：明确请求、至少 3 字符的标题/标签精确术语，以及有明确句式的知识—记忆来源冲突。
   2 字符实体、lexical 和 pure semantic 均保持 medium。
5. v3 固定集显示相关 dense 最低 0.477699、无关 dense 最高 0.561615，正负类别发生重叠；因此
   `SEMANTIC_AUTO_HIGH_ENABLED=false`。保留 0.472169 仅作为候选去噪下限，不能视为注入授权阈值。
6. 固定集中的 23 个自然 smart high 判断 precision 为 100%；在 30/15 样本量门槛满足后，仅开放这条确定性 high
   路径。整体 action accuracy 98.08% 的唯一误触发为 medium semantic，真实注入门槛会将其拦截。
7. smart 候选复用 K.4 的 `_plan()`、内容哈希复核、policy/location revision、grant 签发和原子消费；不新增旁路，
   unknown Provider 继续按 remote 处理。
8. SSE 只公开 `knowledge_recall_mode` 与 `knowledge_source=none|explicit|smart|confirmed`，不公开查询或正文。真实 smart
   判断以 `shadow=0`、计数和哈希落库；授权消费后 grant 单向关联该 RecallDecision。
9. 本地 natural 选择继续使用最多 4 条、700 token；最终引用仍必须经过现有 citation key 白名单和当前 chunk 哈希复核。
10. 知识 worker 空闲时推进过期 grant 的状态并清空 token hash；不物理删除审计行，物理保留期仍由 K.8 决定。
11. 授权卡增加初始焦点、Tab 循环、Escape 取消、`aria-modal` 与 live status；安全确认必须可通过键盘完成。

## 后果

- 用户可以在文件与知识页明确选择关闭、明确请求或智能召回；切换立即影响下一次预检。
- smart 会在发送前多运行一次本地检索，聊天消费时再次计算以防 TOCTOU。当前固定集本地检索 P90 约 30 ms，暂不以
  缓存换取放宽后端复核。
- pure semantic 的召回率不会被人为升高；这会漏掉部分自然改写，但避免把无关资料静默送入陪伴对话。
- K.5 不改变知识与记忆的来源隔离；该链路由 K.6 专项实现和验收。

## 验证

- v3 纯合成固定集 52 条、fixture SHA-256 固定、30/15 dense 证据及 high precision 报告。
- off 不搜索不注入；explicit 旧行为回归；smart high 自动注入并落真实 Decision；medium 永不生成 prepared context。
- smart ask_each_time → allow_once → confirmed 的真实数据库端到端；模式切换使旧 grant 失效。
- SSE source/mode、citation 白名单、Provider 策略矩阵、并发/过期/重放继续由 K.4 测试覆盖。
- 前端三模式文案、授权卡四选项及键盘可访问性契约。

## 回滚

将设置改回 `explicit` 即可立即停止自然真实召回而不删除任何数据。代码回滚可忽略 schema 38 的 mode 事件表和 grant
新增列；不得降低 `schema_meta`。若 smart 出现线上误触发，应先强制全局回退 explicit，再保留无正文 Decision 用于校准。

## 后续事项

- [ ] K.6 验证知识引用不会被记忆观察器复制成相处记忆。
- [ ] K.7 扩大真实匿名结论与合成困难样本，再评估 query 清理/reranker；不得沿用重叠 dense 分数直接升档。
- [ ] K.8 定义 RecallDecision/grant/retrieval/citation 的物理保留期。

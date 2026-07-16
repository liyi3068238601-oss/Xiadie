# ADR-0040：一次性知识远传授权

- 状态：已接受
- 日期：2026-07-17
- 阶段：K.4

## 背景

K.1 已让文档表达 `remote_allowed`、`ask_each_time` 和 `local_only`，Provider 也保存 local/remote/unknown
及位置 revision；K.2～K.3 的自然召回仍处于影子模式。显式检索此前会把命中片段直接放进模型上下文，无法保证
`ask_each_time` 和 `local_only` 在远程 Provider 下真正生效。前端布尔值不能作为安全授权，因为它可伪造、重放，
也无法绑定模型、消息和资料版本。

## 决策

1. 后端预检重新运行检索并按数据库中的当前文档策略分类，不接受前端提交的 chunk 或 document 列表。
2. local Provider 可直接使用三种策略的本地片段；remote/unknown Provider 只有 `remote_allowed` 可直接使用。
   unknown 始终按 remote 保护。
3. remote/unknown 命中 `ask_each_time` 或 `local_only` 时创建短期 grant。grant 绑定会话、请求 nonce、规范化
   消息哈希、查询哈希、Provider/model/location revision、阈值版本、策略快照哈希、计划哈希、chunk 内容哈希、
   policy revision、敏感级别和 token 估算。
4. 用户选择“只允许这一次”时，用 `secrets.token_urlsafe(32)` 生成 256-bit 随机 token。响应只返回一次明文，
   数据库只保存 SHA-256；token 五分钟过期并且只能从 `issued` 原子切换一次到 `consumed`。
5. grant 消费与用户消息写入在同一个 SQLite 事务中完成。消息外键使用延迟检查，使并发请求中只有一个请求可消费；
   没有有效 grant 时受限片段在写消息及调用模型前即被拒绝。
6. 用户选择“不使用资料”时，后端只在原始候选集合内取交集并剔除所有受限片段。此选择不需要发送授权；用户可以
   选择“始终允许”或“仅限本地”原子修改文档策略并增加 revision、写入无正文策略事件。
7. `local_only` 永远不能签发远传 token；敏感文档不能改为 `remote_allowed`。策略、Provider、模型、位置、来源或
   内容哈希变化会使已签发 grant 失败并撤销。过期、拒绝和重放均 fail closed。
8. 在线模型调用在 grant 消费之后失败时，grant 仍保持 `consumed`，不得重放。用户重试必须重新预检和确认，避免
   无法确定远程服务是否已收到请求时重复发送。
9. grant、item 和 event 不保存查询正文、知识正文、文件路径、文件名或明文 token。确认接口只返回展示所需的
   Provider、文档名、策略、敏感级别、片段计数和 token 范围；物理清理由 K.8 的审计生命周期统一处理。

## 后果

- 显式知识检索也获得与未来 smart 召回相同的远传安全闸门；K.5 可以复用协议而不另开旁路。
- 每条聊天在发送前多一次本地预检。预检不调用远程模型，但会重复一次本地检索；后续只有测得明显延迟后才考虑
  传递只读检索快照，不能以性能为由信任前端候选。
- 取消确认会留下最多五分钟的无正文 pending 审计，随后转为 expired；K.8 再定义物理保留期。
- 当前自然召回仍为 shadow，K.4 没有打开自动注入。

## 验证

- schema 37 重复初始化，grant/event 表无正文和无明文 token。
- remote/unknown/local 与三种文档策略矩阵。
- 无 grant 拒绝且不写消息；拒绝后收窄候选；敏感和 local_only 组合受限。
- token 哈希保存、单次消费、并发重放、过期、策略 revision 变化和在线调用失败。
- 策略持久选择增加 revision 并写无正文事件。
- 前端四种选择、Provider 位置、文档/片段/token 范围和结构化错误契约。

## 回滚

代码回滚可停止读取 schema 37 新表，但不能通过降低 `schema_meta` 伪装数据库降级。若必须物理移除表，需先停止
应用、备份数据库，再按外键依赖顺序删除 grant events、items、grants 并重建 recall decisions 以移除新增列。

## 后续事项

- [ ] K.5 将通过评测门槛的 smart recall 接入同一预检和授权协议。
- [ ] K.8 定义 pending/issued/consumed/event 的审计保留和物理清理。
- [ ] K.9 完成打包应用中的真实本地/在线 Provider E2E。

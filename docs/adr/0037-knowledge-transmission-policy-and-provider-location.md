# ADR-0037：文档远传策略与 Provider 执行位置地基

- 状态：Accepted
- 日期：2026-07-16
- 决策者：项目所有者、Codex
- 关联版本/任务：知识库优化 K.1、schema 35
- 取代：无
- 被取代：无

## 背景

ADR-0036 已固定“本地命中不等于允许远传”。在实现影子预检或一次性授权前，数据库必须能够独立表达每份文档的
发送边界，以及当前聊天 Provider 的真实执行位置。现有 `sensitivity` 只表示敏感程度，Provider 也只有 URL，二者
都不足以成为后续授权判断依据。

## 决策

1. schema 35 为文档增加 `transmission_policy`、`policy_revision`、`policy_updated_at`。合法策略为
   `remote_allowed / ask_each_time / local_only`；所有旧文档迁移为 `ask_each_time`。
2. 新导入普通文档默认 `ask_each_time`；敏感文档默认 `local_only`。数据库触发器与服务层双重禁止
   `sensitive + remote_allowed`，避免绕过 HTTP API 形成非法组合。
3. 文档策略发生真实变化时 revision 加一并写入无正文事件；重复设置相同值保持幂等，不制造虚假 revision。
4. schema 35 为 Provider 增加 `execution_location`、`location_revision`、`location_confirmed_at`。合法位置为
   `local / remote / unknown`，后续授权判断必须把 `unknown` 当作 `remote`。
5. 内置 mock 固定 local；内置在线供应商默认 remote；Ollama 只有在 HTTP(S) 回环地址且无 URL 用户信息时才自动
   判为 local；custom 默认 unknown，用户可在设置页明确确认位置。
6. Provider 的 Base URL 变化总会递增 location revision，即使重新判定后位置文字相同。位置值变化也递增 revision，
   为 K.4 失效旧 grant 提供稳定绑定字段。
7. 非 mock Provider 只有明确回环 URL 才允许被用户标为 local；保存设置时不主动联网探测服务，因为联网结果既不能
   证明模型执行位置，也会引入未披露副作用。
8. K.1 新增文档策略读写/事件 API，并扩展现有 Provider API；不新增自然预检、grant 或自动注入路径，聊天继续保持
   explicit 基线。

## 备选方案

### 复用 sensitivity 表示远传许可

- 未采用：敏感程度和数据发送许可不是同一概念，无法表达普通但仅本地或敏感且逐次询问。

### 只根据 URL 或 Provider 名称实时猜测

- 未采用：名称可以对应自建服务，URL 也会变化；没有 revision 就无法可靠使旧授权失效。

### 保存时主动连接 Provider 判断位置

- 未采用：连接成功不能证明推理发生在本机，且会产生额外网络请求。K.1 使用保守分类和用户确认。

## 后果

### 正面后果

- 后续预检和 grant 有可版本化、可审计的策略输入。
- 旧数据保守迁移，不会因为升级自动获得远传许可。
- UI 能准确说明文档是否可能发送以及 Provider 位于何处。

### 代价与限制

- 当前策略只建立地基；`ask_each_time` 的确认和 `remote_allowed` 的智能注入尚未实现。
- 用户确认 custom 为 local 仍只代表配置声明，未来授权消费还需复核 URL 与 revision。
- Provider 位置事件账本和 collection 默认策略推迟到 K.8，K.1 先保留 revision 和确认时间。

## 安全与隐私

- 策略事件只记录 document ID、前后策略、revision、actor、reason code 和时间，不含文件名、正文、查询或路径。
- Provider API 仍不返回 API Key。
- 本阶段不发送任何新增网络请求，也不改变现有知识片段注入条件。

## 数据与迁移

- schema：34 → 35。
- 旧文档：统一 `ask_each_time`、revision 1，更新时间取原 `updated_at`。
- 旧 Provider：mock local、已知在线供应商 remote、本机默认 Ollama local、custom unknown。
- 迁移通过 schema_meta 只执行一次；重复 `init_db()` 不重复 ALTER 或改写 revision。

## 验证

- 旧库升级、重复初始化、文档/Provider 默认值和事件无正文测试。
- 普通/敏感导入默认策略、数据库触发器和 API 非法组合测试。
- Provider 自动分类、带用户信息的 URL 拒绝、地址变化 revision、非回环 local 拒绝测试。
- 前端契约验证三种文档策略和三种 Provider 位置均有准确文案。

## 回滚

schema 35 使用 SQLite `ALTER TABLE ADD COLUMN`，代码回滚可忽略新增列但不会自动删除。若必须物理降级，需要在备份后
重建旧表；不能通过修改 schema_meta 假装降级。策略数据本身不包含正文，可随文档删除级联清理事件。

## 后续事项

- [ ] K.2 的 RecallDecision 保存 policy/location revision 快照。
- [ ] K.4 的 grant 绑定并复核两个 revision。
- [ ] K.8 增加 collection 默认策略、批量修改和审计生命周期。

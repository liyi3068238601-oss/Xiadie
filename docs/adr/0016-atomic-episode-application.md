# ADR-0016：正式 Episode 必须与来源、审计和任务终态原子提交

- 状态：Accepted
- 日期：2026-07-15
- 决策者：项目所有者、Codex
- 关联版本/任务：记忆系统阶段 C.5
- 取代：无；细化 ADR-0013 的应用事务
- 被取代：否

## 背景

C.4 已经能为高分候选生成受来源约束的摘要，但候选仍停留在 `pending`，需要用户手动确认才能
形成正式 Episode。最终陪伴型 Agent 应自主整理经历，同时必须避免以下问题：

- worker 重试或并发运行时，同一候选生成两个 Episode；
- 一个 Fragment 被两个正式 Episode 同时占用；
- 摘要完成后 Fragment 被纠正，旧摘要仍被提交；
- Episode 已写入，但来源关系、实体关系、候选状态、审计事件或 run 终态只写了一部分；
- 应用失败后候选丢失，无法进行有限重试。

C.4 review 通过且无新增缺陷。审查建议在正式应用路径再次核对 `source_hash` 和 Fragment 单一
归属；本阶段采纳。摘要批处理耗时优化与校验状态界面分别留给性能阶段和 C.6。

## 决策

### 1. 应用前的不可变条件

自动应用只处理按分数、时间和 ID 稳定排序后的最多 20 个 `pending` 候选。进入写事务后逐个复核：

1. 候选仍为 `pending`，并具有 `extractive_fallback` 或 `model_validated` 安全摘要；
2. 来源仍有 2～20 条，全部为 active、enabled、normal 且不含已知敏感/注入模式；
3. 当前 Fragment 内容哈希与候选保存的 `summary_source_hash` 完全一致；
4. 摘要证据 ID 非空且全部属于本候选来源；
5. 全组仍至少共享一个 active Entity；
6. 每个 Fragment 尚未归属正式 Episode；
7. 标题与摘要非空且通过安全内容检查。

任一条件不满足都拒绝整个应用事务，不降低阈值、不忽略来源，也不写入部分 Episode。

### 2. 单一短事务

`apply_candidates_for_run` 使用 `BEGIN IMMEDIATE`。同一事务内按顺序完成：

1. 创建正式 `memory_episodes`；
2. 按候选顺序写入 `memory_episode_fragments`；
3. 继承来源 Fragment 关联的全部 active Entity；
4. 把候选更新为 `accepted` 并保存 resolved Episode；
5. 写入候选 `accepted` 与 Episode `created` 两类记忆审计事件；
6. 把 Consolidator run 更新为 `applied` 或 `skipped`，保存结果 Episode ID；
7. 写入 run 的 `processed` 状态事件。

任何 SQL、约束或审计异常都会回滚以上全部内容。模型调用不在该写事务内。

### 3. 幂等与单一归属

- `memory_episodes.candidate_id` 建立非空唯一索引，同一候选只能生成一个正式 Episode；
- `memory_episodes.grouping_fingerprint` 建立非空唯一索引，同一稳定来源分组只能生成一个 Episode；
- 原有 `memory_episode_fragments.fragment_id` 唯一索引继续保证 Fragment 单一正式归属；
- 应用代码在插入前显式检查归属，用稳定错误码报告，而数据库唯一约束作为最后防线；
- 重复向新 run 提交已经 accepted 的候选只会得到 `skipped`，不会创建副本。

### 4. 来源与摘要快照

正式 Episode 独立保存以下快照，不要求日后必须回查仍可能变化的候选才能解释自身：

- `grouping_fingerprint`、分组策略版本和应用协议版本；
- 有序 `source_fragment_ids_json` 与应用时的 `source_hash`；
- 摘要状态、协议版本、供应商、模型和证据 Fragment ID；
- `candidate_id`，用于回溯评分、警告、token 和修复审计。

`memory_episode_fragments` 仍是规范化来源关系；JSON 来源集合是稳定审计快照，两者在应用事务中
同时写入。

### 5. 失败与恢复

- 应用失败后候选保持 `pending`，正式 Episode 与关系表不留半成品；
- 候选记录应用尝试次数、最近错误码和时间；
- 单个候选连续失败三次后退出自动批次，避免永久失效候选阻塞后续经历；
- 如果来源后来被纠正并生成了新的摘要哈希，候选失败计数与错误码清零，可重新进入自动批次；
- run 按既有最多三次、指数退避策略进入 `recovery_pending` 或 `exhausted`；
- 重试会重新读取所有 pending 候选并刷新安全摘要，因此来源纠正后可使用新哈希重新提交；
- 候选错误审计自身失败时，run 仍必须进入有限恢复，不能悬挂在 `running`。

### 6. 旧确认 API 的过渡边界

C.5 暂时保留旧接受/拒绝 API，供迁移与测试兼容，但接受路径也改用显式写锁、来源哈希复核、
唯一约束和同一内部应用函数。用户编辑标题、摘要或来源时，正式 Episode 标为 `user_edited`，
不会伪装成模型校验摘要。C.6 将从主界面移除候选确认流程，只保留 Episode 详情与纠错。

## 备选方案

### 每个 Episode 单独提交，最后再更新 run

- 优点：一个坏候选不影响同批其他候选。
- 缺点：崩溃时 run 与已提交 Episode 可能不一致，也不符合 ADR-0013 的应用事务边界。
- 未采用原因：当前每批最多 20 个且均为本地短写入，整批原子性更重要。

### 只依赖数据库唯一约束

- 优点：代码更少。
- 缺点：错误只能表现为通用完整性异常，无法区分来源变化、归属冲突或重复候选。
- 未采用原因：可恢复后台任务需要稳定、可审计的原因码。

### 正式 Episode 只保存 candidate_id

- 优点：字段更少。
- 缺点：候选纠正、迁移或未来退役后，Episode 的来源与摘要生成边界不再自解释。
- 未采用原因：正式长期记忆必须保留自己的来源链快照。

## 后果

### 正面后果

- 用户不再需要逐条确认高分 Episode 候选；
- 崩溃、重试和重复 run 不会留下重复或半完成 Episode；
- 每个正式 Episode 都能说明来源顺序、内容哈希、分组策略和摘要状态；
- Fragment 纠正与正式应用并发时，旧摘要不会越过事务边界。

### 代价与限制

- 同批任一候选失败会使整批回滚并重试；当前批量上限 20，使锁持有时间保持可控；
- `source_fragment_ids_json` 与关系表存在有意的数据重复，需要测试保证一致；
- C.5 仍保留旧确认 API 和候选界面，界面切换由 C.6 完成。

## 安全与隐私

- 权限等级：本地 S0；应用事务不访问网络、不调用工具；
- run 事件只保存 Episode/候选 ID 和计数，不保存 Fragment 正文、完整 prompt 或密钥；
- 正式 Episode 保存已经通过安全过滤的摘要与来源 Fragment ID；原始模型输出仍不落库；
- 自动流程不会放宽 sensitive、inactive 或 disabled Fragment 的边界。

## 数据与迁移

schema 16：

- Episode 新增分组指纹、策略、来源 ID/哈希、摘要审计和应用协议字段；
- candidate 新增应用尝试次数、错误码和最近应用时间；
- Episode 的 candidate ID 与 grouping fingerprint 增加非空唯一索引；
- 旧 Episode 使用 `legacy`/`legacy_rule` 默认值，不伪装成自动原子应用结果。

## 验证

- 自动成功：Episode、来源顺序、实体、摘要快照、候选、双份审计和 run 同时提交；
- 来源竞争：摘要后修改 Fragment，应用拒绝；刷新摘要后同一候选可重试成功；
- 中途失败：在审计步骤注入异常，所有 Episode 数据与 run 终态回滚；
- 幂等：accepted 候选重复提交不生成第二个 Episode；
- worker：应用错误进入有限恢复并保存稳定原因码；
- 全量后端、前端测试、生产构建与 Electron 脚本检查。

## 回滚

停止 worker 的自动应用调用即可回到 pending 候选兼容流程。schema 16 字段和唯一索引保留，已经
原子生成的 Episode 作为普通正式 Episode 保留，不自动拆散或删除来源。

## 后续事项

- [ ] C.6 从主界面移除候选确认流程，展示 Episode 来源、摘要校验状态与人工纠错入口。
- [ ] 后续性能阶段评估多候选摘要串行调用耗时，必要时缩小单次摘要批量或拆分预算。

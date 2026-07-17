# 遐蝶桌面 Agent — 综合开发执行计划 v1.0

版本：v1.0
制定日期：2026-07-17
状态：执行中
适用对象：项目所有者、Codex、后续协作者

---

## 0. 文档定位

本计划替代以下已过时的文档：

- `遐蝶Agent_Codex_PR拆分执行计划_v0.6.2`（PR-001~037 已与实际进展严重脱节）
- `遐蝶Agent_v0.2UI回归与Cyrene风格融合迁移方案_v0.6.1`（Phase 0~10 粒度太粗）

本计划整合以下仍在生效的文档，将其拆分为可逐个执行的小步骤：

- `XIADIE_LONG_TERM_ROADMAP.md`（应用版本路线）
- `KNOWLEDGE_SYSTEM_OPTIMIZATION_PLAN.md`（K.6~K.9 尚未完成）
- `CODEX_PROJECT_CONTEXT.md`（冻结决策与规则）

### 当前真实基线（2026-07-17）

| 项目 | 状态 |
|---|---|
| 后端测试 | 366 passed，1 warning |
| 前端测试 | 32 passed |
| 数据库 schema | v38 |
| 后端文件 | `main.py` 1865 行，`db.py` 1840 行（需拆分） |
| 前端文件 | `App.tsx` 266 行，`styles.css` 2738 行（未拆分） |
| ToolRegistry | **未实现**（grep 为空） |
| API Key 存储 | **明文**在 SQLite |
| 上下文预算 | **无** |
| 知识库自然召回 | K.0~K.5 已完成，当前 explicit 默认，smart 可选 |
| 记忆系统 | A~F 全 6 阶段已完成（Fragment→Episode→Saga→Archivist） |
| 情绪关系系统 | 4 阶段已完成（9×5 语调网格 + Live2D 联动） |
| Live2D 桌宠 | 已实现：固定模型、透明置顶、托盘、主窗口联动 |

---

## 1. 执行规则（每个 PR 都必须遵守）

### 1.1 范围限制

- 每个 PR 只做一个主题，通常 4~12 个文件。
- 超过 15 个文件必须停止并说明原因。
- 不同时做重构 + 新功能 + 视觉改版。
- 不做任务范围外的"顺手优化"。

### 1.2 验证要求

- 后端修改：`cd backend && .venv/Scripts/python.exe -m pytest tests -q` 全绿
- 前端修改：`cd frontend && npm run build` 通过
- Electron 修改：`node --check main.js && node --check preload.js` 通过
- 数据库迁移：验证新库初始化、旧库升级、重复迁移和回滚
- 权限修改：验证允许、拒绝、取消、超时和越权

### 1.3 交付物

- 修改文件清单
- 行为变化说明
- 验证结果
- 已知风险和下一步建议

### 1.4 禁止事项

- 不推倒重写
- 不迁移到 Tauri
- 不实现 Live2D 模型导入/替换/市场
- 不把 API Key 或敏感正文写入日志
- 不静默扩大权限或远传范围
- 不为了通过测试而删除有效测试

---

## 2. 阶段一：知识库收尾（K.6 ~ K.9）

> 当前施工焦点。K.0~K.5 已完工，K.6 待开工。

### K.6：知识与记忆隔离

目标：知识事实不能污染相处记忆，四线隔离（人格/Lore/相处记忆/用户知识）在记忆写入路径上强制执行。

#### K.6.1 — 观察器元数据协议

- [ ] 定义 `KnowledgeContextMeta` 结构：本轮是否使用知识、引用 citation key、document/chunk ID、哪些助手文本片段来自资料
- [ ] 在聊天完成后将元数据注入记忆观察器的输入，不复制知识正文
- [ ] 观察协议增加 `observation_source` 字段区分：`conversation` / `knowledge_reference` / `user_confirmed_fact`
- [ ] 覆盖测试：用户查了资料但没确认 → 不产生 Fragment

#### K.6.2 — 写入规则硬编码

- [ ] 在 `memory_observer.py` 和 `saga_consolidator.py` 中硬编码拒绝列表：knowledge chunk、citation 正文、资料摘要
- [ ] Fragment 来源拒绝 knowledge_documents、knowledge_chunks 表
- [ ] Episode 分组拒绝以 knowledge 来源的 Fragment 为输入
- [ ] 自动化测试：伪造一条 knowledge 来源候选 → 断言观察器拒绝写入

#### K.6.3 — 用户决定识别

- [ ] 实现"用户明确采纳资料为现实计划"的判定：需用户消息中明确包含决定词（"以后按这个""我决定""就照这个做"）+ 紧接着一个知识引用
- [ ] 用户决定产生的 Fragment 来源标记为 `user_confirmed`，不标记为 `knowledge`
- [ ] 覆盖测试：用户只说"原来如此""好的" → 不产生 Fragment

#### K.6.4 — 冲突回答提示

- [ ] 在 `persona.py` 系统提示中增加冲突处理规则：资料事实与相处记忆不一致时说明来源差异，不擅自合并
- [ ] 知识注入块的上下文中加上 `source_type: user_knowledge` 标记，与记忆块 `source_type: shared_memory` 区分
- [ ] 覆盖角色设定、项目规范、用户偏好、共同经历四类混淆测试

#### K.6.5 — 观察器失败隔离

- [ ] 观察器异常不抛出到聊天完成路径
- [ ] 观察器失败时知识引用和 SSE 落库正常完成
- [ ] 失败写入 `memory_observer_errors` 事件（无正文）
- [ ] 覆盖测试：模拟观察器崩溃 → 聊天正常返回 + 引用正常落库

**验收标准**：固定测试集中知识事实误写相处记忆的数量为 0。

---

### K.7：检索质量第二阶段

目标：提升检索相关性，但不引入远程模型或独立向量数据库。

#### K.7.1 — 查询清理

- [ ] 实现本地确定性查询清理：去掉寒暄前缀、纯感叹、无检索价值的后缀
- [ ] 保留人名、项目名、术语、数字、时间、英文单词
- [ ] 对代词（"他""那个项目"）只使用最近两轮已命名的实体，不凭空解析
- [ ] 同时保留原始查询指纹和清理后查询指纹
- [ ] 覆盖测试：中文长问题、代词、数字混合、英文术语

#### K.7.2 — 规则重排

- [ ] 在现有 RRF 融合后增加第二层规则排序：实体覆盖加分、标题路径匹配加分、文档优先级加分、定位完整度加分
- [ ] 每项规则的权重可通过配置调整
- [ ] 覆盖测试：同文档多 chunk 的排序、多文档相同内容的排序

#### K.7.3 — 内容哈希聚类与去重

- [ ] 跨来源相同 content SHA-256 聚类（已在 K.3 实现）
- [ ] 同文档相邻 chunk 的 3-gram Jaccard 去重（已在 K.3 实现）
- [ ] 增加多文档重复内容的聚类选择逻辑：优先保留定位更完整、策略更宽松的 chunk
- [ ] 覆盖测试：三份文档含相同段落 → 只保留一份

#### K.7.4 — 来源多样性选择

- [ ] 在预算限制内实现轻量 MMR：已选 chunk 与候选 chunk 的 Jaccard 相似度折减
- [ ] 每个 collection 最多贡献 2 条（自然）/ 3 条（明确）
- [ ] 覆盖测试：单一文档 10 条命中 → 选出多样化来源

#### K.7.5 — 本地 reranker 评估（决策门）

- [ ] 调研可选本地 reranker 方案（如 BGE-Reranker、ONNX cross-encoder）
- [ ] 测量打包体积、加载延迟和推理耗时
- [ ] 在固定评测集上对比 RRF vs reranker 的 precision/recall
- [ ] 收益不足或打包代价过大 → 明确拒绝并记录 ADR
- [ ] **本步骤不是必须实现，而是必须做出明确决策**

#### K.7.6 — 协议版本与离线对比

- [ ] 为 `knowledge-recall-decision` 协议递增版本号
- [ ] 保留 RRF 基线分数与当前协议版本分数的对比脚本
- [ ] 更新固定评测集以覆盖新增场景

**验收标准**：相关性指标有可测提升（固定评测集对比），延迟和安装体积仍在接受范围。

---

### K.8：管理、审计与生命周期

目标：用户能理解每份文档的状态，审计数据有定义的保留期。

#### K.8.1 — 文档详情完善

- [x] 文件页展示每份文档的：远传策略、索引版本、最近召回时间、引用次数
- [x] 显示统计时不泄露查询正文或用户对话内容
- [x] 过期索引/embedding version 的文档有明显标记

#### K.8.2 — Collection 批量策略

- [x] 支持按 collection 设置默认远传策略（已有策略 API，批量功能推迟到有真实需求时）

#### K.8.3 — 审计保留期定义

- [x] `recall_decisions`：保留 90 天，过期物理删除
- [x] `transmission_grants`（consumed/expired/revoked）：保留 30 天后清除
- [x] `knowledge_chat_retrievals`：保留 180 天
- [x] `knowledge_message_citations`：不自动删除（绑定消息生命周期）
- [x] 所有清理逻辑由 knowledge worker 的 60 秒空闲维护执行
- [x] 清理只影响审计记录，不删除文档、索引或引用

#### K.8.4 — 导出与备份定义

- [x] 明确审计清理仅为数据量管理，完整数据导出走独立功能（后置）

#### K.8.5 — UI 一致性验收

- [ ] 文件页影子诊断 vs 实际使用标记一致
- [ ] 检索模式切换后统计口径对应
- [ ] 删除中/索引过期/策略变更的状态标签在所有相关页面一致

**验收标准**：用户能从文件页理解每份文档何时被检索、是否可能远传、如何关闭和删除。

---

### K.9：知识库总验收与收尾

- [x] 完成 off/explicit/smart 三模式 E2E 冒烟（K.5 已有完整测试覆盖）
- [x] 完成 local/remote/unknown Provider × 三种文档策略的授权矩阵冒烟（K.4/K.5 已覆盖）
- [x] 完成授权同意、拒绝、过期、重放、策略变化和模型切换的集成测试（K.4/K.5 grant 测试）
- [x] 完成提示注入、来源变化、删除、重建、向量失败和 FTS 降级的回归（K.2/K.3 影子模式 + K.5 评测）
- [x] 完成知识与记忆隔离全链路验证（K.6 8 项专项测试）
- [x] 运行全量后端测试 + 前端构建 + Electron 检查（374 passed / 32 passed / 通过）
- [x] 更新 `BASELINE_STATUS.md`、schema 39、测试数量
- [x] 知识库优化主线已完成，`KNOWLEDGE_SYSTEM_OPTIMIZATION_PLAN.md` 中 K.6~K.9 全部勾选
- [x] worker 清理日志可观测性（K.9 添加 info log）
- [x] N15 已修复（前端测试 32/32）

**验收标准**：自然召回可用、可解释、可关闭、可授权、可追溯、可删除，不污染相处记忆。

---

## 3. 阶段二：安全债务清偿（v0.1.1 剩余项）

> 知识库收尾完成后立即启动。这些是扩展前的安全底线。

### SEC.1：API Key 加密存储

#### SEC.1.1 — SecretStore 接口

- [ ] 定义 `SecretStore` 抽象：`store(key_id, value)` / `retrieve(key_id)` / `delete(key_id)` / `has(key_id)`
- [ ] 实现 `InMemorySecretStore` 用于测试
- [ ] 实现 `SqliteSecretStore` 作为开发期兼容层（保持当前行为）
- [ ] 预留 `ElectronSafeStorage` 接口签名

#### SEC.1.2 — 业务层接入

- [ ] `providers` 的 API Key 读写全部经过 SecretStore，不直接拼 SQL
- [ ] `providers.api_key` 字段改为 `key_ref`（存储 SecretStore 中的引用键）
- [ ] API 响应中 `api_key` 字段改为 `has_key: bool`
- [ ] 日志、异常、连接测试中的密钥脱敏统一处理

#### SEC.1.3 — 旧数据迁移

- [ ] 实现迁移脚本：读取旧 `api_key` 明文 → 写入 SecretStore → 验证 → 清除旧值
- [ ] 迁移失败保留明文 + 事件记录，不丢数据
- [ ] 覆盖测试：新库初始化、旧库迁移、部分损坏恢复

#### SEC.1.4 — Electron safeStorage 实现

- [ ] 实现 `ElectronSecretStore`：使用 `safeStorage.encryptString()` / `decryptString()`
- [ ] 开发模式下可回退到 SqliteSecretStore
- [ ] 打包后强制使用 safeStorage
- [ ] 测试密钥加密/解密/迁移完整链路

**验收标准**：API Key 不以明文出现在数据库、日志、API 响应或错误信息中。

---

### SEC.2：上下文预算与长会话管理

#### SEC.2.1 — 上下文能力定义

- [ ] 为每个已知 Provider 定义上下文窗口大小（token）
- [ ] `ProviderCapability` 增加 `context_window` 字段
- [ ] 自定义 Provider 默认保守值（如 4096），用户可在设置中调整

#### SEC.2.2 — Token 计数

- [ ] 实现本地 token 估算（基于字符数 / 模型的近似方法，不需要精确 tokenizer）
- [ ] 计算系统提示词 + Lore + 记忆摘要 + 知识注入 + 历史消息的总 token
- [ ] 在 SSE 流开始前检查预算，超限时裁剪

#### SEC.2.3 — 上下文裁剪策略

- [ ] 优先保留：系统提示词 + 安全规则 + 当前用户消息
- [ ] 其次保留：最近 6 轮对话 + 明确相关的 L0 记忆
- [ ] 可裁剪：早期对话轮次、不相关的 L2 记忆
- [ ] 裁剪后在 SSE meta 中告知用户本轮裁剪了多少轮对话

#### SEC.2.4 — 长会话摘要

- [ ] 当会话超过 20 轮时，在后台生成前 10 轮的摘要
- [ ] 摘要使用当前聊天模型（非独立模型），在空闲时执行
- [ ] 摘要保存为 `session_summaries` 表，不修改原始消息
- [ ] 注入时机：当完整历史超过预算时，用摘要替换早期消息
- [ ] 摘要上标记"此为自动生成摘要，非原始对话"

**验收标准**：长会话不会无限发送全部历史，裁剪是可诊断的。

---

### SEC.3：重新生成安全化

> 部分已实现（当前版本已改为先保留旧回复再替换），需确认和补测试。

- [ ] 复核 `main.py` 中 regenerate 逻辑：确认新回复成功持久化后才替换旧消息
- [ ] 前端增加"查看上一版回复"入口（仅重新生成后可见）
- [ ] 覆盖测试：模拟网络中断 → 旧回复保留；模拟模型返回一半崩溃 → 旧回复保留
- [ ] 前端错误卡在重新生成失败时显示"旧回复已保留"

---

## 4. 阶段三：代码结构治理（v0.1.3）

> 在安全债务清偿后执行。只整理结构，不改变功能。

### STR.1：后端 main.py 拆分

#### STR.1.1 — 路由提取

- [ ] 新增 `backend/app/routers/` 目录
- [ ] `sessions.py`：会话 CRUD + 消息列表
- [ ] `chat.py`：聊天 SSE + 知识上下文 + 授权链
- [ ] `tasks.py`：任务 CRUD
- [ ] `providers.py`：Provider CRUD + 连接测试 + 模型发现
- [ ] `settings.py`：设置读取/写入
- [ ] `memory_routes.py`：记忆 API（复用 `memory.py` 逻辑）
- [ ] `knowledge_routes.py`：知识库 API
- [ ] `tools.py`：工具日志 API
- [ ] `companion.py`：伴侣状态 API

#### STR.1.2 — main.py 瘦身

- [ ] `main.py` 只保留：app 创建、CORS、中间件、lifespan、`/api/health`
- [ ] 所有路由通过 `app.include_router()` 挂载
- [ ] 全量后端测试通过且功能不变化

#### STR.1.3 — 每轮验证

- 每个子 PR 结束后运行全量后端测试
- 确认 API 路径、参数、响应结构不变
- Electron 启动冒烟

---

### STR.2：后端 db.py 整理

#### STR.2.1 — 迁移独立化

- [ ] 新增 `backend/app/migrations.py`
- [ ] 把 `MIGRATIONS` 列表和 `init_db()` 中的迁移逻辑移过去
- [ ] `db.py` 保留：连接管理、工具函数（`new_id`、`now`、`connect`）、基础表定义
- [ ] 迁移测试：新库初始化、从 schema 1 逐步升级到最新、重复迁移幂等、失败回滚

#### STR.2.2 — 常量和工具提取

- [ ] 新增 `backend/app/constants.py`：DATA_DIR、DB_PATH、token 相关常量
- [ ] `db.py` 只保留数据库操作函数
- [ ] 全量测试通过

---

### STR.3：前端 App.tsx 拆分

#### STR.3.1 — 提取导航与布局壳

- [ ] 新增 `components/workbench/WorkbenchLayout.tsx`：左侧导航 + 中央内容区 + 右侧状态栏的布局
- [ ] `App.tsx` 只保留顶层状态（view、mode、session）和 WorkbenchLayout 渲染
- [ ] 功能视觉不变，npm run build 通过

#### STR.3.2 — 提取左侧会话栏

- [ ] 新增 `components/workbench/SessionList.tsx`：会话列表、新建会话、模型切换入口
- [ ] 状态通过 props 从 App 传入
- [ ] 功能视觉不变

#### STR.3.3 — 提取顶部状态栏

- [ ] 新增 `components/workbench/TopBar.tsx`：Mode 显示、当前模型、状态胶囊
- [ ] 替换 App.tsx 中内联的顶部栏 JSX
- [ ] 功能视觉不变

---

### STR.4：前端 styles.css 拆分

#### STR.4.1 — 主题 Token 提取

- [ ] 新增 `src/theme/tokens.css`：颜色、间距、圆角、阴影、字体、动效变量
- [ ] 现有样式逐步替换硬编码值为变量引用
- [ ] 视觉不变

#### STR.4.2 — 组件样式文件

- [ ] 新增 `ChatView.css`、`RightBar.css`、`SettingsPage.css` 等组件级样式
- [ ] `styles.css` 只保留全局重置和布局壳
- [ ] 每个子 PR 构建通过且视觉不变

---

## 5. 阶段四：ToolRegistry 与工具闭环（v0.2.0 ~ v0.2.1）

> 从"聊天应用"变成"Agent"的关键阶段。

### TOOL.1：ToolRegistry 基础

#### TOOL.1.1 — 类型定义

- [ ] 新增 `backend/app/tools/types.py`
- [ ] 定义 `ToolDefinition`：name、version、description、risk_level(S0~S4)、input_schema、timeout、idempotent
- [ ] 定义 `ToolCall`：id、tool_name、parameters、status、result、started_at、finished_at
- [ ] 定义 `Approval`：tool_call_id、risk_level、scope、user_decision、decision_at

#### TOOL.1.2 — 注册表

- [ ] 新增 `backend/app/tools/registry.py`
- [ ] 工具必须显式注册（`register(ToolDefinition)`），不允许字符串动态导入
- [ ] 启动时校验：工具名唯一、version 兼容、schema 合法
- [ ] 提供 `list_tools()` / `get_tool(name)` / `execute(name, params)` 接口
- [ ] 第一阶段不接模型 function calling

#### TOOL.1.3 — 权限层

- [ ] 新增 `backend/app/tools/guard.py`
- [ ] S0 调用记录审计后直接执行
- [ ] S1 检查用户已授权的资源范围后执行
- [ ] S2 需要 ApprovalToken（一次性、绑定参数哈希、有时效）
- [ ] S3 需要 ApprovalToken + 展示完整目标与参数
- [ ] S4 默认拒绝
- [ ] 覆盖测试：S2 无 ApprovalToken 拒绝、Token 重放拒绝、Token 过期拒绝

#### TOOL.1.4 — 审计写入

- [ ] `tool_logs` 表从手动插入改为 ToolRegistry 自动写入
- [ ] 每次调用记录：tool、risk_level、parameters_summary、status、result_summary、duration_ms
- [ ] 敏感参数（路径、密钥、正文）不进入审计日志

**验收标准**：注册一个测试工具 → 可发现、可预览、S2 以上需审批、执行后产生审计记录。

---

### TOOL.2：第一批本地工具

#### TOOL.2.1 — 任务工具

- [ ] `task.create` (S1)：创建任务
- [ ] `task.list` (S0)：列出任务
- [ ] `task.start` (S1)：开始任务
- [ ] `task.complete` (S1)：完成任务
- [ ] `task.reopen` (S1)：重开任务
- [ ] `task.delete` (S2)：删除任务（需确认）

#### TOOL.2.2 — 记忆工具

- [ ] `memory.create` (S1)：创建记忆
- [ ] `memory.list` (S0)：列出记忆
- [ ] `memory.update` (S1)：修改记忆
- [ ] `memory.enable/disable` (S1)：启用/禁用记忆
- [ ] `memory.delete` (S2)：删除记忆（需确认）

#### TOOL.2.3 — 会话管理工具

- [ ] `session.rename` (S1)：重命名会话
- [ ] `session.archive` (S1)：归档会话
- [ ] `session.list` (S0)：列出会话

#### TOOL.2.4 — 系统工具

- [ ] `system.health` (S0)：健康检查
- [ ] `settings.read_safe` (S0)：读取非敏感设置

#### TOOL.2.5 — 聊天接入兼容

- [ ] 后端 API 内部改为调用 ToolRegistry（原按钮和 API 行为不变）
- [ ] 增加非流式的本地命令格式（如 `/task create 整理周报`）
- [ ] 最后才接入模型的 function calling（需 Provider 能力检查）

**验收标准**：从 UI 按钮、API 和命令三种方式触发同一工具，行为一致。

---

### TOOL.3：前端工具展示

#### TOOL.3.1 — 工具调用卡

- [ ] 聊天消息中渲染工具调用卡：工具名、目的、参数摘要、风险等级
- [ ] 执行中显示状态和动画
- [ ] 完成后显示结果摘要 + 审计详情入口

#### TOOL.3.2 — 确认卡组件

- [ ] 高风险工具（S2+）弹出确认卡：工具名、风险等级、参数摘要、允许/拒绝/修改参数
- [ ] 拒绝后工具不执行，模型收到拒绝通知
- [ ] 确认有过期时间，过期自动拒绝
- [ ] **不允许**一键永久全开

#### TOOL.3.3 — 工具日志页升级

- [ ] 从纯展示页升级为可搜索、可筛选的审计视图
- [ ] 显示真实调用记录（不再只是占位说明）

---

## 6. 阶段五：UI 伴侣化深化

> 在核心能力稳定后进行。目标：让主窗口更像伴侣，而非工程控制台。

### UI.1：聊天气泡视觉升级

#### UI.1.1 — 气泡样式

- [ ] 用户消息和遐蝶消息使用不同的气泡样式（颜色、对齐、圆角）
- [ ] 头像占位（遐蝶侧用 Live2D 缩略图或蝴蝶图标，用户侧用默认头像）
- [ ] 气泡支持长文本、代码块、列表的正确渲染
- [ ] 复制按钮位置和样式优化

#### UI.1.2 — 工具调用卡嵌入气泡

- [ ] 工具调用过程和结果以嵌入卡片形式出现在气泡流中
- [ ] 知识引用以脚注形式显示在遐蝶回复下方

#### UI.1.3 — 贴纸与文件按钮

- [ ] 输入区增加贴纸按钮（`disabled`，提示"贴纸功能即将开放"）
- [ ] 输入区增加文件按钮（`disabled`，提示"文件发送即将开放"）
- [ ] 不做贴纸选择器、不做文件上传功能

---

### UI.2：设置页完善

#### UI.2.1 — Provider 能力标签

- [ ] 后端 `ProviderCapability` 增加可靠字段（不依赖模型名猜测）：`supports_streaming`、`supports_tools`、`supports_vision`、`supports_reasoning`
- [ ] 前端设置页显示能力标签（带 tooltip 说明）
- [ ] 连接测试成功时更新能力

#### UI.2.2 — Live2D 设置持久化

- [ ] 显示/隐藏、置顶、穿透、缩放、透明度、气泡开关写入后端设置
- [ ] 前端读取设置并生效
- [ ] IPC 联动：前端设置变化 → 通知 Electron 桌宠窗口更新行为

#### UI.2.3 — 数据页导出入口

- [ ] 数据页显示会话数、记忆数、任务数、知识文档数、存储占用
- [ ] "导出数据"按钮（`disabled`，提示"数据导出功能开发中"）
- [ ] "清除数据"按钮（`disabled`，提示"需二次确认，即将开放"）

---

### UI.3：空状态与错误状态统一

- [ ] 空会话："在输入框中写下你想说的话，遐蝶在这里陪你"
- [ ] 无任务："今天还没有任务，对遐蝶说「帮我记一个任务」试试"
- [ ] 无记忆："相处久了，遐蝶会在这里记住关于你的事情"
- [ ] 无知识文件："拖入或选择文件，遐蝶可以帮你查阅资料"
- [ ] API 错误：显示错误码 + 友好文案 + 操作建议（不隐藏错误码）
- [ ] 网络断开：显示"与遐蝶的连接似乎断开了"+ 重试按钮

---

## 7. 阶段六：文件工作区与产物管理（v0.3.0 ~ v0.3.1）

> 在工具闭环完成后启动。让 Agent 能安全地读取和编辑文件。

### FILE.1：安全文件工作区

#### FILE.1.1 — 目录授权

- [ ] 用户通过 Electron 原生目录选择器选择工作目录
- [ ] 授权记录保存规范化路径、时间和作用范围
- [ ] 所有子路径 resolve 后必须仍在授权根目录
- [ ] 路径逃逸、符号链接逃逸、设备路径必须拒绝
- [ ] 覆盖测试：`../`、绝对路径、`/etc/passwd`、Windows 盘符跨越

#### FILE.1.2 — 只读文件操作

- [ ] `file.list_dir` (S1)：列出目录
- [ ] `file.read_text` (S1)：读取文本文件（限定扩展名、大小、编码）
- [ ] `file.stat` (S0)：读取文件元数据
- [ ] 二进制文件只展示元数据，不作文本解析

#### FILE.1.3 — 文件写入

- [ ] `file.write_text` (S2)：编辑/创建文本文件
- [ ] 保存前展示路径、变更摘要和风险
- [ ] 原子写入：临时文件 → 落盘 → 替换
- [ ] `file.delete` (S2)：删除（先进入应用回收区）
- [ ] 所有写入、删除进入审计日志

**验收标准**：不能越过工作区访问文件，取消确认后无副作用，外部修改保存时提示冲突。

---

### FILE.2：产物管理

- [ ] 定义 `Artifact` 对象：id、task_id、type、local_path、checksum、created_by_tool、version
- [ ] 输出默认进入应用产物区
- [ ] 重新生成形成新版本，不覆盖旧版本
- [ ] 支持预览、打开目录、另存为、删除
- [ ] 产物可追溯到任务、工具和输入来源

---

## 8. 阶段七：TaskRun 执行工作台（v0.5.0 ~ v0.5.1）

> 需要 ToolRegistry + 权限内核 + 文件工作区稳定后启动。

### TASKRUN.1：核心对象与状态机

- [ ] `TaskRun`：一次具体执行
- [ ] `TaskNode`：计划步骤
- [ ] 状态机：draft → planning → awaiting_approval → running → paused → completed/failed/cancelled
- [ ] 每个状态定义允许的下一状态
- [ ] 覆盖测试：非法状态跳转被拒绝

### TASKRUN.2：工作台 UI

- [ ] 任务详情页：计划、步骤、进度、当前动作
- [ ] 工具调用卡和确认卡嵌入工作流
- [ ] 暂停/继续/取消/重新规划按钮
- [ ] 全局急停入口

### TASKRUN.3：恢复

- [ ] 重启后恢复未完成 TaskRun
- [ ] 区分可重试、需重规划、需人工介入
- [ ] 幂等键避免重复外发和重复产物

---

## 9. 阶段八：Planner / Executor / Verifier（v0.5.1）

> TaskRun 稳定后启动。Agent 首次拥有"规划-执行-验证"闭环。

- [ ] Planner：目标 → 有限步骤，每步声明输入/输出/工具/风险
- [ ] Executor：只执行已批准且依赖满足的步骤
- [ ] Verifier：根据完成条件检查结果，不确定时标记"需人工确认"
- [ ] 规划长度、重试次数和成本有硬上限
- [ ] 失败不透传权限或跳过验证

---

## 10. 阶段九：外部能力（v0.6.0 ~ v0.8.0）

> 以下为远景规划，每阶段开工前需要独立 review 和 ADR。

### v0.6.0：搜索与浏览器

- 搜索工具（web search API）
- 浏览器只读：打开、读取、查找、截图
- 浏览器受限写入：点击、输入、下载（进入隔离区）
- Prompt Injection 防护

### v0.6.1：办公与数据工具

- CSV/JSON 分析转换
- XLSX 读取生成
- DOCX 创建编辑
- PDF 提取生成

### v0.7.0：外部连接与消息

- Connector 统一接口
- 只读 → 草稿 → 确认发送 → 逐步开放
- 外发确认卡展示目标平台、收件人、内容和风险

### v0.8.0：受控桌面自动化

- DesktopObserver + DesktopExecutor + PolicyGuard + EmergencyStop
- 先列窗口 → 截图 → 单次点击 → 文本输入 → 多步骤流程
- 密码框/支付/系统设置默认禁止
- 全局急停快捷键

---

## 11. 阶段十：多 Agent Worker 化（v0.9.0）

> 以下为长期远景。只有在单主控稳定后才能开始。

- 第一批 Worker：KnowledgeAgent、FileDataAgent、VerifierAgent
- Worker 不直接面对用户，不拥有永久权限
- Scheduler 控制并发、超时、资源租约
- 协作通过结构化事件和产物，不依赖隐式聊天上下文

---

## 12. V1.0 发布门槛

- [ ] 桌宠和单主窗口体验稳定
- [ ] 聊天、记忆、任务、知识、工具形成闭环
- [ ] 至少一套多步骤工作流通过验收
- [ ] API Key 使用安全存储
- [ ] Live2D 模型替换为授权清晰可再分发的资产
- [ ] Windows 安装/升级/卸载/数据保留验证
- [ ] 代码签名和自动更新策略

---

## 13. 当前执行优先级

按紧迫顺序排列：

| 优先级 | 编号 | 事项 | 理由 |
|---|---|---|---|
| **P0** | K.6 | 知识与记忆隔离 | 正在施工，直接影响数据质量 |
| **P0** | SEC.1 | API Key 加密存储 | 安全红线 |
| **P1** | K.7 | 检索质量第二阶段 | 知识库核心体验 |
| **P1** | K.8 | 审计与生命周期 | 知识库可维护性 |
| **P1** | K.9 | 知识库总验收 | 完成当前施工主线 |
| **P2** | SEC.2 | 上下文预算 | 防止长会话溢出 |
| **P2** | STR.1 | main.py 拆分 | 降低后续改动成本 |
| **P3** | TOOL.1 | ToolRegistry 基础 | Agent 从聊天到行动的关键 |
| **P3** | STR.3 | App.tsx 拆分 | 降低前端改动成本 |
| **P4** | UI.1~3 | UI 伴侣化深化 | 体验提升 |

---

## 14. 旧文档处理

以下文档已被本计划替代，应标记为"历史参考"不再作为执行依据：

- `遐蝶Agent_Codex_PR拆分执行计划_v0.6.2.md`
- `遐蝶Agent_v0.2UI回归与Cyrene风格融合迁移方案_v0.6.1.md`

以下文档仍有效，与本计划互补：

- `CODEX_PROJECT_CONTEXT.md`：冻结决策与规则
- `BASELINE_STATUS.md`：当前基线数据
- `XIADIE_LONG_TERM_ROADMAP.md`：长期版本路线
- `KNOWLEDGE_SYSTEM_OPTIMIZATION_PLAN.md`：K.6~K.9 施工详情（与阶段二重叠部分以本计划为准）
- `MEMORY_SYSTEM_DESIGN_FOR_BEGINNERS.md`：记忆系统架构说明

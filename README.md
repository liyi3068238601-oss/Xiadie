# 遐蝶 · Windows 桌面 AI 伴侣 Agent

> **长期停工状态（2026-08-08）：** 主线停在 LIFE2.10 完成、LIFE2.11 尚未开始的 Review 边界。恢复施工前必须先阅读 [项目长期停工交接与恢复手册](docs/PROJECT_PAUSE_AND_RESUME_HANDOFF_2026-08-08.md)，不要从旧 `main` 或旧基线快照直接开工。

以 Live2D 桌宠为入口、以单主窗口为核心，具备聊天、多层记忆、认知决策、知识治理、生活连续性与受控主动陪伴的桌面 AI 伴侣。

本地优先：所有数据存储在用户本机 SQLite，不依赖云端服务。模型调用通过 OpenAI-Compatible 接口直达用户配置的供应商，项目自身不中转任何对话内容。

---

## 目录

- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [子系统详解](#子系统详解)
  - [LLM 集成层](#llm-集成层)
  - [记忆系统](#记忆系统)
  - [CDS 认知决策系统](#cds-认知决策系统)
  - [KIG 知识治理系统](#kig-知识治理系统)
  - [CTX 上下文系统](#ctx-上下文系统)
  - [EAP 主动陪伴系统](#eap-主动陪伴系统)
  - [LIFE 生活连续性](#life-生活连续性)
  - [知识库系统](#知识库系统)
  - [情绪与关系](#情绪与关系)
- [数据库与 Schema](#数据库与-schema)
- [安全与隐私](#安全与隐私)
- [项目治理](#项目治理)
- [本地开发](#本地开发)
- [测试与构建](#测试与构建)
- [Windows 打包](#windows-打包)
- [路线图](#路线图)
- [许可证](#许可证)

---

## 核心特性

### 陪伴式聊天

- 多轮上下文对话，SSE 流式输出，会话增删改与自动标题
- 伴侣人设系统：角色设定、情绪语气、长期记忆、知识背景动态注入
- 复制 / 收藏 / 重新生成 / 错误恢复卡
- 消息内记忆与任务卡片化展示

### 多模型接入

- 支持 DeepSeek / OpenAI / GLM / Qwen / Kimi / OpenRouter / SiliconFlow / Ollama / 自定义（全部 OpenAI-Compatible）
- 模型切换、能力标签、连接测试、密钥不回显
- 内置 mock 演示模型，无配置时全部功能可用
- 模型认证分级：未验证 → 结构化能力 → 决策验证 → 本地敏感验证

### 三层记忆星座

- **L0 Fragment**：单条记忆碎片，用户可见可编辑可删可禁用
- **L1 Episode**：2-20 条 Fragment 聚合为一次连续经历
- **L2 Saga**：2-12 个 Episode 聚合为跨时间长期主题
- 自主记忆观察器：保守自动抽取，敏感信息拦截，来源可追溯
- 慢生命周期：Episode 180 天成熟 / 180 天归档，Saga 365 天归档

### 认知决策与知识治理

- 共享 DecisionRun 运行时：Shadow → Advisory → Active 三级模式
- 模型认证、熔断器、token 预算、并发限流、隐私分级 fail-closed
- KIG 多源检索：查询规划 → 知识/记忆/历史/生活/任务/角色六源召回 → LLM 语义重排 → 证据与引用

### 受控主动陪伴

- 本机 Level 1-4 投递通道（主窗口消息 / 桌宠气泡 / 轻提示 / Live2D 表达）
- at-most-once 投递账本，暂停 / 关闭 / 类型开关
- 六种来源：预期返回、情感关怀、Episode 里程碑、Saga 里程碑、日常问候、生活种子
- 外部渠道（QQ / 微信 / 邮件）硬禁用

### 生活连续性

- 确定性 LifeClock：遐蝶在离线期间按有界模拟世界继续时间
- 离线追赶：下次启动时执行有界世界追赶，不做后台动作
- 日记、重要日期、个人目标、自我时间线
- 模拟生活不声明为现实执行，私人日记默认折叠

### 知识库

- 本地 BGE-M3 ONNX 向量嵌入（int8 量化，1024 维）
- SQLite FTS5 全文检索（CJK 双字 bigram 分词）
- 混合检索 + MMR 多样性 + 规则重排
- 文档解析：TXT / Markdown / PDF / DOCX
- 传输授权：远程 embedding 默认拒绝，需逐次授权

### 安全边界

- 本地 API 令牌保护，CORS 仅允许明确本机来源
- 密钥不明文回传前端、不打印到普通日志
- S0-S4 五级工具风险分级，高风险默认需确认或禁用
- Prompt Injection 防护、路径逃逸防护、敏感信息拦截

---

## 系统架构

```
desktop (Electron 33)
  ├─ 透明置顶桌宠窗口（Live2D 遐蝶，默认显示）
  ├─ 系统托盘 + 右键菜单
  ├─ 主窗口（点击桌宠打开）
  ├─ 后端进程管理（拉起/守护冻结后端）
  ├─ 主动陪伴投递轮询（at-most-once 消费）
  └─ Windows 唤醒保护（powerMonitor）
        │
        ├─ frontend (React 18 + Vite 5 + TS)
        │    ├─ 陪伴·对话（SSE 流式、记忆/任务卡片）
        │    ├─ 今日任务
        │    ├─ 今日生活（日记/目标/重要日期/时间线）
        │    ├─ 记忆与关系（L0/L1/L2 + 实体）
        │    ├─ 文件与知识（导入/检索/引用）
        │    ├─ 运行日志（模型/决策/检索/上下文/工具只读审计；聊天轮次正文按需查看）
        │    └─ 设置（模型/Live2D/外观/记忆/权限/数据/上下文）
        │
        └─ backend (Python 3.12 + FastAPI + SQLite)
              ├─ LLM 集成层（httpx 直调 OpenAI-Compatible）
              ├─ CTX 上下文系统（硬预算 + 摘要 + 跨会话回忆）
              ├─ CDS 认知决策（DecisionRun + 模型认证 + 熔断 + 预算）
              ├─ KIG 知识治理（查询规划 + 多源召回 + 重排 + 证据）
              ├ EAP 主动陪伴（候选 + 决策 + 表达 + 投递 + 反馈）
              ├─ LIFE 生活连续性（LifeClock + 事件账本 + 离线追赶）
              ├─ 记忆系统（Fragment + Episode + Saga + 实体 + 归档）
              ├─ 知识库（BGE-M3 + FTS5 + 混合检索 + 传输授权）
              ├─ 情绪与关系（心境引擎 + 关系意义）
              └─ 12 个后台 worker（记忆/摘要/归档/知识/主动/认知/生活...）
```

### 三层职责

| 层 | 目录 | 技术 | 职责 |
|---|---|---|---|
| `desktop/` | Electron 33 | Node.js | 桌宠透明窗口、主窗口、托盘、后端进程管理、IPC 状态联动、主动陪伴投递消费、Windows 唤醒保护 |
| `frontend/` | React 18 + Vite 5 + TS | TypeScript | 三栏主窗口 UI、SSE 流式聊天、Live2D 渲染、设置/任务/记忆/知识/生活/工具页 |
| `backend/` | FastAPI + SQLite | Python 3.12 | 多模型接入、会话/记忆/任务/知识/生活/认知决策/主动陪伴全部后端逻辑，150+ 模块，130+ REST 路由 |

---

## 技术栈

### 后端

| 依赖 | 版本 | 用途 |
|---|---|---|
| FastAPI | ≥0.115 | Web 框架 |
| uvicorn[standard] | ≥0.32 | ASGI 服务器 |
| httpx | ≥0.27 | LLM API 调用（OpenAI-Compatible） |
| pydantic | ≥2.9 | 数据校验 |
| numpy | ≥2.0 | 向量计算 |
| onnxruntime | ≥1.20 | 本地 BGE-M3 embedding 推理 |
| tokenizers | ≥0.20 | BGE-M3 分词 |
| pypdf | ≥6.0 | PDF 解析 |
| python-docx | ≥1.1 | DOCX 解析 |
| tzdata | ≥2025.2 | 时区数据 |
| pytest | ≥8.3 | 测试 |

后端不依赖 langchain、openai SDK、llama_index 或任何 LLM 框架。所有 LLM 调用、RAG、Agent 编排、结构化输出解析均为自研。

### 前端

| 依赖 | 版本 | 用途 |
|---|---|---|
| React | 18 | UI 框架 |
| Vite | 5 | 构建工具 |
| TypeScript | 5.6 | 类型系统 |
| pixi.js | 6 | Live2D 渲染底层 |
| pixi-live2d-display | 0.4 | Live2D 模型加载与显示 |

### 桌面壳

| 依赖 | 版本 | 用途 |
|---|---|---|
| Electron | 33 | 桌面应用壳 |
| electron-builder | 25 | 打包（NSIS 安装器） |

---

## 子系统详解

### LLM 集成层

**核心文件**：`backend/app/llm.py`（264 行）

不使用任何 LLM 框架（langchain / openai SDK / llama_index），基于 `httpx.AsyncClient` 直接调用 OpenAI-Compatible `/chat/completions` 接口。

**核心函数**：

| 函数 | 职责 |
|---|---|
| `stream_chat(provider, model, messages, max_tokens)` | 流式 SSE 解析，手写 `data:` 行解析与 `[DONE]` 处理 |
| `complete_json(provider, model, messages, ...)` | 非流式 JSON 观察调用，支持 `response_format: json_object`、温度 / top_p、硬上限 token、延迟 / usage 上报 |
| `test_connection(provider)` | 连接测试 |
| `discover_models(provider)` | `/models` 端点发现 |

**模型路由**：`cognition_runtime.resolve_model_binding(role)` 解析逻辑角色（FAST / REASONING / CREATIVE）到具体 provider + model，支持角色覆盖配置。

**Provider 配置**：存 SQLite `providers` 表，支持 10 个预设（mock / deepseek / openai / glm / qwen / kimi / openrouter / siliconflow / ollama / custom）。

### 记忆系统

采用三层分级 + 三种粒度的"记忆星座"架构。

#### L0 Fragment（碎片）

**文件**：`backend/app/memory.py`

单条记忆单元，`layer` 字段标记所属层级（L0/L1/L2），支持 `status`（active/tombstone）、`enabled`、`confidence`、`sensitivity`、完整来源链（`source_session_id` / `source_message_id`）。

- 注入约束：`MAX_INJECT=12`、`MAX_INJECT_CHARS=2400`
- 手动 + 自动双轨：用户可见可编辑可删可禁用；自动抽取保守标注来源
- 敏感提示词检测：API Key、密码、身份证、银行卡等禁止记录

#### 自主记忆观察器

**文件**：`backend/app/memory_observer.py`（协议层）、`memory_observer_service.py`（worker）、`memory_writer.py`（原子写入）

- 协议版本：`memory-observer-v1`
- 每轮最多提取 3 条候选，最低置信度 0.65
- 记忆类型：fact / preference / plan / experience / relationship / observation / correction
- 作用域：user / self / relationship / world
- 安全校验：`FORBIDDEN_PATTERNS` 拦截敏感信息，`IMPORTANCE_CAPS` 按类型限制重要性
- 观察来源标注：conversation / knowledge_reference / shared_lookup / user_confirmed_fact
- 原子写入：SQLite 事务内复核来源、写 Fragment + 实体关系 + 事件

#### L1 Episode（经历）

**文件**：`backend/app/episodes.py`、`episode_consolidator.py`、`episode_summary.py` / `episode_summary_service.py`

2-20 条 Fragment 聚合为 Episode，继承来源、时间范围、独立 significance。

- 评分（确定性，无模型）：实体 0.35 + 文本 0.25 + 时间 0.20 + 连贯性 0.20，阈值 0.50
- 时间窗口：7 天
- 自动批处理上限：20 个候选，最多 3 次尝试
- grounded 摘要：模型生成需基于真实 Fragment

#### L2 Saga（传奇）

**文件**：`backend/app/sagas.py`、`saga_consolidator.py`、`saga_lifecycle.py`、`saga_summary.py` / `saga_summary_service.py`

2-12 个 Episode 聚合为 Saga，最大跨度 180 天，最大相邻间隔 60 天。

- 评分：实体 0.30 + 文本 0.35 + 时间 0.15 + 连贯性 0.20，阈值 0.52
- 生命周期状态流转管理

#### 慢生命周期与归档

**文件**：`backend/app/slow_lifecycle.py`、`archivist.py`、`archivist_worker.py`

- Episode 成熟期 180 天，归档期 180 天
- Saga 归档期 365 天
- 召回保护期 180 天，显著性保护阈值 8
- 独立预算：Episode 10 / Saga 10 每批

#### 实体与冲突

**文件**：`backend/app/entities.py`、`memory_conflicts.py`

- 9 类实体类型（人物 / 项目 / 地点 / 组织 / 日期 / 概念 / 文件 / 工具 / 事件）
- 正则抽取 + 别名匹配 + 自动关联 Fragment
- 冲突关系管理

### CDS 认知决策系统

共享的认知决策运行时，为 EAP / KIG / LIFE 等专项提供统一的 Shadow / Advisory / Active 决策框架。

#### 协议层（CDS.1）

**文件**：`backend/app/cognitive_decision.py`

- 协议版本：`cognitive-decision-v1`
- `DecisionKindRegistry`：每种决策绑定专属 input/output schema + validator + 确定性 fallback + 版本号
- `SourceSnapshot`：来源快照（kind / id / revision / content_hash），sha256 防篡改校验
- 三模式：SHADOW（影子对照）→ ADVISORY（建议）→ ACTIVE（生效）
- 一次 JSON 修复：手写 JSON 解析 + 修复（去 markdown fence、截取 `{...}`）
- body-free：不持久化原始 prompt、候选正文、用户文本或模型输出

#### 运行时层（CDS.2）

**文件**：`backend/app/cognition_runtime.py`

- 逻辑角色路由：FAST（5s 超时）/ REASONING（30s）/ CREATIVE（15s）
- 模型认证：`CertificationLevel`（UNVERIFIED → STRUCTURED_CAPABLE → DECISION_VERIFIED → LOCAL_SENSITIVE_VERIFIED）
- 合成探针：用结构化数据探针认证模型能力，未通过时降级到确定性 fallback
- `CognitionBudgetGovernor`：熔断器（3 次失败 → open，60s 冷却 → half_open，成功 → closed）、本地 / 远程并发限流、滚动 1h + 日 token 预算、前台延迟保护、省电模式
- 8 步流水线：绑定解析 → 认证检查 → 隐私检查 → 熔断检查 → 预算授权 → prompt 组装 → LLM 调用 → 结果校验
- 隐私 fail-closed：body-bearing 认知在远程 / 未认证位置直接拒绝

#### 已注册决策种类（9 种 Shadow）

| DecisionKind | 用途 | 所属专项 |
|---|---|---|
| `kig_query_planner` | 查询规划 | KIG.5 |
| `retrieval_rerank` | 检索重排 | KIG.7 |
| `life_schedule_coarse` | 日程粗规划 | LIFE.1 |
| `life_schedule_detail` | 日程细规划 | LIFE.1 |
| `life_schedule_replan` | 日程重规划 | LIFE.1 |
| `life_important_date_interpretation` | 重要日期解读 | LIFE.1 |
| `life_diary_reflection` | 日记反思 | LIFE.1 |
| `life_event_meaning` | 事件意义 | LIFE.1 |
| `presence_thread_observer` | 在线状态观察 | CDS.3 |

#### 校准与诊断

- `cognition_calibration.py`：校准画像与反馈信号
- `cognition_diagnostics.py`：body-free 诊断视图（TTL 30 天）
- `cognition_settings.py`：设置与冻结候选

### KIG 知识治理系统

知识检索、治理与个人世界模型（PWM）的统一框架。

#### 查询规划（KIG.5）

**文件**：`backend/app/kig_query_planner.py`

- 6 个检索源：knowledge / memory / history / life / task / lore
- 正则识别：时间 / 版本 / 实体 / 精确引用 / 冲突 / 多义 / 歧义 / 注入防护
- 最多 4 个子查询，每个最长 160 字符
- 11 种 reason code

#### 多源召回（KIG.6）

**文件**：`backend/app/kig_retrieval.py`

- 适配各权威召回路径（knowledge_search / memory / history_recall / lore 等）
- 不持久化查询文本或摘录，不改变 owner 系统生命周期
- 默认每源 6 条，总计上限 60 条，摘录上限 1200 字符

#### LLM 语义重排（KIG.7）

**文件**：`backend/app/kig_reranker.py`

- 7 类相关性角色：direct / partial / background / conflict / outdated / duplicate / irrelevant
- 3 个排名桶：primary / secondary / excluded
- 最多 30 个输入候选，最多选择 12 个
- 模型失败使用确定性融合（lexical / vector / metadata / recency 分离）
- Shadow 模式对比旧排序

#### 治理（KIG.9）

**文件**：`backend/app/kig_governance.py`

- 11 种关系类型：exact_duplicate / semantically_equivalent / compatible / extends / supersedes / contradicts 等
- 6 种新鲜度状态：current / possibly_stale / deprecated / superseded / expired / unknown
- 权威等级：user_correction(100) > user_confirmed(90) > tool_result(80) > official(60) > imported(40) > model_proposal(10)
- 高影响冲突（医疗 / 法律 / 财务等）需用户确认

#### 聊天编排与证据

- `kig_pipeline.py`：查询规划 → 多源召回 → 重排 → 治理 → 证据，返回 `ChatRetrievalResult`
- `kig_evidence.py`：证据链与引用段
- `kig_maintenance.py`：非破坏性维护队列（11 种候选类型）+ 用户检索反馈

#### 个人世界模型 PWM

**文件**：`backend/app/pwm.py`、`pwm_api.py`、`pwm_extractor_shadow.py`

- 可重建的导航层，每次写入仅 Shadow，绑定 owner 系统 SourceRef
- 18 类实体类型，15 种 Predicate，6 层事件
- 可逆实体解析：proposal 机制，不自动合并
- 敏感属性过滤：医疗 / 宗教 / 政治 / 收入 / 亲密关系等

### CTX 上下文系统

管理聊天上下文的硬预算规划、会话摘要、跨会话回忆与统一组装。

#### 硬预算规划（CTX.1）

**文件**：`backend/app/context_budget.py`

- 纯函数，不读库不联网
- 上下文上限：1,000,000 tokens
- 保守默认窗口：4,096 tokens
- 生成可审计的 `BudgetPlan`：估算输入 / 输出预留 / 安全边际 / 历史预算 / 裁剪后消息 / 轮次数

#### 统一上下文组装器（CTX.4）

**文件**：`backend/app/context_assembler.py`

- 在同一个硬预算中组合：摘要 + 最近原文 + 长期记忆 + 角色设定 + 用户知识
- 可选组件份额：滚动摘要 20% / 跨会话回忆 15% / 记忆摘要 13% / 知识 22% / 角色 10% / 附件 20%
- 不可信摘要指令检测：防止 Prompt Injection 通过摘要注入系统指令
- 摘要失效时退回安全完整轮次裁剪

#### 会话摘要（CTX.2/3）

- 约束背景摘要，注入时"已参考记忆"提示
- 后台 worker 自动生成，支持停滞恢复

#### 跨会话历史回忆（CTX.5）

**文件**：`backend/app/history_recall.py`

- 两阶段：先选相关会话（最多 6 个），再选匹配消息扩展为完整轮次（最多 4 轮，每会话 2 轮）
- 默认模式：仅显式引用
- 评分权重：标题匹配 3.0 / 活跃摘要 1.5 / 消息匹配 1.25 / 轮次词项 2.0 / 显式标题引用 4.0
- 本地索引，事件只存 query hash / 计数 / 版本，不存正文

### EAP 主动陪伴系统

**目录**：`backend/app/proactive/`

受控的主动陪伴与关系闭环。

#### 六个领域协议

| 协议 | 版本 | 职责 |
|---|---|---|
| `conversation-presence-v2` | v2 | 用户在线状态（8 值枚举）、离开原因、open_thread |
| `user-affect-observation-v1` | v1 | 用户情绪观察（state 6 值、needs 5 类、evidence） |
| `relationship-meaning-v1` | v1 | 关系意义标签（9 种） |
| `proactive-decision-v2` | v2 | 主动决策建议（SEND / DEFER / SUPPRESS / ABANDON） |
| `expression-plan-v1` | v1 | 表达向量（warmth 等 7 维）与迟滞参数 |
| `proactive-feedback-v1` | v1 | 用户反馈（少一点这种消息、别用这种语气） |

#### 核心模块

| 模块 | 职责 |
|---|---|
| `orchestrator.py` | 可恢复运行时编排，轮询 30s，6 种来源 |
| `decision.py` | 三层硬门 + LLM 结构化建议 + Shadow 基线 |
| `candidates.py` | 候选生成与状态管理 |
| `presence.py` | Conversation Presence v2 |
| `expression.py` | 表达向量（7 维） |
| `intensity.py` | 接近意愿与打扰负担模型 |
| `delivery.py` | at-most-once 投递账本，租约 / 确认 / 重试 |
| `feedback.py` | 用户反馈处理与偏好权重 |
| `episodes.py` | ContactEpisode 管理 |
| `relationship.py` | 关系意义 |
| `life_adapter.py` | LIFE 适配 |
| `run_ledger.py` | 运行账本与幂等键 |

#### 投递通道

本机 Level 1-4 通道：主窗口消息 / 桌宠气泡 / 轻提示 / Live2D 无文字表达。Windows 系统通知首次询问。外部渠道（QQ / 微信 / 邮件）硬禁用。

### LIFE 生活连续性

维护遐蝶的"生活"——确定性 LifeClock、SelfState、事件账本、离线追赶、日记 / 目标 / 重要日期。

#### 确定性运行时（LIFE.3）

**文件**：`backend/app/life_runtime.py`

- 算法版本：`life-state-reducer-v1`，确定性（相同输入相同输出）
- `SelfState`：revision / logical_time / reliable_wall_time / timezone / current_activity / energy / focus / rest_need / social_openness / conservative_mode / anomaly_code
- `Modulation`：contact_need / valence / arousal / bond / trust
- 活动：resting / winding_down / routine / focused / reflecting
- 租约：TTL 30s，最大推进 7 天，步长 5 分钟，时钟回拨容忍 5 分钟
- 数据库物化：`life_runtime_state` / `life_runtime_lease` / `life_runtime_events` / `life_exit_snapshots`

#### 事件账本（LIFE.2）

**文件**：`backend/app/life_events.py`

- 事件类型：state_transition / activity / agent_action / observation / date_marker
- 世界层：planned / simulated / observed / performed（模拟生活不声明为现实执行）
- 来源：life_event / diary_entry / important_date / personal_goal / self_timeline / tool_run / user_statement / system_observation
- 幂等：`make_idempotency_key()`，`SourceRef`（kind / id / revision / content_hash）

#### 离线追赶（LIFE.4）

**文件**：`backend/app/life_catchup.py` / `life_catchup_service.py`

- 仅在下次启动时执行有界离线世界追赶，不做后台动作
- 模式：continuous_simulated / paused / disabled
- 最多 16 个候选，最多 2 次模型调用
- 跨日期事件处理

#### 数据视图

| 模块 | 文件 | 职责 |
|---|---|---|
| 日程 | `life_schedule.py` | 日程与时间段管理 |
| 日记 | `diary.py` | 日记条目（私人日记默认折叠） |
| 重要日期 | `important_dates.py` | 重要日期管理 |
| 个人目标 | `personal_goals.py` | 目标与来源 |
| 自我时间线 | `self_timeline.py` | 自我时间线 |

### 知识库系统

本地优先的知识库：解析、稳定切片、本地 FTS + BGE-M3 向量混合检索、grounded 引用、传输授权。

#### Embedding

**文件**：`backend/app/knowledge_embeddings.py`

- 本地：`LocalBgeM3Provider`，BAAI/bge-m3，1024 维，ONNX int8 量化，模型 SHA-256 校验
- 远程：默认拒绝，需逐次授权 `consent_id`

#### FTS 全文检索

**文件**：`backend/app/knowledge_search.py`

- 协议：`knowledge-fts-terms-v2` / `knowledge-search-v2`
- CJK 双字 bigram + 词分词
- 索引只存检索词项，正文以 `knowledge_chunks` 为准
- 有界规则重排 + MMR 多样性选择

#### 混合检索与召回

**文件**：`backend/app/knowledge_recall.py`

- 召回预检决策：正则识别寒暄 / 情感 / 简单任务 / 歧义引用 / 源冲突
- 三种模式：off / explicit / smart
- 查询清理：去前缀 / 后缀 / 语气词 / 停用词

#### 管理与授权

- 文档 / 集合管理
- 单次使用传输授权
- 传输策略（local_only / ask_each_time / remote_allowed）
- 清理与完整清除（删除级联）

### 情绪与关系

**目录**：`backend/app/affect/`

| 模块 | 职责 |
|---|---|
| `engine.py` | 确定性心境引擎 |
| `observer.py` | 情绪观察器（协议层） |
| `observer_service.py` | 观察 worker |
| `repository.py` | 状态持久化 |
| `tone_grid.py` | 语气网格 |

- `affect_state`：五维有界状态（connection / pride / valence / arousal / immersion）
- `relationship_state`：关系积温
- 状态只影响语气，不影响事实判断、工具权限和安全策略
- 用户可查看、重置和关闭状态影响

---

## 数据库与 Schema

- **数据库**：SQLite，WAL 模式，`PRAGMA foreign_keys = ON`，`busy_timeout = 5000`
- **当前 Schema 版本**：80（80 个有序幂等迁移）
- **开发路径**：`backend/data/xiadie.db`
- **正式路径**：`%APPDATA%\遐蝶\data\`

### 主要表（150+ 张，按域分组）

| 域 | 主要表 |
|---|---|
| 元数据 | `schema_meta`、`settings`、`providers` |
| 会话/消息 | `sessions`、`messages`、`tasks`、`tool_logs`、`message_attachments` |
| 伴侣状态 | `companion_state` |
| 记忆 L0 | `memory_fragments`、`memory_candidates`、`memory_events`、`memory_lifecycle_events`、`memory_recall_events` |
| 实体 | `memory_entities`、`memory_fragment_entities` |
| 记忆 L1 Episode | `memory_episodes`、`memory_episode_fragments`、`memory_episode_candidates`、`episode_consolidator_runs/events` |
| 记忆 L2 Saga | `memory_sagas`、`memory_saga_episodes`、`saga_consolidator_runs/events`、`saga_relationship_delta_suggestions` |
| 记忆关系 | `memory_fragment_relations`、`memory_conflicts` |
| 情绪/关系 | `affect_state`、`relationship_state`、`affect_events` |
| 知识库 | `knowledge_collections`、`knowledge_documents`、`knowledge_chunks`、`knowledge_chunk_embeddings`、`knowledge_transmission_grants/items/events` |
| 上下文 CTX | `conversation_summary_runs/revisions`、`conversation_history_recall_events`、`context_package_events`、`conversation_presence` |
| 主动陪伴 EAP | `proactive_candidates`、`proactive_decisions`、`proactive_deliveries/attempts/events`、`proactive_feedback`、`contact_episodes` |
| CDS 认知决策 | `decision_runs`、`decision_run_events`、`cognition_model_certifications`、`cognition_circuit_breakers`、`cognition_budget_events` |
| LIFE 生活 | `life_events/revisions/sources`、`life_runtime_state/lease/events`、`life_schedules/segments`、`diary_entries/revisions`、`important_dates`、`personal_goals`、`self_timeline_entries` |
| KIG 知识治理 | `kig_retrieval_bundles`、`kig_answer_claim_segments`、`kig_evidence_links`、`kig_source_governance`、`kig_version_relations`、`kig_maintenance_candidates` |
| PWM 世界模型 | `pwm_entities`、`pwm_entity_aliases`、`pwm_claims`、`pwm_relations`、`pwm_world_events`、`pwm_state_assertions` |

---

## 安全与隐私

### 本地 API 保护

- Electron 每次启动生成 32 字段随机令牌
- 令牌通过 preload 桥接交给主窗口，不进入 URL、日志和 localStorage
- 前端所有 API 请求携带令牌
- 后端中间件校验令牌
- CORS 仅允许明确的本地开发源和 Electron 来源

### 密钥保护

- API Key 仅存本地 SQLite
- API 永不返回完整密钥，只返回 `has_key` 和必要状态
- 日志、异常和连接测试结果统一脱敏
- 正式版需迁移到 Electron `safeStorage`（路线图 v0.1.1）

### 工具风险分级

| 等级 | 示例 | 默认策略 |
|---|---|---|
| S0 | 展示、计算、格式转换 | 自动允许并记录 |
| S1 | 读取用户明确选择的本地范围 | 在已授权范围内允许 |
| S2 | 修改本地数据或文件 | 每次预览并确认 |
| S3 | 外发消息、网络写入、操作应用 | 强确认，展示目标和参数 |
| S4 | Shell、输入控制、系统设置、付款 | 默认禁用，后期白名单开放 |

### 隐私分级

- CDS 隐私 fail-closed：body-bearing 认知任务在远程 / 未认证位置直接拒绝
- 知识库传输策略：local_only / ask_each_time / remote_allowed
- 记忆敏感信息拦截：API Key、密码、身份证、银行卡等禁止记录
- 模拟生活不声明为现实执行
- 私人日记默认折叠

---

## 项目治理

### 治理文档

| 文档 | 用途 |
|---|---|
| [Codex 项目上下文](docs/CODEX_PROJECT_CONTEXT.md) | 不可变决策和禁止事项 |
| [长期开发路线图](docs/XIADIE_LONG_TERM_ROADMAP.md) | 从 MVP 到最终 Agent 的分阶段安排 |
| [项目基线状态](docs/BASELINE_STATUS.md) | 当前环境、验证结果、已有能力与已知风险 |
| [PR 检查清单](docs/PR_CHECKLIST.md) | 每次改动的范围、风险、验证与交付标准 |
| [架构决策记录](docs/adr/README.md) | 66 份 ADR（0001-0066） |
| [专项所有权与共享施工契约](docs/SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md) | 统一基线、所有权、晋级、模型认证、预算及数据生命周期门禁 |
| [CDS 认知决策施工计划](docs/LLM_COGNITIVE_DECISION_REFACTOR_PLAN.md) | CDS 专项计划（已冻结） |
| [LIFE 生活连续性施工计划](docs/LLM_DECISION_AND_LIFE_CONTINUITY_PLAN.md) | LIFE 专项计划（已冻结） |
| [KIG 知识治理与 PWM 施工计划](docs/XIADIE_KNOWLEDGE_INTELLIGENCE_GOVERNANCE_AND_WORLD_MODEL_PLAN.md) | KIG 专项计划（施工中） |

### 专项施工顺序

```
CTX（上下文）→ EAP（主动陪伴）→ CDS（认知决策）→ LIFE（生活连续性）→ KIG（知识治理）
     冻结          冻结              冻结             冻结            施工中
```

### 冻结状态

| 专项 | Schema | 测试基线 | 状态 |
|---|---|---|---|
| CTX | 48-55 | — | 已冻结 |
| EAP | 60 | — | 已冻结（6 协议） |
| CDS | 63 | 2304 passed | 已冻结（`cognitive-decision-v1`） |
| LIFE | 71 | 2423 passed | 已冻结（`life-adapter-v1`） |
| KIG | 74 | 2428 passed | KIG.0-7 完成，KIG.8 待施工 |

### 所有权原则

任何对象只有一个唯一写入者。其他专项只能只读或提议，不能成为第二个正式写入者。删除始终由权威所有者执行并向派生层传播。

---

## 本地开发

### 前置要求

- Node ≥ 18
- Python 3.10-3.12
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip

### 启动

```bash
# 1) 后端
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python run.py          # http://127.0.0.1:8756

# 2) 前端（另开终端）
cd frontend
npm install
npm run dev                      # http://127.0.0.1:5173

# 3) 桌面壳（再开终端；dev 期假定前后端已启动）
cd desktop
npm install
npm start
```

启动后桌面出现 Live2D 遐蝶；点击桌宠或托盘打开主窗口。

浏览器预览：主窗口 `http://127.0.0.1:5173/`，桌宠页 `http://127.0.0.1:5173/pet.html`。

### 一键启动

依赖安装完成后，双击仓库根目录的 `启动遐蝶.bat`。无终端窗口启动后端、前端和 Electron；退出遐蝶后清理后台进程，后端在启动器异常退出时通过父进程看门狗自行关闭。

### 模型配置

首启使用内置 `mock` 演示模型，界面全部可用但回复为占位文案。到 **设置 → 模型 API** 填入任意兼容 OpenAI 接口的 Base URL + API Key，点"连接测试"通过后"设为当前"即可获得真实回复。

---

## 测试与构建

### 后端测试

```bash
cd backend && python -m pytest tests -q
```

- 107 个测试文件，约 1208 个测试用例
- 19 个 JSON 评估夹具（`tests/fixtures/`）
- 约 40 个离线评估与基线脚本（`backend/scripts/`）

### 前端构建

```bash
cd frontend && npm run build     # tsc -b + vite build
```

### 前端测试

```bash
cd frontend && npm test          # node --test
```

---

## Windows 打包

### 一键打包

```powershell
.\build-windows.ps1
```

产出 `dist-installer\遐蝶-Setup-0.1.0.exe`（NSIS 安装器）。目标机无需预装 Python / Node。

### 打包流程

1. 前端构建（`npm run build`）
2. 后端冻结（PyInstaller → `xiadie-backend.exe`）
3. Electron 打包（electron-builder → NSIS）

### 运行时数据

- 安装目录：`遐蝶.exe` + `resources\`（app.asar、frontend、backend、models）
- 用户数据：`%APPDATA%\遐蝶\data\`（可写目录，不在安装目录）
- 后端固定 `127.0.0.1:8756`
- 前端 `file://` 加载 `resources\frontend\`

详见 [BUILD-WINDOWS.md](BUILD-WINDOWS.md)。

---

## 路线图

完整路线见 [长期开发路线图](docs/XIADIE_LONG_TERM_ROADMAP.md)。

| 应用版本 | 核心目标 | 状态 |
|---|---|---|
| v0.1.0 | 可运行 MVP 骨架 | 已完成 |
| v0.1.1 | 治理与本地安全 | 已完成 |
| v0.1.2 | 聊天、上下文和数据可靠性 | 已完成 |
| v0.1.3 | 前端结构与 UI 基线 | 已完成 |
| v0.2.0 | ToolRegistry 与权限内核 | 待启动 |
| v0.3.0 | 安全的本地文件工作区 | 待启动 |
| v0.4.0 | 可追溯知识库 | KIG 进行中 |
| v0.4.1 | 可靠记忆系统 | 已完成 |
| v0.4.2 | 记忆星座与伴侣状态 | 已完成 |
| v0.5.0 | TaskRun 执行工作台 | 待启动 |
| v0.5.1 | Planner / Executor / Verifier | 待启动 |
| v0.6.0 | 搜索与浏览器工具 | 待启动 |
| v0.9.0 | 多 Agent Worker 化 | 待启动 |
| v1.0.0 | 可发布稳定版 | 待启动 |

---

## 许可证

本项目自身代码采用 [MIT License](LICENSE)。第三方依赖、Live2D Core、角色模型及其他资源仍分别受其自身许可约束；MIT 声明不覆盖这些内容，详见 [NOTICE.md](NOTICE.md)。

### Live2D 模型授权说明

当前内置的是用户提供的"遐蝶"桌宠模型，仅个人自用授权（禁止再分发 / 商用 / 上传 / 二改）。正式版须换成原创或已授权可再分发的模型。

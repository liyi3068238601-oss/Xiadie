# 遐蝶知识智能、信息治理与个人世界模型专项施工计划

- 版本：v0.2
- 日期：2026-07-21
- 状态：施工前设计，待代码审计（EAP v0.2 已完成，LIFE 专项待施工，KIG 在 LIFE 之后施工）
- 专项代号：`KIG`（Knowledge Intelligence & Governance）
- 子系统代号：`PWM`（Personal World Model）
- 适用范围：用户知识库、信息分类与治理、多源检索、LLM 查询规划与重排、证据与引用、冲突与版本、个人世界模型，以及与对话历史、长期记忆、生活连续性、任务和 ContextAssembler 的接口
- 不包含：SecretStore、ToolRegistry、MCP、多 Agent、外部消息平台、桌面自动化、完整联网研究 Agent、云端多人知识空间
- 施工原则：本计划描述最终能力全集；开始施工前必须审查现有代码、数据库、测试和 UI，已经完整实现的项目直接勾选，部分实现只补差距，不因设计重叠而重写现有功能
- 关联专项：
  - `CTX`：对话上下文、滚动摘要与跨会话历史回忆（已冻结 Schema 45，6 个协议 FROZEN）
  - `EAP`：情感、关系积温与主动陪伴（v0.2 已完成 Schema 55，6 个协议 FROZEN）
  - `LIFE`：LLM 认知决策与生活连续性（待施工，新 schema 从 Schema 56 起）
  - `MEM`：Fragment、Episode、Saga、Archivist 和记忆观察器（Schema 47 冻结）

> v0.2 修订说明（2026-07-21）：EAP v0.2 已完成代码施工（Schema 55，commit fd9042c），LIFE 专项计划书已修订并复制到 docs/。本次修订重点：
> 1. 更新现有能力实现状态（知识库已实现大量功能，不是从零开始）
> 2. 明确与已冻结协议的边界（context-package-v1、knowledge-search-v2、knowledge-recall-decision-v1 等）
> 3. 补充与 EAP v0.2/LIFE 专项的接口约束
> 4. Schema 版本断言：KIG 新表从 Schema 56 起（如 LIFE 未占用），或从 LIFE 之后第一个可用版本起

---

## 0. 审计状态标记

本计划不是“所有条目都尚未实现”的假设性清单。施工前使用以下状态：

```text
[x] 已完整实现，并通过当前验收与测试
[~] 已部分实现，必须列出剩余差距
[ ] 尚未实现
[→] 由其他专项拥有，本专项只建立或调用接口
[-] 当前版本不适用，保留未来兼容位
```

### 0.1 现有能力实现状态（v0.2 新增，2026-07-21 审计）

> 基于对现有代码的审查，按 KIG 计划书章节标注能力状态。

#### 已完整实现 `[x]`

| 能力 | 当前事实来源 | 状态 |
|---|---|---|
| 知识库导入与文件管理 | `knowledge.py` + `knowledge_management.py` | `[x]` 已实现（content_sha256 去重、sensitivity、transmission_policy） |
| 文档解析（TXT/MD/PDF/DOCX） | `knowledge_parser.py` | `[x]` 已实现（parser_version 记录） |
| 结构化切片 | `knowledge_chunker.py` | `[x]` 已实现（chunker_version 记录、heading_path、chunk_index） |
| FTS5 词法索引 | `knowledge_search.py` + `db.py` FTS 触发器 | `[x]` 已实现（BM25 排序） |
| Embedding 向量索引 | `knowledge_embeddings.py` | `[x]` 已实现（provider_id、model_id、dimension 记录） |
| 混合检索（FTS + Dense） | `knowledge_search.py hybrid_search` | `[x]` 已实现（`knowledge-search-v2` 协议） |
| 知识库授权与传输策略 | `knowledge_grants.py` + `knowledge_policy.py` | `[x]` 已实现（local_only/ask_each_time/remote_allowed） |
| 知识召回决策 | `knowledge_recall.py` | `[x]` 已实现（`knowledge-recall-decision-v1` 协议） |
| 知识召回评估 | `knowledge_recall_evaluation.py` | `[x]` 已实现 |
| 知识上下文装配 | `knowledge_context.py` + `context_assembler.py` | `[x]` 已实现（`context-package-v1` 协议） |
| 知识清理 | `knowledge_cleanup.py` | `[x]` 已实现（orphan 清理） |
| 后台知识工作器 | `knowledge_worker.py` | `[x]` 已实现 |
| 长期记忆（Fragment） | `memory.py` + `memory_writer.py` | `[x]` 已实现（FTS5 索引、enabled/layer/status） |
| 记忆观察器 | `memory_observer.py` + `memory_observer_service.py` | `[x]` 已实现 |
| 记忆冲突 | `memory_conflicts.py` | `[x]` 已实现 |
| Episode/Saga | `episodes.py` + `sagas.py` + `episode_consolidator.py` + `saga_consolidator.py` | `[x]` 已实现（Schema 47 冻结） |
| Archivist 工作器 | `archivist.py` + `archivist_worker.py` | `[x]` 已实现 |
| 跨会话历史召回 | `history_recall.py` | `[x]` 已实现（`conversation-history-score-v1-shadow`） |

#### 部分实现 `[~]`（需补差距）

| 能力 | 当前事实 | 差距 |
|---|---|---|
| SourceRef 统一来源锚点 | 各表有自己的来源字段（content_sha256、source_session_id 等） | `[~]` 缺统一 SourceRef 抽象，跨系统来源引用需补 |
| 查询规划 | `knowledge_search.py hybrid_search` 已实现基础查询 | `[~]` 缺 QueryIntent 分类、QueryPlan 子查询拆解 |
| LLM 语义重排 | `knowledge_search.py _re_rank` 使用确定性融合 | `[~]` 缺 LLM 语义重排（当前是 BM25+向量分数融合） |
| 证据与引用 | `knowledge_context.py` 返回 chunk_id + locator | `[~]` 缺 EvidenceLink、claim-support-v1 支持度检查 |
| 冲突与版本 | `memory_conflicts.py` 处理记忆冲突 | `[~]` 缺 VersionRelation、FreshnessState 跨文档版本治理 |
| 实体管理 | `entities.py` 基础实体表 | `[~]` 缺 EntityAlias、Relation、WorldEvent、StateAssertion |

#### 未实现 `[ ]`

| 能力 | 状态 |
|---|---|
| InformationItem 统一分类对象 | `[ ]` 未实现 |
| Claim 原子断言 | `[ ]` 未实现 |
| Personal World Model（PWM）完整投影 | `[ ]` 未实现 |
| RetrievalTrace 检索追踪 | `[ ]` 未实现 |
| MaintenanceCandidate 维护候选 | `[ ]` 未实现 |
| 知识库主页 UI（项目/实体页） | `[ ]` 未实现（当前只有文件列表） |

#### 由其他专项拥有 `[→]`

| 能力 | 拥有专项 | KIG 接口 |
|---|---|---|
| ContextAssembler 上下文装配 | CTX（`context-package-v1` 已冻结） | `[→]` KIG 只提供 RetrievalBundle，不修改 ContextAssembler |
| LifeEvent 生活事件 | LIFE（待施工） | `[→]` KIG 只读引用，不修改 LifeEvent |
| EAP 主动陪伴决策 | EAP（`proactive-decision-v2` 已冻结） | `[→]` KIG 不参与主动决策 |
| Fragment/Episode/Saga 写入 | MEM（Schema 47 冻结） | `[→]` KIG 只提供候选，MEM Validator 裁决 |
| 聊天历史原文 | CTX（`conversation-history-index-v1` 已冻结） | `[→]` KIG 只索引允许参与召回的会话 |

### 0.2 已冻结协议边界（v0.2 新增）

> KIG 专项不得修改以下已冻结协议的 schema 和校验规则。

| 协议 | 冻结阶段 | KIG 边界 |
|------|---------|---------|
| `context-package-v1` | CTX.4 | KIG 通过 ContextAssembler 扩展点注入 RetrievalBundle，不修改协议 schema |
| `conversation-summary-v1` | CTX.2/3 | KIG 不修改摘要协议 |
| `context-budget-v1` | CTX.1 | KIG 遵守 token 硬预算，不突破 |
| `conversation-history-index-v1` | CTX.5 | KIG 只索引允许参与召回的会话，不修改索引协议 |
| `conversation-history-score-v1-shadow` | CTX.5 | KIG 不修改历史召回评分 |
| `context-acceptance-v1` | CTX.7 | KIG 不修改上下文接受协议 |
| `affect-observer-v1` | EAP.B | KIG 不修改情感观察协议 |
| `user-affect-observation-v1` | EAP.C | KIG 不修改用户情感观察协议 |
| `conversation-presence-v2` | EAP.C | KIG 不修改在场状态协议 |
| `relationship-meaning-v1` | EAP.D | KIG 不修改关系意义协议 |
| `proactive-decision-v2` | EAP.F | KIG 不参与主动决策 |
| `expression-plan-v1` | EAP.H | KIG 不修改表达计划协议 |
| `knowledge-search-v2` | 现有知识库 | KIG 可扩展但不修改已冻结的 search_protocol_version |
| `knowledge-recall-decision-v1` | 现有知识库 | KIG 可扩展但不修改已冻结的 recall 协议 |

### 0.3 Schema 版本断言

- EAP v0.2 已冻结 Schema 55
- LIFE 专项新表从 Schema 56 起
- KIG 专项新表从 LIFE 之后第一个可用版本起（预计 Schema 70+，具体取决于 LIFE 实际使用的版本数）
- KIG 不得修改 Schema 55 及之前的所有迁移
- KIG 新增表必须使用顺序迁移，不修改历史迁移

执行要求：

1. 任何 `[x]` 必须有真实代码路径、数据库对象、测试用例和当前行为证据。
2. 文档、注释、常量或未接入主链的骨架不能标记为 `[x]`。
3. 与 CTX、MEM、EAP、LIFE 重叠的项目优先标记 `[→]`，禁止在 KIG 内另建第二套实现。
4. `[~]` 必须明确“已有能力、缺失能力、最小补差范围、回滚方式”。
5. 每完成一个阶段，更新本计划、`BASELINE_STATUS.md` 和 `CODEX_PROJECT_CONTEXT.md`，再进入下一阶段。

---

## 1. 专项目标

### 1.1 产品目标

KIG 的目标不是把更多文字塞进向量库，而是建立一套能够长期运行的信息认知系统，使遐蝶能够：

1. 知道一条内容属于外部知识、用户记忆、聊天历史、生活事件、角色设定、任务结果还是临时状态。
2. 知道信息来自哪里、何时产生、是否仍有效、是否被新内容替代、是否允许用于当前回答。
3. 面对模糊问题时，先判断应该查哪里、怎样拆解查询，而不是对所有数据库同时做一次相似度搜索。
4. 在本地检索结果中使用 LLM 做语义重排、冲突判断和证据支持度分析，提高自然理解能力。
5. 将人物、项目、文件、目标、地点、日期、工具和事件组织成可追溯的个人世界模型，但不把模型推断当成事实。
6. 将知识、记忆、历史、生活和任务结果通过统一接口交给 ContextAssembler，在模型窗口内选择最相关的证据。
7. 回答复杂问题时区分“来源明确的事实”“模型综合推断”“仍不确定的部分”，并提供可回到原文的引用。
8. 长期运行后仍能处理新旧版本、重复文档、用户纠正、过时计划、同名实体和删除级联。
9. 普通用户无需理解向量、图谱、分数和版本算法，也能通过自然界面管理文件、来源、日期、项目和记忆。
10. LLM 只负责语义理解和建议；程序负责来源、权限、状态、版本、真正写入和执行。

### 1.2 一句话定位

> **知识库负责“资料中写了什么”，长期记忆负责“用户是谁、我们经历过什么”，聊天历史负责“过去具体说过什么”，生活时间线负责“遐蝶自己经历了什么”，个人世界模型负责“这些人物、项目、事件和事实彼此是什么关系”，KIG 负责判断本轮应该相信什么、查找什么、组合什么以及如何证明。**

### 1.3 目标闭环

```text
用户导入文件 / 用户消息 / 工具结果 / 生活事件 / 记忆变化
                         ↓
             Provenance Intake 来源接收
                         ↓
      Information Classification 信息分类与归属建议
                         ↓
    Knowledge / Memory / Conversation / Life / Task / Lore
                         ↓
      Entity、Claim、Event、Relation 候选抽取与验证
                         ↓
          Personal World Model 个人世界模型
                         ↓
用户问题 → Query Planner → 多库候选检索 → LLM 语义重排
                         ↓
     冲突、版本、新鲜度、证据支持度与预算校验
                         ↓
                 ContextAssembler
                         ↓
            回答 / 工具建议 / 状态更新建议
                         ↓
        用户纠正、来源变化、命中反馈和维护候选
```

---

## 2. 与现有系统的职责边界

### 2.1 各系统唯一职责

| 系统 | 唯一职责 | KIG 可以做什么 | KIG 禁止做什么 |
|---|---|---|---|
| `messages/sessions` | 原始聊天档案 | 检索、引用、建立实体/事件候选 | 修改原文、用摘要覆盖原文 |
| `CTX` | 本轮上下文预算和装配 | 提供排序后的候选及预算建议 | 自己拼接最终 Prompt、突破 token 硬预算 |
| `Fragment` | 稳定事实、偏好、计划、边界 | 提出分类、冲突、召回重排建议 | 直接创建、删除、覆盖正式 Fragment |
| `Episode` | 一段有意义的共同经历 | 提出事件边界和成员建议 | 直接合并正式 Episode |
| `Saga` | 长期主题和阶段演变 | 提出继续、分支、休眠、复活建议 | 直接修改 Saga 生命周期 |
| `LIFE` | 遐蝶连续状态、日程、日记、自我时间线 | 检索 LifeEvent，建立实体关系候选 | 将模拟事件改成真实工具行为 |
| `EAP` | 情绪、关系、主动候选和表达 | 提供相关知识/记忆证据 | 用知识相关度改变权限或关系数值 |
| `Lore` | 角色世界观和固定设定 | 检索、版本和引用 | 将用户文件静默写入核心人设 |
| `Task/ToolRun` | 任务状态和真实执行证据 | 索引结果、建立项目事件 | 无 ToolRun 证据声称真实执行 |
| `Knowledge` | 用户明确导入的外部资料 | 完整负责接收、索引、检索、版本和引用 | 默认扫描磁盘、静默上传远程 Provider |
| `PWM` | 实体、关系、事件和状态的派生视图 | 统一导航、消歧、跨库检索 | 成为原始事实来源、替代各源数据库 |

### 2.2 KIG 是治理与集成层，不是“大一统数据库”

KIG 不把全部数据复制到一张表。正确方式：

```text
原始文件、消息、记忆、LifeEvent、ToolRun
                  ↓
            SourceRef / EvidenceLink
                  ↓
      轻量统一索引与世界模型投影
```

原则：

- 原始系统继续保存权威数据。
- KIG 保存来源引用、派生 Claim、实体关系、版本关系和检索索引。
- 派生对象失效时可以重建。
- 用户手动确认或修正的治理信息必须保留 revision，不能被后台重建静默覆盖。
- 删除源数据时，KIG 只做级联失效或删除派生引用，不保留隐藏正文副本。

### 2.3 与 CTX 的唯一接线

> v0.2 修订（2026-07-21）：`context-package-v1` 协议已冻结（CTX.4 阶段）。KIG 不得修改已冻结协议 schema，只能通过 ContextAssembler 的扩展点注入 RetrievalBundle。

KIG 不直接向聊天模型拼接知识。它向 ContextAssembler 返回：

```text
KnowledgeRetrievalBundle（KIG 输出，通过 ContextAssembler 扩展点注入）
├─ query_plan_summary
├─ selected_evidence[]
│  ├─ source_type
│  ├─ source_id
│  ├─ locator
│  ├─ excerpt
│  ├─ relevance_role
│  ├─ freshness_state
│  └─ token_estimate
├─ conflict_notes[]
├─ insufficiency_notes[]
└─ retrieval_trace_metadata
```

**已冻结协议边界**：

- `context-package-v1`（CTX.4 冻结）：ContextPackage 的 schema 已冻结，KIG 不得直接修改
- `context-budget-v1`（CTX.1 冻结）：KIG 遵守 token 硬预算，不突破
- `context-acceptance-v1`（CTX.7 冻结）：KIG 不修改上下文接受协议

**KIG 专项改造边界**：

- 不修改 `context-package-v1` 协议 schema（已冻结）
- 不修改 ContextAssembler 的核心装配逻辑（已冻结）
- 通过 ContextAssembler 的**扩展点**注入 KnowledgeRetrievalBundle（如果 CTX 已提供扩展点）
- 如果 CTX 未提供扩展点，KIG 不得强行修改 ContextAssembler，须先与 CTX 协商解冻协议
- ContextAssembler 最终决定是否注入、注入多少以及先缩减哪一部分
- CTX 的总预算不变量、当前用户输入保护区和输出预留优先于 KIG 的任何排序

现有 CTX 已明确知识、长期记忆、历史原文和摘要各自独立，并由统一 ContextPackage 编排。现有 `knowledge_context.py` 已实现知识上下文装配，KIG 不得重写，只补差距（EvidenceLink、支持度检查等）。

---

## 3. 不可突破的产品边界

### 3.1 来源高于模型判断

1. LLM 输出不是事实来源。
2. 原始文件、原始消息、用户最新纠正、正式 ToolRun 和经过确认的用户设置优先。
3. 摘要、Embedding、实体关系、Claim 和世界模型都是派生层。
4. 派生层与原文冲突时，派生层必须失效或重建。
5. 不能因为某条内容“看起来合理”就补造页码、文件名、日期、人物或版本。

### 3.2 用户明确导入和数据流向

- 不默认扫描用户磁盘。
- 不因为文件位于常用目录就自动建立知识库。
- 用户必须通过拖入、选择文件/文件夹或明确连接来源导入。
- 使用远程模型处理文件、摘要、语义切片或重排前，必须遵守数据传输策略和 Provider 授权。
- 敏感文件可选择仅本地解析、仅本地 Embedding、禁用 LLM 语义增强。
- 连接云盘、网页或仓库属于后续 Connector 能力，不在首版静默开启。

最终产品需求已经明确知识库要遵守“用户明确导入、来源可追溯、结果可删除”，且不得在用户不知情时上传文件。

### 3.3 LLM 提议，程序裁决

LLM 可以：

- 分类信息类型。
- 建议文档结构和切片边界。
- 规划查询、改写查询、选择检索源。
- 在有限候选中重排。
- 判断候选之间是补充、替代、条件不同还是冲突。
- 抽取实体、关系、事件和 Claim 候选。
- 判断证据对结论的支持程度。
- 生成面向用户的摘要、比较和解释草稿。

LLM 不可以：

- 直接删除或覆盖文件、记忆和聊天。
- 直接合并实体或正式记忆。
- 直接改变用户权限、隐私级别和数据传输设置。
- 直接把候选写成 active/superseded/deprecated 最终状态。
- 创建不存在的引用、页码、来源 ID 或 ToolRun。
- 突破 ContextAssembler 的 token 预算。
- 把用户对话中的提示当成后台系统命令。

### 3.4 个人世界模型不宣称全知

- PWM 只是当前已有来源的结构化投影。
- 没有证据的关系保持 `candidate/uncertain`，不进入事实回答。
- 同名实体不自动合并。
- 模型推断的职业、健康、家庭、政治、宗教、性取向等敏感属性不得自动建立。
- 用户可查看、纠正、拆分、合并和删除个人世界模型中的派生节点。
- 关系图不能成为对用户进行画像评分、说服操纵或外部广告定向的工具。

### 3.5 普通体验不技术化

默认界面显示：

```text
已参考 3 份资料
存在更新版本
这个结论的资料不足
这两份文档说法不同
```

默认不显示：

```text
rerank_score=0.847
entity_merge_confidence=0.72
BM25=14.3
vector_distance=0.18
claim_graph_node_id=...
```

详细分数、协议版本、模型、hash 和候选原因只进入开发者诊断。

### 3.6 安全事实不因“拟人化”而变化

知识、记忆、情绪和关系可以影响表达方式，不得改变：

- 文件权限。
- 外部网络权限。
- Shell/桌面自动化确认。
- 消息发送确认。
- 引用真实性。
- 用户删除、忘记、禁记和关闭历史的明确指令。

---

## 4. 目标架构

```text
                    Information Sources

 User Files   Messages   Memory   Life   ToolRun   Lore   External Search
     │           │          │       │       │        │          │
     └───────────┴──────────┴───────┴───────┴────────┴──────────┘
                                 ↓
                    4.1 Provenance Gateway
                来源、权限、hash、版本、locator
                                 ↓
                  4.2 Information Classifier
              类型、生命周期、目标存储、敏感级别
                                 ↓
        ┌────────────────────────┼─────────────────────────┐
        │                        │                         │
 Knowledge Document        Existing Memory         Conversation/Life
 文档、Chunk、Claim        Fragment/Episode/Saga    原消息、LifeEvent
        └────────────────────────┼─────────────────────────┘
                                 ↓
                4.3 Personal World Model Projection
                 Entity / Relation / Event / State
                                 ↓
用户问题 → 4.4 Query Planner → 4.5 Multi-source Candidate Retrieval
                                 ↓
                        4.6 LLM Reranker
                                 ↓
       4.7 Conflict / Version / Freshness / Evidence Validation
                                 ↓
                    4.8 Retrieval Bundle for CTX
                                 ↓
                         ContextAssembler
                                 ↓
                      Chat / Task / Explanation
```

### 4.1 两条运行路径

#### 写入路径

```text
Source Intake
  ↓
确定性安全检查
  ↓
正文/元数据提取
  ↓
本地初步结构化
  ↓
可选 LLM 语义增强
  ↓
Schema、来源、敏感和版本验证
  ↓
索引 + PWM 派生候选
```

#### 读取路径

```text
User Query
  ↓
本地高精度意图规则
  ↓
必要时 LLM Query Planner
  ↓
各源独立召回
  ↓
去重、过滤和候选压缩
  ↓
LLM 语义重排
  ↓
来源/版本/冲突/证据校验
  ↓
CTX 按预算注入
```

---

## 5. 统一领域模型

### 5.1 SourceRef：所有派生信息的来源锚点

```text
SourceRef
├─ id
├─ source_type
│  document / document_chunk / message / fragment / episode / saga /
│  life_event / diary / important_date / task / tool_run / lore / web_result
├─ source_id
├─ source_revision
├─ content_hash
├─ locator_json
│  page / section / paragraph / line / message_range / timestamp
├─ title_hint
├─ created_at
├─ observed_at
├─ privacy_level
├─ provider_transfer_policy
├─ status
│  active / missing / deleted / superseded / revoked / inaccessible
└─ metadata_json
```

规则：

- 所有 Claim、EntityMention、Relation、Event 和引用必须至少拥有一个 SourceRef。
- `locator_json` 必须能返回原文位置，不允许只保存不可验证摘要。
- 来源 revision 或 hash 变化后，依赖的派生对象进入 `stale_pending_rebuild`。
- SourceRef 不复制完整正文，仅保存必要定位和短展示信息。

### 5.2 InformationItem：统一分类对象

```text
InformationItem
├─ id
├─ item_type
│  world_fact / personal_fact / preference / plan / event / opinion /
│  temporary_state / instruction / policy / lore / agent_self_state /
│  task_result / unknown
├─ canonical_summary
├─ subject_hint
├─ temporal_scope
├─ stability
│  transient / short_term / ongoing / stable / unknown
├─ proposed_destination
│  knowledge / memory / conversation / life / lore / task / none
├─ confidence
├─ sensitivity
├─ source_refs
├─ protocol_version
├─ status
│  candidate / validated / applied / rejected / expired / revoked
└─ created_at / updated_at
```

`InformationItem` 是路由建议，不是原始事实。正式落库仍由目标系统自己的 Validator 完成。

### 5.3 KnowledgeDocument

```text
KnowledgeDocument
├─ id
├─ collection_id
├─ display_name
├─ source_uri_or_local_ref
├─ file_hash
├─ mime_type
├─ size_bytes
├─ language
├─ document_type
├─ author_hint
├─ version_label
├─ effective_date
├─ imported_at
├─ parser_version
├─ semantic_protocol_version
├─ status
│  queued / parsing / indexed / partially_indexed / failed /
│  active / possibly_stale / superseded / archived / deleted
├─ transfer_policy
└─ metadata_json
```

### 5.4 KnowledgeChunk

```text
KnowledgeChunk
├─ id
├─ document_id
├─ chunk_index
├─ source_locator
├─ raw_text
├─ normalized_text
├─ heading_path
├─ chunk_kind
│  paragraph / definition / procedure / warning / table / code /
│  list / caption / mixed
├─ token_count
├─ embedding_ref
├─ lexical_index_state
├─ semantic_boundary_source
│  deterministic / llm_suggested / manual
├─ status
└─ content_hash
```

原则：

- `raw_text` 保留解析后的原文，不由 LLM 改写。
- `normalized_text` 只允许确定性空白、换行和编码清理。
- LLM 可以建议合并/拆分边界，不能重写原文后冒充原文。
- 表格、代码、图注应保留上级标题和必要上下文。

### 5.5 Claim：可验证的原子断言

```text
Claim
├─ id
├─ statement
├─ claim_type
├─ subject_entity_id
├─ predicate
├─ object_entity_id_or_value
├─ qualifiers_json
│  time / location / condition / version / scope / modality
├─ confidence
├─ source_refs
├─ support_type
│  explicit / strongly_implied / model_inferred
├─ validity_state
│  candidate / active / disputed / superseded / expired / revoked
├─ valid_from / valid_until
└─ protocol_version
```

约束：

- 首版不要求所有文档都抽取 Claim；只对高价值文档、用户查询命中的片段和世界模型需要的内容按需抽取。
- `model_inferred` Claim 默认不能独立支持事实回答。
- 用户最新明确纠正可以使相关 Claim `superseded/revoked`，但原来源仍保留。

### 5.6 Entity 与 EntityAlias

```text
Entity
├─ id
├─ entity_type
│  person / agent / project / organization / document / model / tool /
│  place / concept / product / date / goal / event / other
├─ canonical_name
├─ description
├─ sensitivity
├─ status
│  candidate / active / merged / split / archived / revoked
├─ created_from
└─ revision

EntityAlias
├─ entity_id
├─ alias
├─ language
├─ scope
├─ source_refs
├─ confidence
└─ status
```

同名处理：

- “遐蝶”“Xiadie”“遐蝶 Agent”可以成为同一实体的 alias 候选。
- 低置信度不自动合并。
- 已合并实体必须支持拆分和关系迁移预览。
- 人物实体不自动推断敏感属性。

### 5.7 Relation

```text
Relation
├─ id
├─ subject_entity_id
├─ predicate
├─ object_entity_id_or_value
├─ qualifiers_json
├─ confidence
├─ source_refs
├─ temporal_scope
├─ status
│  candidate / active / disputed / superseded / revoked
└─ protocol_version
```

首版 Predicate 使用白名单：

```text
alias_of
owns
uses
depends_on
part_of
references
works_on
plans
prefers
created
completed
supersedes
related_to
occurred_at
involves
```

自由 Predicate 只能作为候选或映射到 `related_to`，避免关系类型无限膨胀。

### 5.8 WorldEvent

```text
WorldEvent
├─ id
├─ event_type
├─ title
├─ summary
├─ start_at / end_at
├─ participant_entity_ids
├─ object_entity_ids
├─ location_entity_id
├─ source_refs
├─ confidence
├─ event_layer
│  external_world / user_life / shared_conversation /
│  agent_simulated_life / agent_real_action / project_history
├─ status
│  candidate / active / disputed / superseded / revoked
└─ protocol_version
```

与 LIFE 的区别：

- LifeEvent 是遐蝶生活连续性的事实账本。
- WorldEvent 是跨系统的派生视图，可以引用 LifeEvent，但不能改变其 `planned/materialized/performed/inferred` 语义。
- `agent_real_action` 必须引用 ToolRun。

### 5.9 StateAssertion

用于表达有时间范围的状态，而非永久事实：

```text
StateAssertion
├─ subject_entity_id
├─ state_type
├─ value
├─ valid_from / valid_until
├─ scope
├─ confidence
├─ source_refs
└─ status
```

例如：

```text
用户近期正在开发 CTX
某项目当前处于设计阶段
某文档当前为 authoritative
遐蝶当前正在休息（引用 LIFE）
```

状态过期后不删除，只退出 active 视图。

### 5.10 VersionRelation

```text
VersionRelation
├─ older_source_ref
├─ newer_source_ref
├─ relation
│  exact_duplicate / revision_of / supersedes / partially_supersedes /
│  compatible / divergent_branch / unrelated / uncertain
├─ scope_json
├─ confidence
├─ evidence_refs
├─ decision_source
│  deterministic / llm_proposal / user_confirmed
└─ status
```

### 5.11 RetrievalTrace

```text
RetrievalTrace
├─ request_id
├─ query_hash
├─ planner_protocol
├─ selected_sources
├─ candidate_counts_by_source
├─ reranker_model
├─ validation_warnings
├─ conflict_count
├─ injected_item_ids
├─ token_counts
├─ latency_breakdown
└─ created_at
```

不保存不必要的完整查询正文和完整候选正文；仅保存 hash、ID 和统计，开发者诊断按权限读取原来源。

---

## 6. 信息分类与目标路由

### 6.1 分类枚举

| 类型 | 例子 | 默认归属 |
|---|---|---|
| `WORLD_FACT` | “某 API 的参数定义” | Knowledge |
| `PERSONAL_FACT` | “用户就读于某学校” | Fragment 候选 |
| `PREFERENCE` | “用户更喜欢单主窗口” | Fragment 候选 |
| `PLAN` | “下一步先完成 CTX” | Fragment/Task 候选 |
| `EVENT` | “项目完成一次重大迁移” | Episode/WorldEvent 候选 |
| `OPINION` | “这个设计更自然” | Conversation，必要时 Memory 候选 |
| `TEMPORARY_STATE` | “最近有点忙” | Conversation/EAP 短期状态 |
| `INSTRUCTION` | “安装步骤” | Knowledge |
| `POLICY` | “发送消息必须确认” | Knowledge/Lore/Project rule |
| `CHARACTER_LORE` | 角色固定设定 | Lore 候选 |
| `AGENT_SELF_STATE` | 遐蝶当前日程、心境 | LIFE，只读投影 |
| `TASK_RESULT` | 工具真实执行结果 | Task/ToolRun，知识可索引 |

### 6.2 分类流程

```text
高精度本地规则
  ├─ 文件来源 → Knowledge
  ├─ ToolRun → Task Result
  ├─ LifeEvent → Agent Self State / Event
  ├─ 明确“记住/忘记” → Memory command
  └─ 明确用户设置 → Setting/Boundary
            ↓
规则无法完整判断
            ↓
LLM Classification Proposal
            ↓
目标系统 Validator
            ↓
应用、保持候选或拒绝
```

### 6.3 分类不得直接写入

LLM 说“这是稳定偏好”不等于创建 Fragment。目标系统仍需检查：

- 是否有逐字用户证据。
- 是否只是当前任务中的临时要求。
- 是否与已有记忆冲突。
- 是否包含敏感内容。
- 是否达到该 kind 的稳定性门槛。
- 用户是否关闭自动记忆。

---

## 7. LLM 参与决策矩阵

### 7.1 文档接收阶段

| 环节 | LLM 参与 | 程序裁决 |
|---|---|---|
| MIME、大小、安全 | 不参与 | 完全确定性 |
| 文档类型 | 提出语义类型 | 校验枚举、保留 unknown |
| 标题/作者/版本线索 | 提取候选 | 与文件元数据、正文证据核对 |
| 章节结构 | 提出层级 | 定位必须落在真实文本范围 |
| 语义切片 | 建议合并/拆分 | 原文和 locator 不得变化 |
| 摘要 | 生成 | 标记为派生，可重建 |
| 实体/Claim | 生成候选 | Schema、来源和敏感过滤 |

### 7.2 查询阶段

| 环节 | LLM 参与 | 程序裁决 |
|---|---|---|
| 意图识别 | 判断问题类型 | 高精度命令优先 |
| 检索源选择 | 建议 Knowledge/Memory/History/Life 等 | 根据用户设置和权限放行 |
| 查询拆解 | 生成子查询 | 限制数量、长度和允许源 |
| 时间/版本范围 | 解析候选 | 程序计算日期和过滤 |
| 候选重排 | 选择真正相关证据 | 来源 active、预算和去重 |
| 证据支持度 | 判断 direct/partial/conflict | 引用存在性和 locator 校验 |
| 回答结构 | 规划结论、比较、不确定性 | 最终回答仍受聊天安全策略 |

### 7.3 维护阶段

| 环节 | LLM 参与 | 程序裁决 |
|---|---|---|
| 重复文档 | 判断语义重复 | hash/版本/用户确认 |
| 冲突关系 | 建议 relation | 最新纠正、日期和来源优先 |
| 过时风险 | 提出 possibly_stale | 不自动下线 |
| 实体合并 | 提出候选 | 低置信度不合并，用户可确认 |
| Episode/Saga | 提出叙事边界 | MEM Validator 应用 |
| 清理建议 | 提出维护候选 | 不自动删除原文 |

### 7.4 统一结构化协议

所有 LLM 决策采用严格 JSON Schema，至少包含：

```json
{
  "protocol_version": "...",
  "decision": "...",
  "confidence": 0.0,
  "selected_source_ids": [],
  "evidence": [],
  "warning_codes": [],
  "proposal": {}
}
```

要求：

- 模型不可返回未知 decision 枚举。
- source ID 必须来自输入候选白名单。
- 不接受模型生成的新 ID、路径和页码。
- 允许一次结构修复；仍失败则回退。
- 原始模型输出不落库。
- 低置信度不写正式状态。
- Provider 切换不能改变边界规则。

---

## 8. 文档接收与索引流水线

### 8.1 Intake 状态机

```text
selected
  ↓
validating
  ↓
accepted
  ↓
parsing
  ↓
parsed
  ↓
chunking
  ↓
indexing_lexical
  ↓
indexing_dense
  ↓
semantic_enrichment（可选）
  ↓
ready
```

失败状态：

```text
rejected / parse_failed / partial / embedding_failed /
semantic_failed / stale_pending_rebuild / deleted
```

### 8.2 原文解析

首版优先支持：

```text
TXT / Markdown / PDF 文本层 / DOCX / 常见代码和 JSON/YAML
```

后续单独扩展：

```text
复杂扫描 PDF OCR / PPTX 视觉结构 / 图片 / 音视频字幕 / 网页快照
```

规则：

- PDF 有文本层时优先直接解析，不默认 OCR。
- 表格尽可能保留行列和页码。
- 代码文件保留路径、语言和符号范围。
- DOCX 保留标题层级、表格和段落序号。
- 解析器版本升级后支持按文档重建。

### 8.3 切片策略

采用“确定性结构优先、LLM 语义修正可选”的混合方案：

1. 先按标题、段落、列表、代码块、表格和页码建立结构块。
2. 超过 token 上限的结构块按句子或符号范围继续拆分。
3. 过短且语义依赖明显的相邻块可由规则合并。
4. 高价值文档可请求 LLM 建议边界。
5. 每个 chunk 保存 `heading_path` 和前后邻居 ID。
6. 检索时允许邻居扩展，但不能无预算地整章注入。

### 8.4 索引层

```text
Lexical Index     SQLite FTS/BM25
Dense Index       Embedding 向量
Metadata Index    类型、日期、版本、项目、标签、语言、状态
Graph Projection  Entity/Relation/Claim/Event
```

首版不引入独立图数据库。PWM 使用 SQLite 规范化表和索引；确认查询瓶颈后再评估图数据库，避免过早增加运维复杂度。

### 8.5 Embedding 治理

每个向量记录：

```text
provider_id
model_id
embedding_dimension
normalization
input_hash
created_at
index_version
```

规则：

- 不同模型向量不能在同一空间直接比较。
- 切换 Embedding 模型时建立新索引版本，旧索引可并存直到重建完成。
- 远程 Embedding 必须受数据传输策略控制。
- Embedding 失败不阻塞 Lexical 检索。

---

## 9. 查询规划与多源检索

### 9.1 QueryIntent

```text
factual_lookup              查明确事实
source_lookup               找原文、文件、页码
historical_recall           回忆过去具体说过什么
personal_memory             查询用户偏好或长期事实
project_status              查询项目当前状态
compare_versions            比较新旧方案
explain_decision            解释为何做出某个决定
self_timeline               查询遐蝶自己的经历
important_date              查询日期、约定、纪念日
procedural_help              查步骤和操作方法
open_ended_synthesis         多来源综合
unknown
```

### 9.2 QueryPlan

```text
QueryPlan
├─ intent
├─ subqueries[]
├─ requested_sources[]
├─ excluded_sources[]
├─ entity_hints[]
├─ time_range
├─ version_scope
├─ needs_original_quote
├─ needs_conflict_analysis
├─ needs_web_freshness
├─ max_candidates_per_source
└─ confidence
```

### 9.3 本地规则优先

以下不必调用 Query Planner LLM：

- 用户明确选择某个文档问答。
- 用户点击“在本文件中搜索”。
- 用户明确说“找到我上次说的原话”。
- 用户点击某个实体、日期或项目页。
- 用户执行删除、重建、导出等命令。

LLM 用于模糊、复合和跨库问题。

### 9.4 多源召回

各源独立召回后统一为 `RetrievalCandidate`：

```text
source_type
source_id
locator
excerpt
lexical_score
vector_score
metadata_match
recency
freshness_state
source_authority
candidate_role
```

检索源：

```text
knowledge_document
conversation_history
memory_fragment
memory_episode
memory_saga
life_event / diary / self_timeline
task / tool_run
lore
external_search（后续）
```

### 9.5 不使用一套总分决定一切

本地召回分数用于缩小候选，不直接等同于最终相关性。至少区分：

```text
retrieval_match      文本或向量是否匹配
source_authority     来源是否权威
temporal_validity    当前是否仍有效
query_role           直接证据、背景、反例还是冲突
```

这些不同含义不能简单压成一个不可解释分数。

---

## 10. LLM 语义重排

### 10.1 输入限制

- 每次重排只接收 10～50 个短候选。
- 每个候选带 ID、来源类型、短文本、时间、版本和状态。
- 不把整个文档发送给重排模型。
- 私密来源遵守 Provider 传输策略。
- 候选正文作为不可信数据封装。

### 10.2 输出

```text
selected[]
├─ candidate_id
├─ relevance_role
│  direct_support / partial_support / background /
│  contradiction / outdated / duplicate / irrelevant
├─ rank_bucket
├─ confidence
└─ short_reason_code
```

### 10.3 本地最终验证

重排后必须再次检查：

1. 候选来源仍 active。
2. source revision/hash 未变化。
3. 用户未关闭该来源类型。
4. 文档未删除、记忆未 revoked、聊天未归档禁用。
5. locator 可以读取原文。
6. 去除重复和近重复片段。
7. 仍满足 ContextAssembler 预算。
8. 低置信度冲突不得被当作最终事实。

### 10.4 回退

LLM 不可用时：

```text
Metadata hard filter
  ↓
FTS + Dense rank fusion
  ↓
来源权威和新鲜度过滤
  ↓
MMR/去重
  ↓
CTX
```

聊天不能因为重排模型失败而停止。

---

## 11. 证据、引用与答案支持度

### 11.1 EvidenceLink

```text
EvidenceLink
├─ claim_or_answer_segment_id
├─ source_ref_id
├─ relation
│  direct_support / partial_support / background /
│  contradiction / example / definition
├─ excerpt_hash
├─ locator_snapshot
├─ validated_at
└─ status
```

### 11.2 引用规则

- 引用必须来自真实 SourceRef。
- 展示时尽量链接到文档、页码、段落或原会话。
- 不能只引用文档标题而没有定位。
- 同一结论涉及多个来源时支持多引用。
- 对用户记忆的回答可引用记忆卡和原聊天证据，但普通陪伴对话不必每句技术化展示。
- 用户询问“为什么记得”或“来源是什么”时可以展开。

### 11.3 Claim Support Check

回答生成前或后，对高风险、复杂比较和多来源结论进行支持度检查：

```text
supported
partially_supported
conflicted
insufficient
not_checkable
```

处理：

- `supported`：正常回答并引用。
- `partially_supported`：明确限定范围。
- `conflicted`：展示主要分歧，不擅自选择。
- `insufficient`：说明资料不足，必要时建议继续检索。
- `not_checkable`：区分观点、建议和事实。

### 11.4 引用不能被人格风格弱化

遐蝶可以用自然语气解释，但不能为了“像伴侣”把不确定内容说成确定事实。事实准确性高于表达亲密度。

---

## 12. 冲突、版本与新鲜度

### 12.1 关系枚举

```text
exact_duplicate
semantically_equivalent
compatible
compatible_with_conditions
extends
partially_supersedes
supersedes
contradicts
divergent_branch
unrelated
uncertain
```

### 12.2 冲突处理优先级

```text
用户最新明确纠正
  > 用户确认的 authoritative 文档
  > 正式 ToolRun / 当前系统状态
  > 新版本且同一适用范围的来源
  > 稳定官方来源
  > 其他导入资料
  > 模型推断
```

“新”不自动等于“正确”，必须确认是同一对象和适用范围。

### 12.3 FreshnessState

```text
current
possibly_stale
deprecated
superseded
expired
unknown
```

决定因素：

- 文档有效日期和版本。
- 同主题更新来源。
- 用户确认的权威级别。
- 软件/API/政策等时效类别。
- 外部联网验证结果（后续）。
- 模型只可提出 stale 风险，不能单独判定 deprecated。

### 12.4 条件与时间避免伪冲突

例如：

```text
“用户早上喜欢喝咖啡”
“用户晚上不喜欢喝咖啡”
```

需要通过 qualifiers 判断为条件兼容，而非互相覆盖。

```text
“当前先用 Electron”
“长期可能评估 Tauri”
```

需要区分当前决策与未来候选。

### 12.5 用户确认

以下情况建议请求用户确认：

- 两份同级权威文档存在关键冲突。
- 实体合并会影响大量记忆或引用。
- 新来源可能替代用户手动维护的重要条目。
- 日期、人物或项目指代含糊。
- 删除一个来源会使多个派生对象失去唯一证据。

---

## 13. Personal World Model（PWM）

### 13.1 定位

PWM 是导航和关联层，不是事实权威层。它帮助遐蝶理解：

```text
谁
在做什么
涉及哪个项目
使用哪些文件和工具
何时发生
与哪些目标、决定和事件有关
```

### 13.2 首版实体范围

```text
User
Xiadie Agent
Project
Document
Repository
Model / Provider
Tool
Task
Goal
Person
Organization
Place
Concept
ImportantDate
Event
```

不首版自动建模：

```text
医学诊断
人格评分
政治/宗教推断
收入/资产推断
亲密关系推断
未被用户明确提供的现实身份信息
```

### 13.3 项目视图示例

```text
Project: 遐蝶 Agent
├─ current_stage: 设计与基础开发
├─ uses: FastAPI / SQLite / React / Electron
├─ documents:
│  ├─ 总体设计
│  ├─ CTX 施工计划
│  ├─ EAP 施工计划
│  └─ LIFE 施工计划
├─ goals:
│  ├─ 连续陪伴
│  ├─ 复杂任务执行
│  └─ 多模型支持
├─ events:
│  ├─ 回归单主窗口
│  ├─ 冻结固定 Live2D 模型
│  └─ 新增生活连续性设计
└─ related_entities:
   ├─ 用户
   └─ 遐蝶
```

每一项必须能回到 SourceRef。

### 13.4 实体消歧流程

```text
Alias exact match
  ↓
同一 scope 和类型候选
  ↓
LLM 判断同一性
  ↓
程序检查来源、时间和冲突
  ↓
高置信度自动链接 / 中置信度保持候选 / 高影响请求确认
```

### 13.5 状态投影

PWM 只保存跨系统只读状态投影：

- 当前项目阶段来自 Task/Memory/文档。
- 用户当前临时状态来自 EAP，带过期时间。
- 遐蝶当前活动来自 LIFE。
- 当前权威文档来自用户确认或版本关系。

PWM 不应自行成为状态更新源。

### 13.6 图谱召回

用户问：

> “我们为什么从旧 UI 改成现在这样？”

PWM 可找到：

```text
Project: 遐蝶
  ├─ event: UI 回归单主窗口
  ├─ older_document
  ├─ newer_document
  ├─ reason_claims
  └─ related_conversation
```

随后仍由各源检索原文，不能仅用图谱摘要回答。

---

## 14. 与记忆、对话、生活和任务的治理接口

### 14.1 Memory Proposal API

KIG 可以输出：

```text
MemoryClassificationProposal
MemoryConflictProposal
EpisodeBoundaryProposal
SagaTransitionProposal
MemoryRecallRanking
```

MEM 系统负责：

- Grounding。
- Kind 规则。
- 敏感过滤。
- 生命周期。
- 正式写入。

### 14.2 Conversation Interface

KIG 只索引允许参与历史召回的会话：

- 当前会话原文。
- 未删除、未归档排除的普通会话。
- 临时聊天默认不进入跨会话索引。
- 关闭“参考聊天历史”后，不检索其他会话。
- 删除聊天不自动删除独立长期记忆；影响范围由 CTX/MEM 既定规则处理。

### 14.3 LIFE Interface

KIG 可读取：

```text
LifeEvent
DiaryEntry
ImportantDate
PersonalGoal
SelfTimeline result
```

限制：

- `planned` 不能回答成已发生。
- `simulated_world/inferred` 不能回答成真实工具执行。
- Diary 不能作为用户事实的唯一来源。
- `private` 日记只影响默认分享，不阻止用户在本地管理。

### 14.4 Task/Tool Interface

- Task 结果可作为项目状态来源。
- ToolRun 是真实执行的权威来源。
- 失败 ToolRun 不能生成成功 Claim。
- 命令输出可能包含敏感信息，索引前必须应用脱敏和存储策略。
- 高风险工具权限仍由 ToolRegistry/PermissionPolicy 管理。

### 14.5 Lore Interface

- Lore 与现实知识分开索引。
- 用户询问角色世界时优先 Lore。
- 用户询问现实事实时不得用 Lore 冒充现实来源。
- 同名实体可以在 `reality_scope` 和 `lore_scope` 中分别存在。

---

## 15. 知识维护与巩固

### 15.1 MaintenanceCandidate

后台低频生成：

```text
duplicate_document
possible_new_version
stale_document
orphan_chunk
broken_source
conflicting_claims
unused_collection
missing_metadata
entity_merge_candidate
entity_split_candidate
reindex_required
```

只建立候选，不自动删除。

### 15.2 文档去重

优先级：

1. 文件 hash 完全相同：确定性重复。
2. 解析文本 hash 相同：内容重复。
3. 高语义相似：LLM 建议可能重复。
4. 同名不同版本：不得仅因相似自动去重。

用户可以选择：

```text
保留两份
标记为新版本
归档旧版本
删除重复副本
```

### 15.3 再索引

触发：

- Parser 版本升级。
- Chunk 策略升级。
- Embedding 模型变化。
- 来源文件变化。
- 用户手动重建。

要求：

- 新索引在完成前不删除旧索引。
- 切换采用原子版本指针。
- 失败后继续使用旧索引并显示状态。

### 15.4 反馈学习

可以记录：

```text
用户打开了哪个来源
用户纠正了哪个回答
用户标记“这条无关”
用户选择了哪个同名实体
用户确认哪个文档是当前版本
```

反馈只能调整检索偏好和治理候选，不能让模型自行修改用户事实。

---

## 16. 用户界面与交互

### 16.1 知识库主页

建议布局：

```text
左侧：集合 / 项目 / 标签 / 最近导入
中间：文档列表与状态
右侧：文档详情、版本、来源、索引和关联实体
```

用户可执行：

- 导入文件或文件夹。
- 查看解析和索引状态。
- 搜索、问答和打开原文。
- 编辑标题、标签、项目和权威级别。
- 重新索引、归档、删除和导出。
- 查看“可能有新版本”“与另一文档冲突”。

### 16.2 对话中的来源体验

普通回答下方可显示轻量来源条：

```text
参考：3 个资料片段 · 1 条过往对话
```

点击展开：

- 文件名、页码/章节。
- 原文短片段。
- 打开原文件或原会话。
- “这条无关”“版本已过时”“不要使用这个来源”。

陪伴闲聊不强制每句话显示来源；涉及事实、设计决定、比较和文件问答时显示。

### 16.3 项目/实体页

首版只做有价值的实体页：

- 项目。
- 文档。
- 重要人物。
- 模型/工具。
- 重要日期。

展示：

```text
概览
相关文件
相关记忆
相关对话
事件时间线
当前状态
冲突和版本
```

### 16.4 删除影响预览

删除文档前显示：

```text
将删除：原文件索引、Chunk、Embedding
将失效：7 条 Claim、2 个实体关系、1 个项目事件
不会自动删除：独立聊天、用户确认的长期记忆
```

如果派生对象仍有其他来源，保留并移除当前证据。

### 16.5 设置

```text
知识库总开关
自动语义增强
本地/远程 Embedding
远程 LLM 是否可读取文件内容
默认引用显示
参考聊天历史
已保存记忆
个人世界模型
后台维护频率
每次检索 token 预算
```

用户不需要设置内部权重。

### 16.6 开发者诊断

可查看：

- QueryPlan。
- 每个源候选数量。
- Lexical/Dense 命中。
- LLM 重排选择。
- 冲突和新鲜度警告。
- ContextAssembler 最终注入。
- 模型、协议、token、延迟和回退。

不得默认复制完整私密正文到日志。

---

## 17. 隐私、安全与数据治理

### 17.1 隐私级别

```text
public_like       普通公开资料
private           用户私人资料
sensitive         身份、财务、健康、私密关系等
restricted        用户明确限制仅本地或禁止 LLM
```

### 17.2 Provider 传输策略

```text
local_only
allow_embedding_remote
allow_rerank_remote
allow_summary_remote
allow_full_remote
```

每次模型任务检查来源中最严格策略；不能因多个候选混合而降低限制。

### 17.3 敏感数据

- API Key、密码、验证码、私钥和访问令牌不得进入知识索引、日记、Claim 或世界模型。
- 工具日志索引前必须脱敏。
- 个人世界模型不得自动建立敏感画像。
- 用户明确“不要记录/不要用于回答”的内容建立硬边界。

### 17.4 删除与导出

用户可以分别导出/删除：

```text
原始知识文件
解析文本与索引
Claim/Entity/Relation/Event 派生层
检索和维护元数据
```

删除派生层不删除原始文件；删除原始文件按影响预览处理派生层。

### 17.5 审计

审计记录保存：

- 操作类型。
- 对象 ID。
- 版本。
- 状态变化。
- 错误码。
- 模型/协议元数据。

不保存：

- 不必要的全文。
- 原始模型输出。
- 明文秘密。

---

## 18. 性能、成本与模型路由

### 18.1 调用原则

- 文件接收安全、解析、基础切片和 FTS 不调用 LLM。
- 普通单文档明确问答可以跳过 Query Planner。
- 只有候选多、问题模糊或跨库时调用重排。
- Claim/实体抽取按需和后台批处理，不要求所有文档全量抽取。
- 维护模型低频运行，不和聊天延迟绑定。

### 18.2 模型角色

```text
fast
  查询意图、分类、小候选重排

reasoning
  多来源冲突、版本关系、Episode/Saga、复杂证据融合

creative
  文档摘要和面向用户解释，不负责事实裁决

embedding
  稠密召回
```

### 18.3 预算

建议默认：

- Query Planner 子查询最多 5 条。
- 每源本地候选最多 20～40 条。
- LLM 重排总候选最多 50 条。
- 最终知识证据通常 4～10 条。
- Claim 抽取只处理命中或高价值 Chunk。
- 单次维护任务有文档数和 token 上限。

最终值通过 KIG.0 基线和模拟校准，不在设计阶段永久冻结。

### 18.4 缓存

可以缓存：

- 相同 query hash + source revision 的检索结果。
- 文档摘要和章节结构。
- Embedding。
- 实体候选和版本关系。

不能跨以下变化复用：

- 用户删除/关闭来源。
- 来源 revision 变化。
- 用户纠正。
- Provider 数据策略变化。
- 模型协议版本变化。

---

## 19. 分阶段施工计划

### KIG.0：当前实现全量审计与边界冻结

> v0.2 修订（2026-07-21）：第 0.1 节已完成初步代码审计，标注了 18 项已完整实现、6 项部分实现、6 项未实现、5 项由其他专项拥有的能力。KIG.0 阶段在施工时需在此基础上深化。

目标：确认真实知识库能力，不把旧设计或未接线骨架当成完成。

- [x] 初步审查知识接收、解析、切片、FTS、Embedding、检索、引用、UI、API、迁移和测试（第 0.1 节已完成）
- [x] 初步审查 CTX、Fragment/Episode/Saga、LIFE、Task/ToolRun 和 Lore 的现有接口（第 0.1 节已完成）
- [x] 初步建立 `[x]/[~]/[ ]/[→]/[-]` 能力矩阵（第 0.1 节已完成）
- [x] 明确已冻结协议边界（第 0.2 节已完成，14 个已冻结协议）
- [x] 明确 Schema 版本断言（第 0.3 节已完成，KIG 新表从 LIFE 之后第一个可用版本起）
- [ ] 施工前深化：记录 20 个单文档、20 个多文档、20 个跨知识/记忆问题基线
- [ ] 施工前深化：记录召回率、引用准确率、延迟、token 和失败模式
- [ ] 新增 ADR：KIG 是治理和投影层，不是大一统正文数据库
- [ ] 新增 ADR：LLM 提议、程序裁决；PWM 不是事实权威
- [ ] 列出权威文档优先级和与 CTX/MEM/EAP/LIFE 的所有权边界

完成门：

- [ ] 后端、前端和 Electron 当前基线通过
- [ ] 0 个未解决的职责冲突
- [ ] 现有完整能力直接勾选，不重写

建议 PR：`docs(kig): audit and freeze knowledge governance boundaries`

### KIG.1：统一 SourceRef 与来源状态

目标：所有知识和派生对象可回到真实来源。

- [ ] 建立 SourceRef、locator、revision、hash 和 status 模型。
- [ ] 为文档、Chunk、消息、记忆、LifeEvent、ToolRun、Lore 建立适配器。
- [ ] 来源变化触发派生对象 stale。
- [ ] 删除和不可访问状态可传播。
- [ ] 不复制不必要正文。
- [ ] 建立来源定位 API 和测试。

验收：任一引用、Claim、关系和事件都能回到原来源；伪造 locator 通过率为 0。

建议 PR：`feat(kig): add unified provenance and source references`

### KIG.2：KnowledgeDocument 与索引版本治理

> v0.2 修订（2026-07-21）：现有 `knowledge.py`、`knowledge_parser.py`、`knowledge_chunker.py`、`knowledge_embeddings.py` 已实现大部分功能。KIG.2 阶段只补差距，不重写。

目标：统一文档、解析器、Chunk 和索引状态。

**已实现状态**：

- [x] 知识文档表（`knowledge_documents`）：content_sha256、sensitivity、transmission_policy、parser_version、chunker_version、tags_json
- [x] 知识切片表（`knowledge_chunks`）：document_id、chunk_index、heading_path、content_sha256、ordinal、char_start、char_end
- [x] FTS5 词法索引（`knowledge_chunks_fts`）：BM25 排序
- [x] Embedding 向量索引：provider_id、model_id、dimension 记录
- [x] 文档去重（content_sha256 UNIQUE 约束）
- [x] 敏感文档 transmission_policy 强制 local_only

**KIG.2 补差距**：

- [ ] 审查并兼容现有知识表，不无条件迁移（已实现部分需确认是否需要扩展）
- [ ] 增加 Embedding 版本和索引状态字段（现有缺 index_version 字段）
- [ ] 建立原子索引切换和失败回退（现有缺版本指针切换机制）
- [ ] 支持文档重建、归档、删除和影响预览（现有 `knowledge_cleanup.py` 只处理 orphan，缺影响预览）
- [ ] FTS 失败和 Dense 失败可独立降级（现有 `knowledge_search.py` 已部分实现，需确认）

验收：旧索引在重建完成前可用；失败不导致文档不可查询。

建议 PR：`feat(knowledge): version documents chunks and indexes`

### KIG.3：信息分类与目标路由

目标：区分 Knowledge、Memory、Conversation、Life、Lore 和 Task Result。

- [ ] 定义 information-classifier-v1 Schema。
- [ ] 高精度命令和来源类型先由程序判断。
- [ ] LLM 只处理模糊场景。
- [ ] 输出 destination proposal，不直接写目标库。
- [ ] 目标系统重新验证。
- [ ] 建立临时状态、长期偏好、观点和计划的误判集。

验收：普通临时要求不会变成永久偏好；外部事实不会污染用户记忆。

建议 PR：`feat(kig): add validated information classification routing`

### KIG.4：文档语义增强与结构化切片

目标：改善章节、表格、代码和语义边界，同时保留原文真实性。

- [ ] 建立结构优先切片器。
- [ ] 保存 heading path、页码、邻居和 chunk kind。
- [ ] 增加可选 LLM 边界建议。
- [ ] 模型不得重写 raw_text。
- [ ] 建立不同文档类型的切片质量集。
- [ ] 模型失败回退确定性切片。

验收：定义、步骤、警告、表格和代码上下文不被明显错误切断；原文 hash 不变。

建议 PR：`feat(knowledge): add provenance-safe semantic chunking`

### KIG.5：Query Planner 与多库路由

> v0.2 修订（2026-07-21）：现有 `knowledge_search.py hybrid_search` 已实现基础查询（`knowledge-search-v2` 协议）。KIG.5 阶段在此基础上补 QueryIntent 分类和 QueryPlan 子查询拆解，不修改已冻结协议。

目标：在检索前决定问题类型、来源和子查询。

**已实现状态**：

- [x] 基础混合检索（`knowledge_search.py hybrid_search`，`knowledge-search-v2` 协议）
- [x] 单文档查询（`search` 函数）
- [x] BM25 + 向量分数融合重排（`_re_rank` 函数）
- [x] 多样性选择（`_diversity_select` 函数）

**KIG.5 补差距**：

- [ ] 定义 `query-plan-v1` 协议（不修改已冻结的 `knowledge-search-v2`）
- [ ] 明确单文档和显式来源问题跳过 Planner（现有已部分实现）
- [ ] 支持 Knowledge/Memory/History/Life/Task/Lore 源选择（现有只支持 Knowledge）
- [ ] 支持时间、版本、实体、原话和冲突需求
- [ ] 用户关闭某源后 Planner 建议也不得放行
- [ ] 建立提示注入和模糊指代测试

**已冻结协议边界**：

- 不修改 `knowledge-search-v2` 协议的 search_protocol_version
- 不修改 `knowledge-recall-decision-v1` 协议
- `query-plan-v1` 是新协议，独立于已冻结协议

验收：跨库问题能选择正确来源；普通明确查询不增加无意义模型调用。

建议 PR：`feat(retrieval): add bounded query planning and source routing`

### KIG.6：混合召回与候选统一

> v0.2 修订（2026-07-21）：现有 `knowledge_search.py` 已实现 FTS + Dense 混合召回和多样性选择。KIG.6 阶段扩展为多源统一候选，不重写现有知识库召回。

目标：统一 FTS、Dense、Metadata 和图投影候选。

**已实现状态**：

- [x] FTS5 词法召回（`knowledge_search.py search`）
- [x] Dense 向量召回（`knowledge_search.py hybrid_search`）
- [x] 去重和多样性选择（`_diversity_select`）
- [x] 候选带 chunk_id + locator + excerpt

**KIG.6 补差距**：

- [ ] 定义统一 `RetrievalCandidate` schema（扩展现有候选为跨源统一格式）
- [ ] 接入现有 FTS 和向量实现，已有能力直接复用（已实现）
- [ ] 增加 metadata filter、日期、版本和状态过滤
- [ ] 建立各源独立候选上限
- [ ] 去重、邻居扩展和多样性选择（知识库内已实现，需扩展到跨源）
- [ ] Dense 不可用时使用 Lexical 回退（已实现）
- [ ] 新增 Memory/History/Life/Task/Lore 源的召回适配器

验收：单一源故障不阻塞查询；候选均带来源、状态和 locator。

建议 PR：`feat(retrieval): unify hybrid multi-source candidates`

### KIG.7：LLM 语义重排

> v0.2 修订（2026-07-21）：现有 `knowledge_search.py _re_rank` 使用确定性融合（BM25 + 向量分数）。KIG.7 阶段补充 LLM 语义重排，与现有确定性重排并行（Shadow 模式）。

目标：让模型在有限候选中判断真正相关性，不直接执行检索或写状态。

**已实现状态**：

- [x] 确定性重排（`knowledge_search.py _re_rank`，BM25 + 向量分数融合）
- [x] 多样性选择（`_diversity_select`）

**KIG.7 补差距**：

- [ ] 定义 `retrieval-rerank-v1` 协议（新协议，不修改已冻结的 `knowledge-search-v2`）
- [ ] 只允许返回输入候选 ID
- [ ] 区分 direct、partial、background、conflict、outdated、duplicate、irrelevant
- [ ] 来源变化后拒绝旧重排结果
- [ ] 模型失败使用确定性融合（回退到现有 `_re_rank`）
- [ ] Shadow 模式对比旧排序（参考 EAP v0.2 Shadow 基线模式）

**与 EAP v0.2 Shadow 模式的一致性**：

- KIG.7 Shadow 模式与 EAP.F Shadow 模式遵循相同原则：旧确定性算法降级为 Shadow 基线，并行运行用于回放比较
- Shadow 模式不影响主链，失败不阻塞聊天

验收：人工相关性显著高于旧排序；引用不存在率为 0。

建议 PR：`feat(retrieval): add validated LLM semantic reranking`

### KIG.8：证据、引用与支持度

目标：回答可以被来源证明，资料不足时不编造。

- [ ] 建立 EvidenceLink。
- [ ] 增加 locator 验证和原文打开入口。
- [ ] 定义 claim-support-v1。
- [ ] 复杂问题执行支持度检查。
- [ ] 冲突和不足进入 ContextBundle。
- [ ] UI 展示轻量来源条。

验收：引用 100% 可打开或明确标记来源不可访问；资料不足时不生成伪引用。

建议 PR：`feat(knowledge): add grounded evidence and citation support`

### KIG.9：冲突、版本与新鲜度

目标：处理新旧设计、软件版本、条件差异和用户纠正。

- [ ] 建立 VersionRelation 和 FreshnessState。
- [ ] 确定性 hash/date/version 规则先行。
- [ ] LLM 只提出语义 relation。
- [ ] 用户最新纠正和 authoritative 标记优先。
- [ ] 高影响冲突请求确认。
- [ ] 建立版本分支、部分替代和条件兼容测试。

验收：新旧文档不会无提示混合；时间条件不同不误判为冲突。

建议 PR：`feat(kig): add conflict version and freshness governance`

### KIG.10：Claim、Entity、Relation 与 WorldEvent

目标：建立个人世界模型的来源化数据底座。

- [ ] 新增 Claim、Entity、Alias、Relation、WorldEvent、StateAssertion 表。
- [ ] 使用白名单实体类型和 Predicate。
- [ ] 先在 shadow 模式抽取。
- [ ] 所有对象必须有 SourceRef。
- [ ] 模型推断默认不可独立支持事实回答。
- [ ] 敏感属性自动抽取禁用。

验收：无来源对象写入率为 0；普通对话不产生大量无意义节点。

建议 PR：`feat(pwm): add sourced claims entities relations and events`

### KIG.11：实体消歧、合并与拆分

目标：识别别名，同时避免错误合并。

- [ ] 规则 exact alias 和 scope 初筛。
- [ ] LLM 同一性建议。
- [ ] 高影响合并要求用户确认。
- [ ] 支持拆分、关系迁移和影响预览。
- [ ] 现实/Lore scope 分离。
- [ ] 建立同名人物、项目简称和跨语言别名测试。

验收：错误自动合并率达到严格门槛；所有合并可回滚。

建议 PR：`feat(pwm): add reversible entity resolution`

### KIG.12：与 Fragment/Episode/Saga 和 LIFE 的治理接线

目标：复用现有系统，不重写其内部状态机。

- [ ] MemoryClassificationProposal 接口。
- [ ] MemoryConflictProposal 接口。
- [ ] EpisodeBoundaryProposal 和 SagaTransitionProposal 接口。
- [ ] LIFE SelfTimeline 只读召回适配。
- [ ] ToolRun 权威来源适配。
- [ ] ContextAssembler 接收统一 RetrievalBundle。
- [ ] 各系统的关闭、临时聊天和隐私设置生效。

验收：KIG 关闭后原有 Memory/CTX/LIFE 行为可继续；无第二套长期记忆写入器。

建议 PR：`feat(kig): integrate memory life task and context governance`

### KIG.13：知识维护与巩固

目标：长期运行后仍可发现重复、失效、冲突和重建需求。

- [ ] MaintenanceCandidate 表和 worker。
- [ ] 确定性重复检查。
- [ ] LLM 语义重复和旧版本建议。
- [ ] 孤立 Chunk、失效来源和索引异常检测。
- [ ] 只生成候选，不自动删除。
- [ ] 用户维护反馈反哺检索偏好。

验收：后台维护不阻塞聊天；未确认删除率为 0。

建议 PR：`feat(kig): add non-destructive knowledge maintenance`

### KIG.14：知识库与世界模型 UI

目标：让用户管理来源、版本、冲突和关联，而不是管理内部算法。

- [ ] 知识库主页和集合视图。
- [ ] 文档详情、索引状态和版本关系。
- [ ] 来源展开和原文入口。
- [ ] 项目/实体页和事件时间线。
- [ ] 删除影响预览。
- [ ] 数据传输与模型设置。
- [ ] 开发者检索诊断。

验收：普通用户不需要理解 BM25、向量和图谱即可完成导入、问答、纠正、归档和删除。

建议 PR：`feat(ui): add knowledge governance and world model views`

### KIG.15：长期模拟、校准与总验收

目标：冻结 KIG v1。

- [ ] 后端全量测试通过。
- [ ] 前端测试、TypeScript、Vite 和 Electron 检查通过。
- [ ] 1 万、10 万和目标规模 Chunk 压力测试。
- [ ] 100 个单文档、100 个多文档、100 个跨库问题评测。
- [ ] 100 个版本冲突与用户纠正场景。
- [ ] 100 个实体消歧和合并回滚场景。
- [ ] Provider 切换、离线、远程受限和预算不足测试。
- [ ] 引用准确率、召回率、重排增益和延迟报告。
- [ ] 更新所有权威文档和迁移说明。
- [ ] 0 个未解决 P0/P1。

冻结标准：

```text
伪造来源/locator 率                   = 0
用户关闭来源后仍检索率                = 0
无来源 Claim/Relation/Event 写入率     = 0
自动删除原文率                        = 0
旧索引重建失败导致知识不可用率         = 0
planned/inferred 被当作真实执行率       = 0
明确用户纠正被旧来源覆盖率             = 0
引用可打开或明确不可访问率             = 100%
跨库路由人工正确率                    ≥ 90%
LLM 重排相对旧排序人工增益              ≥ 15%
复杂回答证据适当性                    ≥ 90%
实体自动合并精确率                    ≥ 98%
```

建议 PR：`feat(kig): complete and freeze knowledge intelligence v1`

---

## 20. 必测场景矩阵

### 20.1 文件与索引

| 场景 | 预期结果 |
|---|---|
| 导入相同文件两次 | 确定性提示重复，不静默复制索引 |
| 同名不同版本 | 保留两份，建立版本候选 |
| PDF 只有扫描图 | 不进行高成本 OCR 或明确进入待处理 |
| DOCX 有表格和标题 | 保留结构和 locator |
| Embedding 失败 | FTS 仍可查询 |
| 重建失败 | 继续使用旧索引 |
| 文件被删除 | SourceRef missing，派生对象失效 |
| Provider 禁止传输 | 不向远程模型发送正文 |

### 20.2 分类

| 输入 | 预期归属 |
|---|---|
| “我最近暂时不想做游戏自动化” | 临时状态/计划状态，不是永久偏好 |
| “游戏自动化是长期目标” | 长期计划候选 |
| “FastAPI 是 Python 框架” | 外部知识 |
| “我更喜欢单主窗口” | Preference 候选 |
| 小说中人物说“我很难过” | 不当作用户状态 |
| 工具执行成功 | ToolRun 事实来源 |
| 工具计划执行 | 不是成功事实 |

### 20.3 查询路由

| 用户问题 | 预期来源 |
|---|---|
| “这个 PDF 第三章说了什么” | 当前文档 |
| “我们为什么改回单窗口” | 历史对话 + 设计文档 + Episode |
| “我以前说过喜欢什么 UI” | Fragment + 原对话证据 |
| “你昨天下午做了什么” | LIFE SelfTimeline |
| “任务是否真的完成” | Task + ToolRun |
| “遐蝶设定里她来自哪里” | Lore |
| “当前 API 最新规则” | 本地知识；可能需要新鲜度/联网提示 |

### 20.4 冲突和版本

- 新版本完全替代旧版本。
- 新版本只替代某一章节。
- 两个分支同时有效。
- 旧文档上传时间晚但内容版本更旧。
- 用户明确说某份文档是当前权威。
- 当前决策与长期候选并存。
- 条件不同的偏好不冲突。

### 20.5 证据与引用

- 文件名相同但来源不同。
- 文档删除后旧回答引用。
- Chunk 重建后 locator 变化。
- 多来源支持同一结论。
- 来源只提供背景，不直接支持结论。
- 没有资料支持时拒绝伪造引用。
- 用户要求原话时打开真实消息。

### 20.6 世界模型

- “遐蝶”“Xiadie”“遐蝶 Agent”别名。
- 两个同名人物不自动合并。
- 现实 Cyrene 和 Lore Cyrene scope 分离。
- 项目名称更改但历史事件保持。
- 实体合并后用户要求拆分。
- 删除唯一来源后关系失效。
- 一个事件同时涉及项目、文档和用户。

### 20.7 隐私和设置

- 关闭知识库总开关。
- 关闭参考聊天历史。
- 关闭已保存记忆。
- 临时聊天。
- 文件标记 local_only。
- 日记 private。
- 用户说“不要记录这件事”。
- 删除文档但保留独立记忆。

---

## 21. 数据迁移、回滚与兼容

1. 每阶段使用顺序迁移，不修改历史迁移。
2. 新 KIG 表优先 shadow 上线，不立即改变现有聊天结果。
3. SourceRef 通过适配器引用旧表，不要求第一阶段复制所有旧数据。
4. 新索引版本构建完成前保留旧索引。
5. KIG 总开关关闭后，原有知识库基础检索按兼容模式继续或明确降级，不影响会话和记忆。
6. PWM 可以整表重建；用户手动确认的 merge/split/authority 设置必须导出并重放。
7. 回滚 KIG 不删除原文件、消息、Fragment、Episode、Saga、LifeEvent 和 ToolRun。
8. 删除源数据后派生层不得保留隐藏全文副本。
9. Provider 或 Embedding 变化需要记录重建范围和预计成本。
10. 所有迁移和重建提供进度、失败原因和恢复入口。

---

## 22. 质量指标

### 22.1 检索指标

```text
Recall@K
Precision@K
MRR / NDCG（离线评测）
重复候选率
过时来源命中率
跨库路由准确率
```

### 22.2 证据指标

```text
引用 locator 可用率
结论直接支持率
冲突漏报率
资料不足误答率
用户纠正覆盖率
```

### 22.3 世界模型指标

```text
无来源节点率
实体合并精确率
实体拆分可恢复率
关系类型膨胀率
敏感属性误抽取率
```

### 22.4 产品指标

```text
用户打开来源率
“这条无关”反馈率
重复导入处理成功率
索引失败恢复率
查询首结果延迟
每次回答额外 token 成本
```

指标只用于质量校准，不用于隐蔽地评价或操纵用户。

---

## 23. 推荐 PR 粒度

```text
PR-KIG-001  审计、ADR 与能力矩阵
PR-KIG-002  SourceRef 与来源适配器
PR-KIG-003  文档/Chunk/索引版本治理
PR-KIG-004  信息分类协议与路由建议
PR-KIG-005  结构化切片与 locator
PR-KIG-006  QueryPlan 与多源路由
PR-KIG-007  统一 RetrievalCandidate 与混合召回
PR-KIG-008  LLM 语义重排 Shadow
PR-KIG-009  EvidenceLink 与引用 UI
PR-KIG-010  冲突、版本和新鲜度
PR-KIG-011  Claim/Entity/Relation/Event schema
PR-KIG-012  实体消歧、合并和拆分
PR-KIG-013  Memory/LIFE/Task/Lore/CTX 接口
PR-KIG-014  后台维护候选与重建治理
PR-KIG-015  知识库、项目与实体 UI
PR-KIG-016  长期模拟、校准和文档冻结
```

单个 PR 不同时完成 schema、后台 worker、聊天接入、世界模型和 UI。跨模块变更必须说明接口所有权。

---

## 24. 给后续 Codex 的固定开工指令

```text
请先阅读：
1. docs/CODEX_PROJECT_CONTEXT.md
2. docs/CONVERSATION_CONTEXT_AND_SUMMARY_PLAN.md
3. docs/EMOTION_RELATIONSHIP_AND_PROACTIVE_COMPANION_PLAN.md
4. docs/LLM_DECISION_AND_LIFE_CONTINUITY_PLAN.md
5. docs/KNOWLEDGE_INTELLIGENCE_GOVERNANCE_AND_WORLD_MODEL_PLAN.md
6. docs/PR_CHECKLIST.md

本轮只执行指定的 KIG 子阶段，不提前实现后续阶段。

开始前必须：
- 核对当前代码、schema、测试数、默认分支和最新提交。
- 使用 [x]/[~]/[ ]/[→]/[-] 更新本阶段能力矩阵。
- 已完整实现的功能直接复用，不因计划重叠而重写。
- 明确本阶段允许修改和禁止修改的文件范围。
- 明确是否调用真实 Provider；测试默认使用 mock/fixture。
- 保留用户已有的无关工作区改动，不加入提交。

实现要求：
- 原始文件、消息、记忆、LifeEvent 和 ToolRun 是权威来源。
- LLM 只输出严格结构化建议，程序负责来源、状态、边界、版本、预算、幂等和执行。
- LLM 只能引用输入白名单中的 source/candidate ID。
- 原始模型输出不得落库。
- KIG 不创建第二套 Fragment/Episode/Saga、ContextAssembler、LifeEvent 或主动发送器。
- PWM 是派生视图，不得成为事实权威。
- 失败时保守降级，不阻塞聊天和现有知识检索。
- 日志不复制不必要的文件正文、聊天正文或秘密。

完成后：
- 更新本计划对应勾选项和差距说明。
- 更新 BASELINE_STATUS.md 与 CODEX_PROJECT_CONTEXT.md。
- 运行本阶段专项测试和全量质量门。
- 输出已完成、未完成、已知限制、数据迁移和回滚方式。
- 创建独立本地 Git 提交，提交信息使用本计划建议格式。
```

---

## 25. 风险与应对

| 风险 | 应对 |
|---|---|
| 世界模型变成第二套事实数据库 | 强制 SourceRef，PWM 只做派生投影 |
| LLM 直接把猜测写成事实 | candidate 状态、Validator、低置信度不应用 |
| 各专项重复实现分类和冲突逻辑 | 明确所有权，KIG 提议、目标系统裁决 |
| 向量库命中但语义错误 | LLM 重排 + 来源/版本/支持度校验 |
| LLM 成本和延迟过高 | 本地规则优先、按需 Planner、有限候选、缓存和回退 |
| 文档重建导致引用失效 | 稳定 SourceRef、revision、locator 映射和旧索引过渡 |
| 实体错误合并污染大量关系 | 高精确率优先、可逆合并、高影响确认 |
| 新旧文档混用 | VersionRelation、authoritative 标记、FreshnessState |
| 私密资料被远程模型读取 | 每源 transfer policy、最严格策略合并 |
| 知识和记忆互相污染 | InformationItem 路由建议 + 目标系统独立 Validator |
| 维护 worker 自动删除重要资料 | 只生成 MaintenanceCandidate，不自动删除 |
| 图谱规模膨胀 | 白名单实体/关系、按需 Claim 抽取、归档低价值候选 |

---

## 26. 最终产品体验

完成 KIG v1 后，用户应感受到：

1. 上传一份文件后，遐蝶知道它是什么、属于哪个项目、可能是哪一版本，而不仅是生成一堆向量。
2. 用户问一个模糊问题时，她会自然找到正确的文档、过去对话、记忆或生活时间线，而不是把全部内容混在一起。
3. 她能解释“我们为什么做过这个决定”，并给出当时的设计文档和真实对话来源。
4. 她知道新方案可能替代旧方案，遇到分支和条件差异时不会简单把两者判为矛盾。
5. 她可以展示一个项目涉及哪些文件、目标、决定、事件和工具结果，但每一项都能回到真实来源。
6. 她不会把计划当成完成、把模拟生活当成真实操作、把用户临时情绪当成永久画像。
7. 她在资料不足时会说明不知道，在来源冲突时会展示分歧，而不是为了显得聪明编造结论。
8. 用户能自然地说“这份是旧版”“不要再用这个来源”“这两个其实是同一个项目”“别记录这件事”，系统会可追溯地调整。
9. 普通界面保持陪伴感，只有需要核对事实时才展开来源和版本；内部算法、分数和协议留在诊断层。
10. 随着文件、对话、记忆、生活和项目逐渐积累，遐蝶形成的是一个可纠正、可删除、可重建的个人世界模型，而不是一个不可控的黑箱知识堆。

一句话定义：

> **KIG 让遐蝶不只是“搜到相似文字”，而是能在来源、时间、版本和用户边界内，理解信息属于什么、彼此是什么关系、当前哪些证据值得相信，并把这些证据自然地组织成可靠回答。**

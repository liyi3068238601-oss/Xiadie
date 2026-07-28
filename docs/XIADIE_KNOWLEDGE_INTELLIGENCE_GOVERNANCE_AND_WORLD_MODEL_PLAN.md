# 遐蝶知识智能、信息治理与个人世界模型专项施工计划

- 版本：v0.3（施工基线、双里程碑、来源依赖与图谱治理补强）
- 日期：2026-07-22
- 状态：KIG.0～KIG.9 已完成；当前 Schema 76，进入 KIG-R 冻结门审计（KIG.7 模型质量门待补测）
- 专项代号：`KIG`（Knowledge Intelligence & Governance）
- 子系统代号：`PWM`（Personal World Model）
- 适用范围：用户知识库、信息分类与治理、多源检索、LLM 查询规划与重排、证据与引用、冲突与版本、个人世界模型，以及与对话历史、长期记忆、生活连续性、任务和 ContextAssembler 的接口
- 不包含：SecretStore、ToolRegistry、MCP、多 Agent、外部消息平台、桌面自动化、完整联网研究 Agent、云端多人知识空间
- 施工原则：本计划描述最终能力全集；开始施工前必须审查现有代码、数据库、测试和 UI，已经完整实现的项目直接勾选，部分实现只补差距，不因设计重叠而重写现有功能
- 专项顺序：`CDS → LIFE → KIG`；KIG 是最后一项，不与前两项并行迁移或抢占领域所有权
- 迁移规则：KIG 首个迁移号必须是 LIFE 最终 Schema + 1，不预占固定编号
- 共享规范：`docs/SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md` 是 ConstructionBaseline、所有权、晋级、模型认证、预算和数据生命周期的规范事实源
- 关联专项：
  - `CTX`：对话上下文、滚动摘要与跨会话历史回忆
  - `EAP`：情感、关系积温与主动陪伴
  - `LIFE`：生活连续性、离线世界、日程、日期、日记与 SelfTimeline
  - `MEM`：Fragment、Episode、Saga、Archivist 和记忆观察器

---

## 前置：当前仓库基线与强制施工边界

以下是 KIG.0 开工时必须验证、不得忽略的当前事实：

1. 现有 Knowledge 已完成文档导入、解析与稳定切片、FTS/Dense 混合检索、Embedding、引用与原文定位、删除生命周期、传输策略/授权、搜索 v2、评测与 CTX 接线，并已有 API、UI 和测试。KIG.0 必须逐项给出 `[x]/[~]` 证据，禁止新建第二套 KnowledgeDocument、Chunk、导入、删除、引用或搜索主链。
2. CTX 已冻结 ContextAssembler 与硬预算；EAP 六协议及 Schema 60 已冻结。KIG 只通过稳定只读接口消费，不修改情绪、关系、主动决策、表达、投递与反馈状态机。
3. CDS 负责共享 DecisionRun、CandidateEnvelope、structured output、Shadow/Advisory/Active、模型路由、校验、熔断与通用 rerank 运行时。KIG 只注册查询规划、信息分类、检索重排、支持度与 PWM 的领域协议，不复制通用运行时。
4. LIFE 负责生活事件、日期、日记和 SelfTimeline；KIG 在 LIFE 冻结后建立只读来源适配，不反向写入或重建 LIFE 账本。
5. KIG 独占的新增范围是跨源 `SourceRef` adapter、信息分类提案、派生 Claim/Entity/Relation/WorldEvent、版本与新鲜度、Evidence 支持度和 PWM 投影。既有来源正文仍留在原系统。
6. 新结构切片、索引或检索版本必须旁路构建、对照验收并原子切换；不得就地破坏现有可用索引。
7. KIG.0 必须确认 CDS 与 LIFE 均已集成到 `main`，锁定 LIFE 的不可变最终提交、Schema、协议/adapter 与测试基线；否则只能审计。
8. `web_result` 在 KIG v1 只是 SourceAdapter 兼容位，不实现自动联网搜索、网页抓取或研究 Agent；只有未来 ToolRegistry 与 NetworkPolicy 完成后才可注册真实来源。

顺序门禁：

```text
CDS 冻结并记录最终 Schema
            ↓
LIFE 从下一号施工、总验收并冻结
            ↓
KIG 从 LIFE 最终 Schema + 1 开工
```

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

KIG 不直接向聊天模型拼接知识。它向 ContextAssembler 返回：

```text
KnowledgeRetrievalBundle
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

ContextAssembler 最终决定是否注入、注入多少以及先缩减哪一部分。CTX 的总预算不变量、当前用户输入保护区和输出预留优先于 KIG 的任何排序。现有 CTX 已明确知识、长期记忆、历史原文和摘要各自独立，并由统一 ContextPackage 编排。

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

SQLite 无法用单一外键约束多态来源，因此可追溯性必须由可执行注册表与依赖图保证：

```text
SourceAdapterRegistry
├─ source_type
├─ exists()
├─ current_revision()
├─ current_hash()
├─ privacy_policy()
├─ locator()
└─ deletion_state()

derived_dependencies
├─ derived_type / derived_id
├─ source_ref_id / source_revision
└─ dependency_role
```

后台 sweeper 有界检查来源删除、revision/hash 变化、locator 失效与权限变化，并把派生对象标记为 stale/rebuild；不得让悬空 SourceRef 永久冒充有效来源。

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

### 5.6 PWMEntity 与 PWMEntityAlias

```text
PWMEntity (`pwm_entities`)
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

PWMEntityAlias (`pwm_entity_aliases`)
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

现有 `memory_entities` 继续属于 MEM，`pwm_entities` 只是 KIG 派生世界模型实体。二者通过 `pwm_entity_source_links` 关联：PWM 删除不删除 memory entity，MEM 不依赖 PWM 才能工作；用户确认的 alias 只能经显式 proposal 同步，禁止静默双向覆盖。

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

高风险、多来源回答必须同时执行生成前证据充分性检查和生成后引用校验。生成后将事实性断言切分为：

```text
AnswerClaimSegment
├─ text_span
├─ claim_type
├─ evidence_ids
├─ support_state
└─ citation_required
```

最终 Validator 必须确认 citation key 属于本轮白名单、来源仍有效、引用实际支持对应句子，并阻止把 partial/conflict 改写为确定事实；“同主题”但不支持该断言的引用视为无效。

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

目标：确认真实知识库能力，不把旧设计或未接线骨架当成完成。

- [x] 以现有 Knowledge K0～K9 验收链为起点，审查知识接收、解析、切片、FTS/Dense、Embedding、检索 v2、引用、删除、传输授权、CTX 接线、UI、API、迁移和测试。
- [x] 审查 CTX、Fragment/Episode/Saga、LIFE、Task/ToolRun 和 Lore 的现有接口。
- [x] 建立 `[x]/[~]/[ ]/[→]/[-]` 能力矩阵。
- [x] 记录 20 个单文档、20 个多文档、20 个跨知识/记忆问题基线。
- [x] 记录召回率、引用准确率、延迟、token 和失败模式。
- [x] 新增 ADR：KIG 是治理和投影层，不是大一统正文数据库。
- [x] 新增 ADR：LLM 提议、程序裁决；PWM 不是事实权威。
- [x] 列出权威文档优先级和与 CTX/MEM/EAP/LIFE 的所有权边界。
- [x] 确认 CDS 与 LIFE 已冻结，记录二者最终 Schema 和 adapter 版本；任一未冻结则 KIG.0 只允许审计，不允许迁移施工。
- [x] 填写共享规范中的 ConstructionBaseline，锁定已合并 LIFE 的不可变提交与测试基线。
- [x] 新增 ADR：`memory_entities` 保持 MEM 权威领域实体，`pwm_entities` 为可重建派生实体；定义单向 proposal、删除与依赖边界。
- [x] 确认 `web_result` 仅为兼容位，KIG v1 不注册真实联网搜索、抓取或研究执行器。

完成门：

- [x] 后端、前端和 Electron 当前基线通过。
- [x] 0 个未解决的职责冲突。
- [x] 现有完整能力直接勾选，不重写。

KIG.0 施工记录（2026-07-27）：ConstructionBaseline 锁定 LIFE PR #3 merge `main@f16d80ab0d2457065dc65d7d284d3cbf3584f5ee`、Schema 71、CDS/CTX/EAP/LIFE/Knowledge 冻结协议与测试基线。新增 60 条纯合成固定集：20 个单文档、20 个多文档、20 个 Knowledge+Memory 问题；隔离临时库实跑的 Knowledge 召回率、Knowledge+Memory 各源召回率和现有 citation allowlist 准确率均为 100%，但跨源统一 Evidence 支持率诚实记录为 0%。基线后端 `2428 passed, 1 warning`、前端 `50 passed`、Vite 190 modules、Electron 语法与 lifecycle contract 3 项通过。能力矩阵确认 Knowledge 主链完整复用，CTX/MEM/EAP/LIFE/Lore 所有权不转移；Task/tool_logs 仅部分具备来源条件，正式 ToolRegistry 仍属未来专项。ADR-0062～0064 分别冻结治理投影层、模型提议/PWM 非权威和 MEM/PWM 实体单向边界。KIG.0 未新增迁移或生产写路径，KIG.1 可在存在真实字段缺口时使用 Schema 72。证据见 `docs/reports/kig-0-construction-baseline.md` 与 `kig-0-baseline.json`。

建议 PR：`docs(kig): audit and freeze knowledge governance boundaries`

### KIG.1：统一 SourceRef 与来源状态

目标：所有知识和派生对象可回到真实来源。

- [x] 优先建立轻量 typed `SourceRef` 信封和各系统 adapter，复用已有 ID、locator、revision/hash 与 status；只有 KIG 派生对象确需查询时才持久化最小引用。
- [x] 为文档、Chunk、消息、记忆、LifeEvent、ToolRun、Lore 建立适配器。
- [x] 来源变化触发派生对象 stale。
- [x] 删除和不可访问状态可传播。
- [x] 不复制不必要正文。
- [x] 不为每条既有来源强制建立平行“通用来源行”，不迁移或复制原系统正文和生命周期。
- [x] 建立来源定位 API 和测试。
- [x] 建立 `SourceAdapterRegistry` 和 `derived_dependencies`，以可执行 exists/revision/hash/privacy/locator/deletion 校验弥补多态外键缺失。
- [x] 增加有界 sweeper，传播 missing/stale/revoked/inaccessible；来源变化检查失败时保守降级，不自动删除权威来源。

验收：任一引用、Claim、关系和事件都能回到原来源；伪造 locator 通过率为 0。

KIG.1 施工记录（2026-07-27）：Schema 72 仅新增无正文 `derived_dependencies`，没有建立每来源一行的平行 `source_refs` 表。`SourceAdapterRegistry` 从 KnowledgeDocument/Chunk、Message、MemoryFragment、LifeEvent、ToolRun 和 Lore 原权威存储实时解析 typed `SourceRef`，统一提供 revision、SHA-256、status、privacy scope 与 owner locator；绑定前必须逐字段回查权威元数据。来源 revision/hash/privacy/locator 改变传播为 stale，删除传播 missing，撤销传播 revoked，关闭传播 inaccessible，适配器故障保守标记 unverified；有界 sweeper 单批最多 500 条，任何失败均不删除权威来源。新增只读定位与严格校验 API，测试对 7 类来源逐一伪造 locator，0 个通过。KIG.1 专项 5 项、KIG/CDS 相关回归 367 项、后端全量 `2434 passed, 1 warning`；详细证据见 `docs/reports/kig-1-source-governance.md`。

建议 PR：`feat(kig): add unified provenance and source references`

### KIG.2：现有 KnowledgeDocument 与索引版本补差

目标：统一文档、解析器、Chunk 和索引状态。

- [x] 审查并兼容现有知识表，不无条件迁移。
- [x] 先盘点现有解析器、Chunk、Embedding、search contract 与索引状态字段；仅对有验收缺口的最小字段新增迁移。
- [x] 建立原子索引切换和失败回退。
- [x] 支持文档重建、归档、删除和影响预览。
- [x] FTS 失败和 Dense 失败可独立降级。

验收：旧索引在重建完成前可用；失败不导致文档不可查询。

KIG.2 施工记录（2026-07-27）：审查确认既有 reindex 会在开工时立即清除 Chunk/FTS/Dense 并将文档退出 indexed，违反旧索引持续可用门槛。Schema 73 在原表旁新增按 run 隔离的 `knowledge_rebuild_chunks` 与最小 staged metadata；重建期间原 `knowledge_documents/knowledge_chunks/FTS/embedding` 保持 active，解析与切片只写 staging，最终在一个 SQLite 事务内校验候选、替换 Chunk/FTS、递增 `active_index_revision` 并切回 idle。任何解析、切片、索引、取消或陈旧恢复失败均只清理 staging/标记 rebuild failed，旧索引和文档仍为 indexed。新增 governance archive/restore 与删除/重建/归档影响预览；归档只退出检索，不删原文。Dense 在切换后可独立重建，失败继续走 FTS；FTS 无词时既有 vector fallback 保留。Knowledge/KIG 专项回归 `193 passed, 1 warning`，详细证据见 `docs/reports/kig-2-atomic-index-governance.md`。

建议 PR：`feat(knowledge): version documents chunks and indexes`

### KIG.3：信息分类与目标路由

目标：区分 Knowledge、Memory、Conversation、Life、Lore 和 Task Result。

- [x] 定义 information-classifier-v1 Schema。
- [x] 高精度命令和来源类型先由程序判断。
- [x] LLM 只处理模糊场景。
- [x] 输出 destination proposal，不直接写目标库。
- [x] 目标系统重新验证。
- [x] 建立临时状态、长期偏好、观点和计划的误判集。

验收：普通临时要求不会变成永久偏好；外部事实不会污染用户记忆。

KIG.3 施工记录（2026-07-27）：新增 `information-classifier-v1` typed input/result 与 CDS `information_classifier` Shadow DecisionKind，不新增运行账本或迁移。程序优先识别临时指令、显式记忆偏好、计划、观点、Lore、外部 Knowledge 来源和 ToolRun；只有无高精度命中的模糊文本才允许进入模型。模型调用要求来源 revision/hash 未变、远程调用显式授权、固定 destination candidates 与严格标量 JSON；模型内容始终作为 untrusted data，任何错误走 `unknown/none` fallback。输出固定 `proposal_only=true`，目标域必须重新校验来源与开关，分类器没有写入权。误判/注入集验证临时请求持久化率 0、外部事实写 Memory 率 0、伪造 destination 通过率 0。专项/CDS 回归 `37 passed, 1 warning`。实配 DeepSeek 8 条纯合成模糊/注入 Shadow 首轮 6 条结构与安全验证通过、2 条调用错误安全回退；有效响应安全率 100%，整体含 fallback 安全收口率 100%，模型一次可用率诚实记录为 75%。详见 `docs/reports/kig-3-information-classifier.md`。

建议 PR：`feat(kig): add validated information classification routing`

### KIG.4：文档语义增强与结构化切片

目标：改善章节、表格、代码和语义边界，同时保留原文真实性。

- [x] 以新 chunk/index 版本旁路建立结构优先切片器，不覆盖现有 raw_text 和当前可用 Chunk。
- [x] 保存 heading path、页码、邻居和 chunk kind。
- [x] 增加可选 LLM 边界建议。
- [x] 模型不得重写 raw_text。
- [x] 建立不同文档类型的切片质量集。
- [x] 模型失败回退确定性切片。
- [x] 新旧版本完成固定集对照和引用定位验证后原子切换，失败时继续服务旧索引。

验收：定义、步骤、警告、表格和代码上下文不被明显错误切断；原文 hash 不变。

KIG.4 施工记录（2026-07-27）：Schema 74 为原 `knowledge_chunks` 与 KIG.2 staging 增加 `chunk_kind`（heading/prose/list/table/code）及 previous/next ordinal；切片器升级 `knowledge-structure-chunker-v2`，先识别 fenced code、Markdown/制表表格、列表和 heading/prose 结构，再在必要时按行/句有界切分。正常尺寸代码块和表格保持单块，所有 content 必须等于 raw text 的精确 char slice，hash 逐块重算；原文件与 document content hash 永不改写。FTS 升级 v2 并兼容读取 v1，旧文档无需迁移；重建继续走 KIG.2 staging/单事务切换。新增 `knowledge_boundary_proposal` CDS Shadow，只能选择程序提供的安全 offset 子集，`rewrites_raw_text=false`，无效或模型失败回退确定性 offsets。Markdown/TXT/PDF/DOCX 既有格式集与新增标题、定义、步骤、警告、列表、表格、代码质量集回归 `198 passed, 1 warning`。详见 `docs/reports/kig-4-semantic-chunking.md`。

建议 PR：`feat(knowledge): add provenance-safe semantic chunking`

### KIG.5：Query Planner 与多库路由

目标：在检索前决定问题类型、来源和子查询。

- [x] 在 CDS 冻结的 DecisionRun/CandidateEnvelope 上注册 `query-plan-v1`，不自建通用模型运行、模式或审计框架。
- [x] 明确单文档和显式来源问题跳过 Planner。
- [x] 支持 Knowledge/Memory/History/Life/Task/Lore 源选择。
- [x] 支持时间、版本、实体、原话和冲突需求。
- [x] 用户关闭某源后 Planner 建议也不得放行。
- [x] 建立提示注入和模糊指代测试。

验收：跨库问题能选择正确来源；普通明确查询不增加无意义模型调用。

KIG.5 施工记录（2026-07-27）：新增 `query-plan-v1` typed input/result，并在既有 CDS DecisionRun/CandidateEnvelope 注册 `kig_query_planner` Shadow DecisionKind；未新增迁移，Schema 保持 74。显式来源、单文档、普通明确查询与时间/版本/实体/原话/冲突/跨库请求由确定性规则直接规划并记录 `bypassed_model=true`，只有模糊指代进入授权模型路径。所有输出限于 Knowledge/Memory/History/Life/Task/Lore 六个候选、最多 4 个各 160 字符子查询，关闭来源在程序与 validator 两层硬拒绝；提示注入作为不可信数据处理。模型输出仅是 `proposal_only` Shadow 建议，重复 DecisionRun 不重复调用，失败或未授权退回 Knowledge（可用时）或空计划，不执行检索和写入。阶段及关联回归 `881 passed, 1 warning`。最终代码实配 `deepseek-v4-flash` 两轮共 12 条纯合成模糊/注入样例：2 条明显注入由程序旁路拒绝，10 条进入模型，其中 3 条严格结果通过、7 条安全回退；来源越权 0、`application_allowed` 0、整体安全收口率 100%。模型调用一次成功率 30%，仅作为 Shadow 观测，不作为晋级证据。详见 `docs/reports/kig-5-query-planner.md`。

建议 PR：`feat(retrieval): add bounded query planning and source routing`

### KIG.6：混合召回与候选统一

目标：统一 FTS、Dense、Metadata 和图投影候选。

- [x] 定义 RetrievalCandidate。
- [x] 接入现有 FTS 和向量实现，已有能力直接复用。
- [x] 增加 metadata filter、日期、版本和状态过滤。
- [x] 建立各源独立候选上限。
- [x] 去重、邻居扩展和多样性选择。
- [x] Dense 不可用时使用 Lexical 回退。

验收：单一源故障不阻塞查询；候选均带来源、状态和 locator。

KIG.6 施工记录（2026-07-27）：新增无持久化的 typed `RetrievalRequest`、`RetrievalFilters`、`RetrievalCandidate` 与 `RetrievalBatch`，统一承载 source/revision/hash/status/privacy/locator、独立 lexical/vector/metadata/recency 信号、freshness、authority、role 和短 excerpt；不把不同含义压成一个总分。六个只读 adapter 分别复用 Knowledge `hybrid_search`（FTS+Dense+RRF+邻居）、Memory FTS/LIKE、CTX History FTS、LIFE SelfTimeline 中的 LifeEvent 投影、Task/ToolRun 和 Lore 现有检索。每源默认 6、最高 20，总候选最高 60；来源分别执行、分别记录 body-free diagnostics，任一来源异常只清空该源。metadata hard filter 支持 source ID、document、tag、revision、status 与时间范围；候选进入批次前重新解析 KIG.1 SourceRef，跨源适配冒充被拒绝。源内 exact-normalized 去重后按来源轮询选择，保留跨源相同证据；Knowledge Dense 不可用时显式记录 lexical fallback，FTS 热路径继续工作。无迁移，Schema 保持 74。专项及核心 KIG/CTX/Knowledge 回归 `893 passed, 1 warning`；详见 `docs/reports/kig-6-unified-retrieval.md`。

建议 PR：`feat(retrieval): unify hybrid multi-source candidates`

### KIG.7：LLM 语义重排

目标：让模型在有限候选中判断真正相关性，不直接执行检索或写状态。

- [x] 在 CDS 通用 rerank 运行时上注册 KIG `retrieval-rerank-v1` 领域 Schema、用途枚举和质量门。
- [x] 只允许返回输入候选 ID。
- [x] 区分 direct、partial、background、conflict、outdated、duplicate、irrelevant。
- [x] 来源变化后拒绝旧重排结果。
- [x] 模型失败使用确定性融合。
- [x] Shadow 模式对比旧排序。
- [~] 晋级、盲评、模型认证和回滚遵守共享 Decision Promotion Policy；未通过对应 decision_kind 认证的模型不得 Active。（Shadow/Active 硬门已完成；实配模型严格结果覆盖率不足，质量盲评未通过，禁止晋级。）

验收：人工相关性显著高于旧排序；引用不存在率为 0。

KIG.7 施工记录（2026-07-27）：在 CDS 共享 DecisionRun/CandidateEnvelope/structured-output/fallback 审计运行时注册 `retrieval-rerank-v1`，最大 30 个输入候选、最多选择 12 个；模型必须对输入 ID 做完整排列并逐一给出七类 relevance role、rank bucket 和 confidence，selected 只能按排序引用未排除的输入 ID。KIG.6 候选适配只携带短 excerpt、privacy scope 与 SourceRef 快照；运行前和输出验收时复核 revision/hash/status/privacy/locator，来源撤销或变化时确定性 fallback 也实时查源并丢弃旧候选，Knowledge `local_only/ask_each_time` 未获许可时整批禁止远传。失败回退保持 lexical/vector/metadata/recency 分离的确定性融合；Shadow comparison 只记录 Jaccard、位置变化和计数，不记录 query/excerpt。共享 `llm.complete_json` 新增默认关闭的 JSON Object 模式，KIG.7 显式启用，其他调用行为不变。Schema 保持 74，核心回归 `924 passed, 1 warning`，非候选选择通过率 0、Shadow `application_allowed` 0。实配模型在启用 JSON Object 前共 18 次合成调用仅 1 次严格结果、17 次安全回退，0 越界；带人工相关标签的 6 例盲评无严格有效结果，不能证明相关性提升。JSON Object 模式的远端复测因当前 Codex 外部用量额度被拒绝，故本阶段维持 Shadow，质量门 `[~]`，KIG-R 冻结前必须补测。详见 `docs/reports/kig-7-retrieval-reranker.md`。

建议 PR：`feat(retrieval): add validated LLM semantic reranking`

### KIG.8：证据、引用与支持度

目标：回答可以被来源证明，资料不足时不编造。

- [x] 审计并复用现有知识 citation/source API、locator 验证和原文打开入口，只把已证实的跨源缺口抽象为 EvidenceLink。
- [x] 定义 claim-support-v1。
- [x] 复杂问题执行支持度检查。
- [x] 对高风险和多来源回答建立 `AnswerClaimSegment`，执行生成后 citation 白名单、来源有效性、句子级支持度和不确定性一致性校验。
- [x] 冲突和不足进入 ContextBundle。
- [x] UI 展示轻量来源条。

验收：引用 100% 可打开或明确标记来源不可访问；资料不足时不生成伪引用。

KIG.8 施工记录（2026-07-28）：Schema 75 新增 body-free `kig_retrieval_bundles`、`kig_answer_claim_segments` 与跨源 `kig_evidence_links`；现有 Knowledge `knowledge_message_citations`、K1 白名单及原文 API 保持唯一，不为知识 Chunk 复制 EvidenceLink。`knowledge-retrieval-bundle-v1` 以结构化对象进入 ContextAssembler，由 CTX 复核字段、限制 12 条并在既有知识预算内最终裁剪；`claim-support-v1` 对复杂/高风险回答执行逐句 citation 白名单、SourceRef revision/hash/status/privacy/locator 实时复核、关键产品/版本锚点和词项支持度、不确定性一致性校验。伪造、失效和同主题但不支持的引用分别明确标记，unsupported 链只保留审计且不进入 UI；跨源原文由 owner store 实时打开，变化/删除后不回放快照正文。聊天 UI 复用既有资料条样式显示轻量来源条。KIG/Knowledge/CTX/API 核心回归 `311 passed, 1 warning`，前端 `51 passed`、TypeScript/Vite 190 modules 通过。详见 `docs/reports/kig-8-grounded-evidence.md`。

建议 PR：`feat(knowledge): add grounded evidence and citation support`

### KIG.9：冲突、版本与新鲜度

目标：处理新旧设计、软件版本、条件差异和用户纠正。

- [x] 建立 VersionRelation 和 FreshnessState。
- [x] 确定性 hash/date/version 规则先行。
- [x] LLM 只提出语义 relation。
- [x] 用户最新纠正和 authoritative 标记优先。
- [x] 高影响冲突请求确认。
- [x] 建立版本分支、部分替代和条件兼容测试。

验收：新旧文档不会无提示混合；时间条件不同不误判为冲突。

KIG.9 施工记录（2026-07-28）：Schema 76 新增 body-free `kig_source_governance` 与 `kig_version_relations`，并为 VersionRelation 绑定两端 `derived_dependencies`。`freshness-state-v1` 仅在同一对象/范围已由 source id、用户 scope 或高重合主题证明后应用 hash、owner revision、semver、有效期与 authority/date 规则；版本号或时间较新本身不证明正确。不同时间/条件先判 `compatible_with_conditions`，不会误标冲突；exact duplicate/supersedes/partial/expired 分别进入可审计新鲜度。`version-relation-v1` 语义判断注册于 CDS Shadow，模型输出始终 proposal-only；用户最新纠正、用户确认 authoritative、ToolRun、官方源、导入资料、模型提案按固定优先级治理。高影响 contradict/divergent 必须 `requires_confirmation`，revision-matched API 接受/拒绝后才可进入 confirmed。KIG-R chat pipeline 仅应用确定性/已确认关系和确定性融合，未决冲突进入 RetrievalBundle 并由生成后 Validator 强制保留冲突措辞；Knowledge K1 同时补上实时来源与句子支持度检查。扩大到 KIG/Knowledge/CTX/API/CDS.9/CDS.10 的回归 `687 passed, 1 warning`。详见 `docs/reports/kig-9-conflict-version-freshness.md`。

建议 PR：`feat(kig): add conflict version and freshness governance`

### KIG-R 冻结门：Retrieval & Governance

KIG.0～KIG.9 完成后先冻结和发布 KIG-R，不等待 PWM：

- [ ] Source adapters、分类、Query Planner、混合候选、LLM rerank、EvidenceLink、生成后 Citation Validator、冲突/版本/新鲜度与 CTX RetrievalBundle 全部通过验收。
- [ ] 独立 Review 为 0 个未解决 P0/P1，零容忍来源/引用/授权指标均为 0。
- [ ] 冻结 `kig-retrieval-governance-v1`、记录 Schema 和回滚点；KIG-P 从下一迁移号继续。
- [ ] KIG-R 关闭后即能独立改善聊天检索；PWM 延期或关闭不得破坏 KIG-R。

KIG-R 冻结审计记录（2026-07-28）：已建立 10 组纯合成、11 项非零分母零容忍验收，当前违规数均为 0；最终后端全量 `2531 passed, 1 warning`，前端 `51 passed`、TypeScript/Vite 190 modules 与桌面 JavaScript 语法检查通过。审计修复了四项冻结前问题：确认关系不再受全库最近 200 条窗口遮蔽、KIG 在排序前受本轮 Knowledge 授权 chunk 白名单约束、验收脚本导入不再污染环境、KIG.7 质量评测改为 100% 严格覆盖且同样本配对增益至少 15%。当前唯一技术阻断仍是 KIG.7 JSON Object 模式实配模型质量证据；发布门保持 `pending_model_quality`，四项冻结条件不得勾选，KIG.10 不得开工。详见 `docs/reports/kig-r-acceptance.md` 与 `docs/reports/kig-r-freeze-readiness.md`。

### KIG.10：Claim、Entity、Relation 与 WorldEvent

目标：建立个人世界模型的来源化数据底座。

- [ ] 所有新表使用 `pwm_` 前缀：`pwm_claims/pwm_entities/pwm_entity_aliases/pwm_relations/pwm_world_events/pwm_state_assertions/pwm_entity_source_links`；只保存派生投影，不复制权威事实行。
- [ ] 使用白名单实体类型和 Predicate。
- [ ] 先在 shadow 模式抽取。
- [ ] 所有对象必须有 SourceRef。
- [ ] 模型推断默认不可独立支持事实回答。
- [ ] 敏感属性自动抽取禁用。
- [ ] 设置硬预算：每来源最大派生 Claim、每日最大新实体、低置信候选 TTL、单实体最大 alias、单次消歧候选和维护批次上限、孤立节点归档规则。

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
- [ ] `memory_entities` 与 `pwm_entities` 不自动合并或双向覆盖；alias 同步只生成可审计 proposal，由目标所有者确认应用。

验收：错误自动合并率达到严格门槛；所有合并可回滚。

建议 PR：`feat(pwm): add reversible entity resolution`

### KIG.12：与 Fragment/Episode/Saga 和 LIFE 的治理接线

目标：复用现有系统，不重写其内部状态机。

- [ ] MemoryClassificationProposal 接口。
- [ ] MemoryConflictProposal 接口。
- [ ] EpisodeBoundaryProposal 和 SagaTransitionProposal 接口。
- [ ] 仅在 LIFE v1 冻结后接入 SelfTimeline 只读召回 adapter；KIG 不写 LifeEvent、日记、日期或生活状态。
- [ ] ToolRun 权威来源适配。
- [ ] ContextAssembler 接收统一 RetrievalBundle。
- [ ] 各系统的关闭、临时聊天和隐私设置生效。
- [ ] EAP 只读来源适配不得改变冻结的候选、关系、表达、投递与反馈协议。

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

- [ ] 扩展现有知识库主页、集合、导入、搜索和详情体验；不得并行重建第二套知识 UI。
- [ ] 文档详情、索引状态和版本关系。
- [ ] 来源展开和原文入口。
- [ ] 项目/实体页和事件时间线。
- [ ] 删除影响预览。
- [ ] 数据传输与模型设置。
- [ ] 开发者检索诊断。

验收：普通用户不需要理解 BM25、向量和图谱即可完成导入、问答、纠正、归档和删除。

建议 PR：`feat(ui): add knowledge governance and world model views`

### KIG.15：长期模拟、校准与总验收

目标：完成 KIG-P（Personal World Model）并在已冻结 KIG-R 之上冻结 KIG v1。

- [ ] 后端全量测试通过。
- [ ] 前端测试、TypeScript、Vite 和 Electron 检查通过。
- [ ] 1 万、10 万和目标规模 Chunk 压力测试。
- [ ] 100 个单文档、100 个多文档、100 个跨库问题评测。
- [ ] 100 个版本冲突与用户纠正场景。
- [ ] 100 个实体消歧和合并回滚场景。
- [ ] Provider 切换、离线、远程受限和预算不足测试。
- [ ] 引用准确率、召回率、重排增益和延迟报告。
- [ ] 更新所有权威文档和迁移说明。
- [ ] 记录 KIG 最终 Schema、协议与 adapter 兼容矩阵，独立 Review 确认 0 个未解决 P0/P1 后冻结 KIG v1。
- [ ] 0 个未解决 P0/P1。
- [ ] 压力测试验证每来源/每日/alias/消歧/维护批次硬预算，单本大型手册不得无界生成数万 PWM 节点。

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
4. docs/LLM_COGNITIVE_DECISION_REFACTOR_PLAN.md
5. docs/LLM_DECISION_AND_LIFE_CONTINUITY_PLAN.md
6. docs/XIADIE_KNOWLEDGE_INTELLIGENCE_GOVERNANCE_AND_WORLD_MODEL_PLAN.md
7. docs/SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md
8. docs/PR_CHECKLIST.md

本轮只执行指定的 KIG 子阶段，不提前实现后续阶段。

开始前必须：
- 核对当前代码、schema、测试数、默认分支和最新提交。
- 确认 CDS 与 LIFE 已依次冻结，读取 LIFE 最终 Schema；KIG 从下一号迁移开始，前置专项未冻结则只允许审计。
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

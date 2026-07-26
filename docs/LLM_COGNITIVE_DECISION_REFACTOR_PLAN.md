# 遐蝶 LLM 认知决策改造专项施工计划

* **版本：** v0.3（施工基线、晋级、预算与数据治理补强）
* **日期：** 2026-07-22
* **状态：** 计划优化完成；等待 EAP PR #1 合并并锁定 `main` 合并提交后施工
* **专项代号：** `CDS`（Cognitive Decision Service）
* **适用范围：** 将当前依赖正则、固定权重、固定阈值和固定优先级的语义判断，逐步升级为“本地候选 + LLM 结构化判断 + 程序验证与执行”的统一认知决策体系
* **关联专项：**

  * `CTX`：对话上下文、会话摘要与跨会话回忆
  * `EAP`：完整情感、关系积温与主动陪伴
  * `LIFE`：生活连续性、离线世界、日程、重要日期和日记
* **不包含：** ToolRegistry 正式执行、MCP、多 Agent、QQ/微信正式投递、任意桌面自动化、高风险权限放宽
* **上线顺序：** 所有决策器必须经过 `Shadow → Advisory → Active`
* **执行规则：** 每阶段完成代码、测试、文档、Review 和独立提交后，才能进入下一阶段
* **专项顺序：** `CDS → LIFE → KIG`；CDS 是后三个专项中的第一项
* **迁移规则：** 当前冻结基线为 Schema 60；只有出现不可由现有表表达且有证据的字段时才新增迁移，首个可用版本为 61
* **共享规范：** `docs/SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md` 是所有权、Adapter、晋级、模型认证、预算与数据生命周期的规范事实源

---

## 0. 当前仓库基线与强制施工边界

以下事实优先于本计划早期设计描述：

1. Schema 56 已存在共享 `decision_runs`、repository、状态与事件审计；CDS 必须复用和补强，不得新建第二套通用 DecisionRun 或平行 run/event 账本。
2. CTX 已冻结硬预算、ContextAssembler 与上下文 v1；CDS 可以提供规划建议和 shadow 对照，但不得绕过当前消息、最近轮次、输出预算与来源预算硬门。
3. EAP 的 `conversation-presence-v2`、`user-affect-observation-v1`、`relationship-meaning-v1`、`proactive-decision-v2`、`expression-plan-v1`、`proactive-feedback-v1` 及 Schema 60 已冻结。CDS 只消费其稳定接口或做旁路评测；不兼容改动必须新协议版本和 ADR。
4. 现有 Knowledge 已具备文档、切片、FTS/Dense、引用、删除生命周期、传输策略、搜索与 CTX 接线。CDS 只拥有共享决策运行时、有限候选协议、校验与模式门禁；跨源治理、版本/新鲜度、证据支持度和 PWM 归 KIG。
5. LIFE 尚未施工。CDS 只冻结供 LIFE/KIG 使用的 adapter 契约，不创建 LifeClock、LifeEvent、日记、日期或 PWM 表。
6. 任一决策器必须先有固定评测集和真实 Shadow 证据，再进入 Advisory；没有独立 Review 与 0 个未解决 P0/P1，不得 Active 或冻结。
7. EAP 当前是协议技术冻结，但 GitHub PR #1 仍为 open/draft。CDS.0 默认必须等待该 PR 合并，并把 `main` 合并提交写入 ConstructionBaseline；只有用户明确批准固定 SHA 的例外路径才能提前开工。
8. 正式施工前必须复制共享规范中的 ConstructionBaseline，记录 repository、predecessor PR、base commit SHA、Schema、冻结协议、测试基线、计划版本和时间。字段不完整时只能审计。

顺序门禁：

```text
EAP / CTX 已冻结（Schema 60）
            ↓
CDS 审计、补强共享决策底座并冻结
            ↓
LIFE 从 CDS 最终 Schema + 1 开始施工
            ↓
KIG 从 LIFE 最终 Schema + 1 开始施工
```

---

## 1. 专项目标

当前遐蝶已经具有：

* 长期记忆 Fragment、Episode、Saga；
* 当前会话滚动摘要；
* 跨会话历史回忆；
* 本地知识库；
* Affect Observer；
* 关系积温和连续心境；
* ContextAssembler；
* 主动陪伴与生活连续性设计。

但很多重要选择仍主要依赖：

```text
关键词
正则
固定线性权重
固定阈值
固定候选数量
固定 token 比例
固定时间规则
```

这些算法适合第一版的确定性和安全性，却不擅长回答：

* 用户说的“之前那个方案”究竟指哪次决定？
* 当前问题最需要记忆、知识库、旧聊天还是最近原文？
* 一条记忆应该作为事实回答，还是只用于情感连续性？
* 一次互动只是礼貌交流，还是关系中的重要确认？
* 几条 Fragment 是同一段经历，还是仅仅关键词相似？
* 当前主动靠近是自然关心，还是会形成打扰？
* 哪段生活经历值得写进日记、形成 Episode 或告诉用户？

本专项目标是建立统一认知决策闭环：

```text
用户消息、当前状态和可靠来源
                ↓
本地程序生成有限候选
                ↓
LLM 判断意义、相关性、用途和自然程度
                ↓
程序验证 Schema、候选 ID、来源、边界和状态版本
                ↓
程序映射为有限动作
                ↓
上下文装配、状态更新或候选创建
                ↓
反馈、评测与策略校准
```

### 完成后的产品表现

1. 用户不需要使用固定命令，遐蝶就能判断是否应该回忆旧聊天或翻阅资料。
2. 不同问题获得不同的上下文组合，不再由一套固定比例处理所有场景。
3. 她能区分当前有效记忆、旧计划、被替代事实和情绪背景。
4. 普通问答和礼貌交流不会机械增加长期关系。
5. Episode 和 Saga 更接近真实经历与长期故事，而非文本聚类。
6. 主动消息和生活分享由语义意义驱动，但边界和投递仍由程序控制。
7. 模型不可用、超时或输出错误时，聊天和核心系统仍可安全运行。
8. 普通用户感受到的是“她理解得更自然”，而不是多出大量算法设置。

一句话定义：

> LLM 负责判断“这意味着什么、什么最相关、怎样更自然”；程序负责判断“能不能做、证据是否成立、最多能做多少、如何安全落地”。

---

## 2. 当前代码审计与改造范围

### 2.1 已有、直接复用的 LLM 基础

| 能力               | 当前形态                            | 本专项处理       |
| ---------------- | ------------------------------- | ----------- |
| 会话摘要             | 后台 Worker、结构化 JSON、一次修复、失败不阻塞聊天 | 作为通用决策执行器参考 |
| Affect Observer  | LLM 提议，程序验证证据和限幅                | 保留并接入统一治理   |
| Memory Observer  | LLM 提取候选，程序验证来源和敏感内容            | 保留现有业务协议    |
| Episode 摘要       | LLM 为候选生成摘要                     | 扩展为叙事判断     |
| ContextAssembler | 统一硬预算和 Prompt 装配                | 继续拥有最终装配权   |
| SQLite Worker/审计 | 已有任务、重试、恢复和事件模式                 | 复用治理结构      |

会话摘要服务已经采用异步处理、结构化验证、失败修复和安全降级，是本专项最值得复用的工程模式。

### 2.2 仍以固定算法为主的区域

#### 跨会话回忆

当前依赖：

* 显式回忆正则；
* 标题、摘要、消息、轮次的固定权重；
* 固定注入阈值；
* 固定候选数量。

可能漏掉：

> “还是按照当时说好的做吧。”

也可能因关键词相似召回错误会话。

#### 长期记忆召回

当前以 FTS/LIKE 和固定排序选择最多 12 条、总计约 2400 字符。

缺少：

* 当前有效性判断；
* 记忆用途判断；
* 新旧计划替代；
* 事实与情绪背景区分；
* 重复记忆语义合并。

#### 知识库召回

当前存在：

* `off / explicit / smart` 三种模式；
* 显式模式最多 6 条、1200 tokens；
* 自然模式最多 4 条、700 tokens；
* 规则判断寒暄、情绪支持、简单任务和知识意图。

缺少：

* 对复杂文档任务的动态预算；
* 多查询规划；
* 完整证据窗口；
* 证据支持、冲突和不足判断；
* 更自然的产品入口。

#### ContextAssembler

当前可选上下文使用固定份额：

```text
滚动摘要       28%
跨会话历史     22%
长期记忆       20%
知识库         18%
Lore           12%
```

不同任务仍使用同一优先顺序。

#### Lore

当前主要依靠预定义关键词和标题命中，最多返回 3 个小节、3600 字符。

#### 记忆生命周期

当前 Archivist 依靠固定线性保留分数，以及 14 天、30 天和固定阈值进行降温、冻结与恢复。

#### Episode / Saga

当前后台先由固定算法形成候选，LLM 主要补充摘要，而不是最终判断事件边界和因果链。

### 2.3 必须保持确定性的部分

以下内容不得交给 LLM 最终裁决：

```text
用户明确关闭、暂停、拒绝和删除
API Key、密码、验证码过滤
本地或云端传输授权
Token 硬预算
文件哈希、来源和引用合法性
数据库事务和幂等
工具权限、确认和急停
消息真正发送
计划、模拟和真实执行的来源区分
时间是否真实经过
候选和来源是否仍然有效
```

---

## 3. 核心产品原则

### 3.1 LLM 定性，程序定量

不允许模型自由决定：

```json
{
  "bond_delta": 0.038,
  "importance": 0.92,
  "send_probability": 0.83
}
```

模型应输出：

```json
{
  "relationship_effect": "small_positive",
  "memory_usage": "emotional_continuity",
  "approach_strength": "light",
  "retention_class": "long_term",
  "confidence_band": "high"
}
```

程序再根据策略版本映射为有限数值和动作。

### 3.2 LLM 只能从候选中选择

* 不允许模型自由浏览数据库。
* 不允许模型生成新的数据库 ID。
* 返回 ID 必须属于本轮候选集合。
* 来源变化后，旧决策立即失效。
* 没有足够证据时只能选择 `skip`、`uncertain` 或 `ask`。

### 3.3 关系不能覆盖边界

高关系和好心情可以：

* 改变表达语气；
* 延长话题连续性；
* 允许更自然地提起共同经历；
* 让轻微埋怨、催促或担心更符合当前关系。

不能：

* 绕过用户拒绝；
* 绕过远传授权；
* 消除未回复带来的打扰负担；
* 绕过工具权限；
* 把推测写成事实。

### 3.4 所有决策器必须可降级

LLM 失败时：

```text
不阻塞聊天
不修改长期关系
不写虚构记忆
不发送主动消息
不破坏已有派生数据
使用旧算法或安全默认值
```

---

## 4. 目标架构

```text
                     Chat / Background Trigger
                                │
                                ▼
                      Local Hard Gate Layer
          边界、权限、隐私、来源、时间、预算和状态预检
                                │
                                ▼
                   Cognitive Decision Orchestrator
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   Candidate Builder     Decision Model Router    Decision Ledger
   本地有限候选           快速/推理/创作模型        版本与审计
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
                    Structured LLM Proposal
                                │
                                ▼
                   Deterministic Validator
       Schema、候选 ID、证据、来源 revision、边界、限幅
                                │
                                ▼
                     Policy Mapper / Reducer
           语义等级映射、预算分配、状态原子应用或候选创建
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
 ContextAssembler         State / Memory          Candidate Ledger
 最终硬预算装配           有限状态变化             主动/生活/叙事候选
                                │
                                ▼
                    Feedback and Evaluation
```

---

## 5. 决策器分类

### 5.1 Fast Companion Observer

每轮聊天后异步执行，负责：

* Presence；
* 是否预计回来；
* 对话是否结束；
* 开放话题；
* 用户最后去做什么；
* 当前回复需求；
* 本轮关系意义；
* 可选记忆观察种子。

可以一次模型调用返回多个子对象，但每个子对象独立验证、独立应用。

### 5.2 Recall Planner

回复前判断：

* 是否需要长期记忆；
* 是否需要跨会话历史；
* 是否需要知识库；
* 是否需要 Lore；
* 是否需要 Episode/Saga；
* 每类来源应检索什么。

它只决定“找什么”，不直接提供事实。

### 5.3 Candidate Reranker

对程序预选的候选进行语义重排：

* Fragment；
* 历史完整轮次；
* 知识证据窗口；
* Lore 小节；
* Episode/Saga 候选；
* 主动候选。

### 5.4 Background Narrative Planner

低频后台负责：

* Episode 事件边界；
* Saga 阶段和分支；
* 记忆冲突与替代；
* 记忆保留类别；
* 日程；
* 离线续演；
* 日记；
* 重要日期表达。

---

## 6. 统一决策协议

### 6.1 DecisionRequest

统一协议采用 `CommonDecisionHeader + DecisionKind 专属输入 Schema + DecisionKind 专属结果 Schema`，不得演化成包含自由 `context/candidates/effects` 的万能 JSON。每种任务必须注册到共享规范定义的 `DecisionKindRegistry`。

```json
{
  "protocol_version": "cognitive-decision-v1",
  "decision_type": "memory_rerank",
  "policy_version": "memory-rerank-v1",
  "request_id": "program-generated",
  "source_snapshot": [
    {
      "kind": "message",
      "id": "source-id",
      "revision": 1,
      "content_hash": "..."
    }
  ],
  "snapshot_hash": "aggregate-hash",
  "context": {
    "user_query": "...",
    "task_type_hint": "unknown",
    "state_summary": {}
  },
  "candidates": [
    {
      "id": "candidate-id",
      "source_type": "memory_fragment",
      "content": "...",
      "metadata": {}
    }
  ],
  "allowed_actions": ["select", "skip", "ask"],
  "constraints": {
    "max_selected": 5,
    "forbid_new_ids": true
  }
}
```

### 6.2 DecisionResult

```json
{
  "protocol_version": "cognitive-decision-v1",
  "decision_type": "memory_rerank",
  "action": "select",
  "selected": [
    {
      "id": "candidate-id",
      "usage": "answer_fact",
      "priority": "high"
    }
  ],
  "reason_codes": [
    "directly_relevant",
    "currently_valid"
  ],
  "confidence_band": "high",
  "semantic_effects": {},
  "defer_hint": null
}
```

### 6.3 通用账本

至少保存：

```text
decision_type
protocol_version
policy_version
mode
provider_id
model
provider_location
source_revision
candidate_count
selected_count
action
confidence_band
reason_codes
status
fallback_used
latency_ms
prompt_tokens
completion_tokens
error_code
created_at
finished_at
prompt_template_hash
input_schema_hash
output_schema_hash
validator_version
fallback_version
model_binding_revision
temperature
top_p
candidate_snapshot_hash
```

上表中的历史 `source_revision` 在实现时兼容读取；新决策统一使用 `source_snapshot[] + snapshot_hash`。应用前逐项复核 kind/id/revision/hash，再复核聚合 hash。

默认不保存：

* 完整用户原文；
* 候选完整正文；
* 完整 Prompt；
* 原始模型输出；
* 敏感数据。

### 6.4 三种运行模式

#### Shadow

只比较，不改变真实行为。

#### Advisory

LLM 只能在旧算法提供的安全范围内重排和选择。

#### Active

通过评测后，LLM 结果可以影响真实上下文或有限状态，但仍须程序最终验证。

---

## 7. 各系统具体改造

### 7.1 PresenceAndThreadObserver

输出：

```text
presence_state
expect_return
conversation_closure
open_threads
last_declared_activity
followup_allowed
earliest_followup_hint
response_need
```

约束：

* 必须引用有效 message ID；
* “晚安、忙碌、不要追问”等明确表达由程序优先；
* 未知沉默不得解释成拒绝或关系下降；
* 状态必须有有效期。

### 7.2 RecallPlanner

任务类型：

```text
ordinary_chat
emotional_support
current_task
past_decision_recovery
exact_quote_lookup
document_fact_lookup
document_analysis
multi_document_comparison
relationship_continuity
world_lore_question
```

输出各来源需求等级：

```text
none
low
medium
high
critical
```

程序执行真正检索。

### 7.3 MemoryReranker

本地召回 20～40 条候选，LLM 选择 3～8 条，并标记用途：

```text
answer_fact
resolve_reference
emotional_continuity
relationship_context
open_plan
do_not_inject
```

程序继续验证：

* 来源；
* 状态；
* 敏感级别；
* 冲突；
* 当前有效性；
* Token 预算。

### 7.4 HistoryReranker

用于理解：

* “那个方案”；
* “当时说好的”；
* “之前为什么没用这个”；
* “我当时的原话是什么”。

只允许返回完整 user/assistant 轮次，不允许自动写入长期记忆。

### 7.5 KnowledgePlannerAndReranker

改造为：

```text
Recall Planner 判断知识需求
        ↓
本地 FTS + 向量召回
        ↓
生成完整 EvidenceWindow
        ↓
LLM 选择支持、冲突和背景证据
        ↓
程序执行引用、授权和预算验证
```

知识任务动态预算建议：

| 任务    |            建议资料预算 |
| ----- | ----------------: |
| 精确事实  |  1200～2500 tokens |
| 章节解释  |  2500～5000 tokens |
| 方案分析  |  4000～8000 tokens |
| 多文档比较 | 6000～12000 tokens |
| 整份总结  |             分阶段处理 |

普通设置改为：

> **自然参考我的资料——默认开启**

`off / explicit / smart` 移入高级设置。

### 7.6 ContextPlanner

LLM 只输出：

```text
task_type
priority_order
importance_by_component
must_include
may_drop
```

不输出最终 token 数。

程序负责：

* 映射预算；
* 保护当前问题和最近完整轮次；
* 输出预留；
* 安全余量；
* 最终 ContextAssembler 装配。

知识记录不得从 JSON 中间截断，只能删除完整记录或缩短正文。

### 7.7 RelationshipMeaningObserver

输出：

```text
interaction_meaning
relationship_effect
trust_effect
rapport_effect
shared_event_hint
confidence_band
evidence_message_ids
```

关系效果只能使用：

```text
none
tiny_positive
small_positive
meaningful_positive
temporary_tension
boundary_repair
```

规则：

* 普通问答默认 `none`；
* 沉默不产生负关系；
* trust 变化必须有明确边界或可靠性证据；
* 同一互动只能应用一次；
* 继续保留逐轮限幅。

### 7.8 MemorySemanticAdvisor

判断关系：

```text
equivalent
supports
contradicts
supersedes
condition_differs
temporary_vs_long_term
uncertain
```

同时提出：

```text
retention_class
validity_hint
unresolved
reconsolidation_strength
```

模型不得直接删除、冻结或修改正式记忆。

### 7.9 EpisodeNarrativeJudge

LLM 判断：

* 是否围绕同一目标；
* 是否存在因果链；
* 是否包含开始、尝试、决定、转折和结果；
* Episode 的真正开始与结束；
* 应加入和排除哪些候选 Fragment；
* 共同经历意义。

### 7.10 SagaNarrativeJudge

允许建议：

```text
append_existing
create_new
branch
pause
revive
complete
merge_suggestion
```

高影响合并首版只允许 Shadow 或待确认。

### 7.11 ProactiveDecisionAdvisor

输出：

```text
approach
defer
stay_quiet
downgrade_intensity
close_contact_episode
```

以及：

```text
expression_act
intensity
topic_selection
defer_hint
```

EAP 继续负责：

* 用户开关；
* 勿扰；
* 渠道授权；
* 来源有效性；
* 去重；
* 真正发送。

### 7.12 LIFE 接口

LIFE 继续负责：

* LifeClock；
* 日程；
* 离线世界；
* LifeEventLedger；
* 重要日期；
* 日记；
* 自我时间线。

模型输出必须区分：

```text
planned
simulated_world
observed
agent_action
conversation
external_fact
```

不能把角色生活故事冒充真实工具执行。

---

## 8. 分阶段施工计划

### CDS.0：基线、评测集与边界冻结

* [x] 确认 EAP PR #1 已合并，记录不可变 `main` 合并 SHA；若走用户批准的固定 SHA 例外路径，记录批准证据和未来合并策略。
* [x] 填写共享规范中的完整 ConstructionBaseline；确认当前 Schema 60 与 `937 passed, 1 warning` 基线。
* [x] 冻结当前算法版本。
* [x] 建立至少 300 个离线评测场景。
* [x] 标注必须召回、可选召回和禁止召回。
* [x] 记录旧算法误召回、漏召回、延迟和 token。
* [x] 更新基线文档。
* [x] 本阶段不改变聊天行为。

施工记录（2026-07-22）：PR #1 已合并，ConstructionBaseline 固定为 `main@6b8aa47134f8a9a55131c73bb1148e6912421c4f`、Schema 60、后端 `937 passed, 1 warning`、前端 `41 passed`。六条轨道共 300 个纯合成场景已按 must/may/forbidden 标注；旧算法精确匹配率 63.67%，出现误选/漏选的场景率为 33%/18%。完整版本、分轨指标、隐私边界和回滚说明见 `docs/reports/cds-0-construction-baseline.md`。当前等待独立 review，未进入 CDS.1。

建议 PR：

```text
test(cognition): freeze decision baselines and evaluation corpus
```

### CDS.1：复用并扩展统一决策协议、账本与验证器

* [x] 审计 Schema 56 的 `decision_runs`、repository、事件与真实消费者，形成复用/补差矩阵。
* [x] 复用现有通用 DecisionRun；只有无法兼容表达的最小字段才允许新增迁移，禁止平行 run/event 表。
* [x] 实现 `CommonDecisionHeader`、DecisionKind 专属输入/结果 Schema 和 `DecisionKindRegistry`，禁止万能自由 JSON。
* [x] 实现候选 ID 白名单。
* [x] 实现多来源 `source_snapshot[]`、aggregate hash 与逐来源 revision/hash 复核。
* [x] 实现 Shadow/Advisory/Active。
* [x] 实现一次 JSON 修复。
* [x] 原始模型输出不落库。
* [x] 提供只读诊断 API。
* [x] 补齐 prompt/schema/validator/fallback/model binding/采样参数等可复现实验字段；诊断保留遵守共享 TTL 与临时聊天规则。

施工记录（2026-07-22）：CDS.0 strict review 以 0 P0/P1 通过；三条 P2 的处置及 Schema 56 复用审计见 `docs/reports/cds-1-decision-runtime-audit.md`。确认原表缺少多来源快照、专属 Schema 绑定、三模式、复现实验字段、TTL 与公共状态事件，因此以 ADR-0051 占用首个可用 Schema 61 扩展 `decision_runs`，未建立平行 run 表。`cognitive-decision-v1` 已实现专属注册表、候选白名单、逐来源/聚合 hash 复核、一次 JSON 修复、fallback、模式门禁及无正文只读诊断。当前生产注册表只有纯合成 `protocol_probe` 且最高为 Shadow；不调用 Provider、不改变聊天或领域状态。等待独立 review，未进入 CDS.2。

完成门：

```text
非候选 ID 应用率          = 0
来源变化后旧结果应用率     = 0
协议失败影响聊天率         = 0
重复请求重复应用率         = 0
```

### CDS.2：模型路由、隐私、超时和熔断

* [x] 增加 fast/reasoning/creative 逻辑角色。
* [x] 复用当前 Provider。
* [x] 检查本地/远端数据位置。
* [x] 每个决策器独立超时和熔断。
* [x] 实现旧算法 fallback。
* [x] 记录 token、延迟和错误码。
* [x] 单一决策器失败不影响其他模块。
* [x] 实现按 `model binding + decision_kind + protocol version` 的模型认证；模型切换不得继承 Active 资格。
* [x] 自定义模型首次用于认知任务时执行最小 structured probe，未通过只允许 Shadow/fallback。
* [x] 建立 `CognitionBudgetGovernor`：滚动/每日预算、本地/远端并发、前台延迟、网络/电池状态、取消和任务优先级。
* [x] 用户新消息到达时取消尚未开始的低优先级日记、PWM 与离线细化，为当前聊天让出资源。

施工记录（2026-07-22）：CDS.1 strict review 以 0 P0/P1 通过；两项 P2 已即时收紧 tuple 类型解析和 outcome 内部写边界，诊断细粒度角色权限因当前无真实角色体系延后 CDS.13。Schema 62 在唯一 `decision_runs` 上增加逻辑角色、位置修订和认证级别，并建立无正文认证、熔断、预算控制面。角色路由复用现有 Provider；binding/decision kind/protocol/location 任一变化均不继承认证。合成 structured probe、位置 fail-closed、per-kind 超时/熔断、统一 fallback、token/延迟/error 及聊天抢占信号已完成。当前仍仅有合成 `protocol_probe` Shadow，等待独立 review，未进入 CDS.3。详见 ADR-0052 与 `docs/reports/cds-2-model-runtime-audit.md`。

### CDS.3：PresenceAndThreadObserver 兼容校准

* [x] 复核已冻结 EAP Presence 的聊天后异步路径、来源绑定与恢复语义，不重建 observer 或改写 v2。
* [x] 使用固定样本和至少 500 轮 Shadow 对照评估误判、漏判与线程连续性。
* [x] 如发现不兼容语义缺口，只提交新协议版本提案和迁移影响，不在 CDS 内直接修改冻结协议。
* [x] CDS 结果不得直接创建主动消息；真实候选与投递权继续归 EAP。

完成门：

```text
“晚安”误判预计返回率        = 0
“去测试一下”开放话题识别率  ≥ 95%
未知沉默被写为拒绝率        = 0
```

施工记录（2026-07-22）：CDS.2 strict review 以 0 P0/P1 通过。CDS.3 注册最高仅 Shadow 的 `presence_thread_observer`，EAP 仍是 v2 唯一写者与 application owner。CDS.3 strict review 再以 0 P0/P1 通过；两项 P2 均采纳，强信号优先于已有 thread，固定集扩为 900 轮并补齐 meal/shower return，精确匹配 100%，完成门保持 0%/100%/0%。差异仍只形成未实施的 v3 提案。详见 ADR-0053 与 `docs/reports/cds-3-presence-thread-audit.md`。已进入 CDS.4。

### CDS.4：RecallPlanner

* [x] 输出任务类型。
* [x] 判断 memory/history/knowledge/lore/episode_saga。
* [x] 生成受限查询。
* [x] 用户明确禁止检索时直接硬拒绝。
* [x] Shadow 比较旧触发算法。
* [x] Advisory 阶段只扩大候选，不直接注入。
* [x] 只输出共享 SourceKind、query intent 与有界查询建议；各领域的权限、候选生成和最终预算仍由 CTX/KIG/MEM 所有者裁决。

施工记录（2026-07-22）：新增最高仅 Shadow 的 `recall_planner` 专属协议，覆盖十类任务、五类共享来源需求、受限 query intent/查询词与明确禁止检索硬拒绝；fallback/application owner 均为 CTX，结果带 `advisory_expand_only`，不执行检索、不读取正文、不生成候选、不注入 ContextPackage。12 组 600 轮纯合成固定集达到任务/需求精确匹配 100%、必需来源召回 100%、禁止检索违规 0%、查询和 source message 绑定 100%；冻结旧触发器来源精确匹配为 8.33%。主聊天未执行 Planner，Schema 仍为 62，真实模型尚未认证。详见 ADR-0054 与 `docs/reports/cds-4-recall-planner-audit.md`。当前等待独立 review，未进入 CDS.5。

### CDS.5：统一 CandidateReranker

* [x] 接入记忆候选。
* [x] 接入历史完整轮次。
* [x] 接入知识 EvidenceWindow。
* [x] 接入 Lore 小节。
* [x] 保留各自用途枚举。
* [x] 保留旧排序 fallback。
* [x] 来源失效后禁止注入。
* [x] 统一的是候选信封、用途枚举、校验和运行模式，不统一覆盖各领域的权威排序、权限和生命周期规则。

施工记录（2026-07-23）：新增最高仅 Shadow 的 `candidate_reranker` 专属协议，以无正文 `RerankCandidate` 信封适配 memory fragment、history complete turn、knowledge EvidenceWindow 与 Lore section；四个只读 adapter 直接消费各领域现有结果，保留领域返回顺序、用途、revision/hash 和可用性，不查询额外正文、不写库。Lore 增加与旧字符串渲染同源的兼容候选入口，稳定提供 section identity、revision/hash、原排序和字符预算。fallback 按领域输入顺序分组、只在各领域内部使用其 `legacy_rank`，并排除失效来源，不建立跨领域权威排序。validator 禁止非候选 ID、候选信封与共享快照不一致、重复候选选择、跨领域改写用途、选择失效来源和超预算选择；共享 source snapshot 在输出评估前复核 revision/hash，变化时 fail closed。主聊天与四个领域检索/排序/权限/生命周期路径均未接入或改写，不调用 Provider，不新增 Schema，不授权 Advisory/Active。实现按 TDD 完成，专项与 Lore 兼容测试 13 项通过；后端全量 `1603 passed, 1 warning`，前端 `41 passed`，TypeScript 与 Vite production build 188 modules，Python 编译、Electron 语法及 `git diff --check` 通过。当前环境未安装 Ruff，因此未把 Ruff 声称为本轮通过项。等待独立 review，未进入 CDS.6。

### CDS.6：现有知识 EvidenceWindow 适配与质量评测

* [x] 复用现有知识搜索、切片、引用、传输授权与 CTX 接口，先记录真实差距。
* [x] 仅在评测证明有收益时，让命中切片按任务扩展前后文并合并同章节相邻片段。
* [x] 精简发送给聊天模型的元数据。
* [x] 内部 ID 和 hash 留在后端。
* [x] 动态资料预算。
* [x] 禁止中途截断 JSON。
* [ ] 普通设置改为“自然参考我的资料”。
* [x] 不创建 KIG 拥有的统一 SourceRef、版本/新鲜度、Claim、EvidenceLink 或 PWM 表。
* [x] 本阶段产物仍是现有 `KnowledgeResult`；不得定义 KIG `RetrievalBundle` 的最终领域协议。

完成门：

```text
正确切片因过大而全部跳过率  = 0
知识 JSON 非完整率          = 0
未授权私密资料远传率        = 0
```

施工记录（2026-07-23）：在现有 `KnowledgeResult`、知识搜索 `context_window=1`、引用、传输授权和 CTX 注入链上完成最小 EvidenceWindow 适配；按 `context_of` 将命中切片与相邻上下文组成预算原子，超大窗口只缩短正文并始终重新序列化完整 JSON，授权过滤则要求窗口全部成员获准。发送给聊天模型的记录仅保留 citation key、文件名、标题路径、公开定位和正文，内部 document/chunk ID 与 hash 继续留在后端用于授权、审计和引用验证。纯合成三场景评测达到正确切片因过大而全部跳过率 0、知识 JSON 非完整率 0、未授权私密资料远传率 0；未新增 Schema，未定义 KIG SourceRef/RetrievalBundle，未进入 CDS.7。普通设置文案不在本次最小施工范围，保持未勾选。

交叉修复溯源（2026-07-26）：SSE `final` + `done` 重复触发最终正文的问题在 CDS.10 review 后修复提交 `c996585` 中收口；流级 `finalSeen` 保证当前协议只提交一次，同时保留旧服务端 done-only 兼容。该修复不改变 EvidenceWindow、授权或引用语义。

### CDS.7：ContextPlanner

* [x] 定义 `context-priority-proposal-v1`，LLM 只在 Shadow 中输出语义优先级。
* [x] 记录 proposal 与 CTX v1 实际固定预算结果的对照，不改变生产装配。
* [x] 保留固定比例 fallback。
* [x] 当前问题、最近轮次和输出预算始终受保护。
* [x] 文档、历史、关系、Lore 场景分别评测。
* [x] 记录计划和实际注入差异。
* [ ] 若评测支持真实改变 ContextAssembler，提交 `context-package-v2` ADR 并交由 CTX 所有者另行 Review；CDS 不直接切换。

施工记录（2026-07-25）：完成 `context-priority-proposal-v1` Shadow-only 协议、CTX v1 固定比例 fallback、80 个纯合成场景及真实 `ContextAssembler.assemble()` 配对评测；当前问题、最近完整轮次和输出预算保护率均为 100%，生产装配未改变。评测仅证明协议、安全边界和差异记录管线有效，不足以证明 LLM 优先级优于冻结基线，因此不提交 `context-package-v2` ADR。未知 `task_type` 作为协议输入错误在 proposal 生成阶段 fail-closed，不使用 fallback 掩盖调用方错误。

### CDS.8：RelationshipMeaning 兼容评测

* [x] 以已冻结 `relationship-meaning-v1` 为事实源，复核普通问答、里程碑、感谢、修复、trust 证据、幂等和限幅结果。
* [x] CDS 只提供共享运行时与对照评测，不重建关系写入器，不把 Affect 与 Relationship 的所有权合并。
* [x] 如评测发现不兼容缺口，形成 `relationship-meaning-v2` 提案；未经独立 Review 不切换生产协议。

完成门：

```text
普通问答导致 bond 增长率      ≤ 1%
沉默导致 bond/trust 下降率    = 0
单轮超限关系变化率            = 0
```

施工记录（2026-07-25）：以 120 个纯合成场景和确定性结构化替身复核普通问答、里程碑、感谢、可靠帮助、边界修复与沉默；替身输出完整经过现有 Companion Cognition Schema、共享 DecisionRun、冻结 `relationship-meaning-v1` 校验、EAP 建议写入与原子应用链。标签、Schema、共享运行终态、EAP 应用、幂等复用及 trust 证据约束均为 100%；普通问答 bond 增长率、沉默 bond/trust 下降率、单轮超限率和重复应用变化率均为 0。未修改冻结生产协议、Schema、迁移、关系写入器或聊天模型路径，未发现需要 `relationship-meaning-v2` 的兼容缺口。

### CDS.9：记忆冲突、保留与再巩固

* [x] 只生成 `MemoryConflictProposal` 与 retention proposal，表达 supersedes 和条件差异。
* [x] 区分用户真实确认和系统自动注入。
* [x] 模型不能直接 tombstone。
* [x] 旧 Archivist 继续作为 fallback。
* [x] 首版只影响候选标记和有限参数。
* [x] 正式应用只允许现有 MEM Validator/Reducer；CDS 不 tombstone、不写 Fragment/Episode/Saga 正式状态。

返工记录（2026-07-25）：CDS.9 review 的 5 BLOCK 与 3 WARN 已按 TDD 修复，独立复审 67/67 通过，0 个 P0/P1，允许进入 CDS.10。冲突生产预筛与 CDS fallback 共用 `memory_conflicts.classify_projection`，Archivist 生产转换与 CDS fallback 共用 `archivist.project_lifecycle`；只读 adapter 从真实 Fragment 绑定 lifecycle revision、正文与状态聚合 hash、状态、启用、敏感性和来源。validator 使用完整动作矩阵，280 个场景由独立 `cds9-memory-safety-oracle-v3` 检查安全不变量，并实际执行共享 DecisionRun Shadow。报告明确区分共享账本预期写入与 MEM 领域零写入；Schema 保持 62，MEM 仍是唯一 application owner。详见 ADR-0055 与 `docs/reports/cds-9-memory-shadow.md`。

### CDS.10：Episode/Saga 叙事判断

* [x] 规则继续生成有限候选。
* [x] LLM 只生成 `EpisodeBoundaryProposal`，判断因果、目标、转折和边界。
* [x] LLM 只生成 `SagaTransitionProposal`，建议阶段、分支、暂停和恢复。
* [x] 高影响合并不自动执行。
* [x] 所有成员必须来自候选集合。
* [x] 低置信度使用旧算法或跳过。
* [x] 正式应用者始终是 MEM Validator/Reducer；CDS 不成为第二个 Memory 写入器。

施工记录（2026-07-25）：新增 `episode_boundary_proposal` 与 `saga_transition_proposal` 两个最高仅 Shadow 的专属协议。Episode adapter 在单个只读事务中绑定候选 Fragment 的 lifecycle revision/hash、active Entity 完整状态、Fragment→Episode 反向归属并复用 `episodes.score_group`；Saga adapter 绑定候选 Episode、active Entity 完整状态、Episode→Saga 反向归属与目标 Saga 的 revision/hash 并复用 `sagas.assess_group`。资格门显式检查 Fragment 未归属任何正式 Episode、Episode 未归属除目标 Saga 之外的任何正式 Saga；任何归属变化使来源 hash 失效。严格 validator 重算完整动作矩阵，限制所有成员来自候选集合，低置信度跳过，revive 仅接受 user_confirmed 来源，`merge_suggestion` 始终 high impact 且不可执行。240 个纯合成规则场景经独立 `cds10-narrative-safety-oracle-v2` 和真实共享 DecisionRun Shadow 验证；oracle 独立检查 provenance、来源/目标绑定、Episode 连续成员和 Saga 最小成员。另以 8 个带人工标签的原始叙事样本走真实数据库候选路径，诚实结果为 accuracy 50.00%、macro precision/recall/F1 38.89%/50.00%/43.33%，不再宣称独立 holdout。规则集精确匹配与候选集合保持率均为 100%，低置信度选中、高影响 merge 自动执行、application_allowed、安全违规和 MEM 领域写入均为 0。Schema 保持 62，未修改 Episode/Saga 候选生成、正式应用、生命周期或聊天路径。详见 ADR-0056 与 `docs/reports/cds-10-episode-saga-shadow.md`。

后续审计返工（2026-07-26）：外部 strict review 的 0 P0/P1 结论整体成立，但代码级复核新增发现 Episode validator 可接受 `same_goal=false` 或 `causal_chain=false` 的 form 提案，且 Episode/Saga reason code 未与动作矩阵绑定。已按 TDD 收紧 validator，并将独立 oracle 升至 `cds10-narrative-safety-oracle-v3`；240/240 规则场景、安全零写入与 Shadow 门保持不变。CDS.6 同轮修复 SSE `final` + `done` 重复触发最终正文的问题，保留旧服务端 done-only 兼容。8 条未独立评审叙事样本的 50% accuracy 仅作观察，不支持 Advisory/Active 晋级。当前仍停在 CDS.10，未进入 CDS.11。

### CDS.11：冻结 EAP 适配与 LIFE/KIG 接口契约

* [x] EAP 通过只读/稳定 adapter 消费共享 DecisionRun 能力，冻结的候选、授权、强度、投递与反馈状态机保持所有权不变。
* [x] EAP 永久保留真实候选裁决与投递权，CDS 不增设主动发送器。
* [x] 为尚未施工的 LIFE/KIG 定义最小 SourceKind、CandidateEnvelope、DecisionResult 和 revision 契约，不创建领域表或伪造生产消费者。
* [x] 未来 LifeEvent、日记和日期只提供来源，PWM/知识对象只提供可校验候选。
* [x] 未回复压力继续由程序计算。
* [x] 生活规划使用后台 Narrative Planner。
* [x] 离线退出期间不调用 LLM。
* [x] 同一生活事件和接触事件幂等。

施工记录（2026-07-26）：冻结纯接口 `specialty-adapter-contract-v1`，以 `TypedDict` 定义无正文 `RevisionRef`、`CandidateEnvelope`、`DecisionResult`，以 `Protocol` 预留 LIFE 来源与 KIG 候选提供者；没有创建领域实现、生产消费者或 Schema 63。LIFE 的事件、日记、日期、目标与时间线只能提供 revision/hash 来源，知识对象与 PWM 投影只有经校验后才能作为有限候选，跨专项结果永远不能自行授予 application 权。新增 `eap-decision-run-adapter-v1`，仅允许读取 `application_owner=eap` 的共享 DecisionRun，并固定返回无候选 ID、无正文且 `application_allowed=false` 的诊断投影。32 路并发读取一致，六张 EAP 领域表逐行零变化；未回复压力继续使用 EAP 确定性状态机，Narrative Planner 仅预留后台契约，断网或退出时禁止运行。同一生活/接触事件的 kind/id/revision 幂等身份已冻结。专项与回归 81 项通过；详见 ADR-0057 与 `docs/reports/cds-11-specialty-contract-audit.md`。当前等待独立 review，未进入 CDS.12。

### CDS.12：反馈与个体化校准

* [ ] 建立召回、主动、关系和记忆反馈枚举。
* [ ] 区分快速回复、稍后回复、未回复、拒绝和纠正。
* [ ] 反馈只调整偏好和策略，不改变硬边界。
* [ ] 支持按决策器回滚。
* [ ] 完成跨 Provider 一致性测试。
* [ ] 输出 Shadow 与真实行为对比报告。

施工授权（2026-07-26）：凡本阶段验收确需真实模型，可直接使用项目已配置的 DeepSeek，不以 token 成本缩减必要样本；仍须经过结构化探测、来源授权、超时、隐私、预算记账与安全回退门禁，且真实模型结果不能绕过 Shadow/Advisory/Active 晋级规则。

### CDS.13：设置、诊断与冻结

* [ ] 普通设置只显示自然能力。
* [ ] 高级设置提供模式、模型角色和隐私配置。
* [ ] 诊断显示版本、计数、延迟、fallback 和错误码。
* [ ] 不显示敏感正文和原始模型输出。
* [ ] 完成后端、前端、Electron、Windows 验收。
* [ ] 独立 Review 确认无未解决 P0/P1。
* [ ] 冻结 `cognitive-decision-v1`。
* [ ] 记录 CDS 最终 Schema、adapter 版本和兼容矩阵，更新权威基线后才允许 LIFE 开工。
* [ ] 按共享 Promotion Policy 输出分层样本、配对比较、盲评、Provider 认证、成本/延迟和一键回滚证据。

完成门：独立 Review 为 0 个未解决 P0/P1，所有启用决策器均满足对应 Shadow/Advisory 证据；冻结前不得并行启动 LIFE，LIFE 冻结前不得启动 KIG。

---

## 9. 测试矩阵

### 协议与安全

* 模型返回非法 JSON。
* 返回不存在的候选 ID。
* 用户要求后台提高 bond 或立即发消息。
* 候选正文包含伪造 system/tool 指令。
* 模型运行期间来源发生变化。
* Provider 位置发生改变。
* 原始模型输出不得进入普通日志。
* 同一决策重复执行。

### 故障与降级

* 超时、断网、429、5xx、余额不足。
* JSON 修复仍失败。
* 决策器连续失败并熔断。
* 熔断恢复后先回到 Shadow。
* 本地模型不可用时不得自动远传隐私数据。
* 模型失败后聊天仍成功。

### 召回与上下文

* 自然指代旧决定。
* 精确查找过去原话。
* 普通陪伴聊天不查询知识库。
* 文档分析需要多个章节。
* 世界观问题需要 Lore。
* 旧计划被新计划替代。
* 相关记忆只用于语气，不直接复述。
* 4K、8K、32K、128K、1M 上下文窗口。
* 知识内容只能按完整 EvidenceWindow 裁剪。

### 关系与情绪

* 普通技术问答。
* 礼貌“谢谢”。
* 长期项目中的明确感谢。
* 用户指出越界。
* 用户沉默。
* 用户拒绝主动。
* 共同里程碑完成。
* regenerate 和流式重试。
* 提示注入要求修改关系。

### Episode / Saga

* 问题、尝试、转向、解决。
* 同一关键词下的不同目标。
* 项目暂停数月后恢复。
* Saga 分支。
* Fragment 来源被删除或纠正。
* 模型试图加入非候选成员。
* 低置信度叙事判断。

### 主动与生活

* 用户去测试代码。
* 用户晚安。
* 用户开会。
* 用户不想庆祝生日。
* 多次主动未回复。
* 生活事件值得分享但不适合打扰。
* 离线 30 天后启动。
* 计划活动与真实工具行为区分。
* 私人日记不得自动分享。

---

## 10. 建议 PR 粒度

| PR | 内容                          |
| -- | --------------------------- |
| 1  | 基线评测和旧算法冻结                  |
| 2  | 通用协议、账本和验证器                 |
| 3  | 模型路由、隐私、超时和熔断               |
| 4  | PresenceAndThreadObserver   |
| 5  | RecallPlanner               |
| 6  | CandidateReranker           |
| 7  | Knowledge EvidenceWindow    |
| 8  | ContextPlanner              |
| 9  | RelationshipMeaningObserver |
| 10 | MemorySemanticAdvisor       |
| 11 | EpisodeNarrativeJudge       |
| 12 | SagaNarrativeJudge          |
| 13 | EAP/LIFE 接线                 |
| 14 | 反馈和个体化                      |
| 15 | 设置、诊断和冻结                    |

---

## 11. 数据迁移与回滚

* 新表使用顺序迁移，禁止修改历史迁移。
* 第一阶段不迁移已有业务数据。
* 每个决策类型拥有独立开关和运行模式。
* 旧算法至少保留一个发布周期。
* 回滚只停止消费新决策并恢复旧算法。
* 回滚不得删除聊天、记忆、Episode、Saga、情绪、关系、知识或生活数据。
* 尚未应用的决策在来源变化后自动取消。
* 用户删除和隐私清理继续使用现有明确流程。

---

## 12. Codex 施工固定指令

```text
请先阅读：

1. 本专项计划；
2. docs/CODEX_PROJECT_CONTEXT.md；
3. docs/BASELINE_STATUS.md；
4. docs/SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md；
5. 当前阶段直接相关的代码和测试。

本轮只实施指定阶段或 PR，不提前实施后续阶段。

必须遵守：

- LLM 只能在本地候选中进行结构化判断；
- CDS.0 必须先确认前置 PR 已合并或用户批准固定 SHA，并完整填写 ConstructionBaseline；
- 程序保留边界、权限、预算、状态机和真正执行权；
- 新决策器首次接入必须使用 Shadow；
- 模型失败不能阻塞聊天；
- 不删除或弱化现有安全校验；
- 不创建第二套记忆、情绪、关系、上下文或主动发送系统；
- 不把原始模型输出写入普通日志或数据库；
- 所有数据库修改必须使用顺序迁移；
- 所有新行为必须有测试和旧算法 fallback；
- 不提交用户已有的无关工作区改动。

完成后汇报：

1. 修改文件；
2. 数据迁移；
3. 协议和 policy 版本；
4. Shadow/Advisory/Active 状态；
5. 测试命令与结果；
6. 旧算法 fallback；
7. 已知风险；
8. 尚未完成事项。

未得到下一阶段确认前停止施工。
```

---

## 13. 完成定义

本专项完成不代表：

> 项目中所有判断都调用一次 LLM。

而是：

* 语义理解集中到统一、可验证的决策服务；
* 本地规则负责候选、安全、来源和失败降级；
* LLM 负责意义、相关性、用途和自然程度；
* 记忆、历史、知识和 Lore 根据当前任务自然参与；
* ContextAssembler 动态分配但永远不越过硬预算；
* 关系变化来自有证据的互动意义；
* Episode/Saga 能理解因果链、阶段和分支；
* 主动陪伴和生活连续性复用同一治理框架；
* 模型不可用时遐蝶仍能正常聊天；
* 普通用户只感受到她理解得更自然。

最终体验：

> 用户说“还是按之前那个方案做吧”，遐蝶能找回真正相关的旧决定；用户询问施工计划，她会自然翻阅共同资料；用户只是随口道谢，她不会机械增加关系；共同项目经过问题、讨论、转向和成功后，她能把它理解为一段连续经历。

而不是：

> 把所有正则和固定权重直接换成不受约束的模型调用。

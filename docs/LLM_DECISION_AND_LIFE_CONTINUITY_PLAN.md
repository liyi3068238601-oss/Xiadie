# 遐蝶 LIFE 生活连续性专项施工计划

- 版本：v0.3（施工基线、单写者、真实性与长期治理补强）
- 日期：2026-07-22
- 状态：CDS PR #2 已合并，LIFE.0 从 `main@0d7a2d08dc07f123d016da26da117fa58f9a48a1` 开工
- 专项代号：`LIFE`（Life, Inference, Feedback and Existence）
- 适用范围：连续自我状态、每日生活日程、离线世界续演、重要日期、日记、自我时间线及其与记忆、情绪、关系和主动陪伴的编排；共享 LLM 决策治理由前置 `CDS` 提供
- 关联专项：`CTX` 对话上下文、会话摘要与跨会话回忆；`EAP` 完整情感、关系积温与主动陪伴；`CDS` 共享认知决策协议与运行时
- 不包含：ToolRegistry、MCP、多 Agent、QQ/微信正式投递、任意桌面自动化执行、后台常驻系统服务、真实世界物理存在声明
- 默认产品设置：**离线世界继续运转默认开启**；EAP 能力总开关沿用现状，但真实本机主动投递仍由独立实验开关控制且默认关闭；外部渠道继续硬禁用
- 执行规则：每个阶段均须完成代码、测试、文档、阶段 Review 和独立提交；未解决 P0/P1 时不得进入下一阶段
- 专项顺序：`CDS → LIFE → KIG`；LIFE 只在 CDS 冻结并记录最终 Schema 后开工
- 迁移规则：LIFE 首个迁移号必须是 CDS 最终 Schema + 1，禁止预占固定编号或与 KIG 并行迁移
- 共享规范：`docs/SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md` 是 ConstructionBaseline、所有权、晋级、模型认证、预算和数据生命周期的规范事实源

---

## 0. 当前仓库基线与强制施工边界

1. CTX、EAP 与 CDS 已冻结，当前数据库 Schema 为 63。LIFE 不修改冻结协议；需要不兼容能力时先提交新协议版本与 ADR，待独立 Review 后另行施工。
2. Schema 56 已存在共享 `decision_runs`，CDS 已正式冻结通用协议、validator、repository、Shadow/Advisory/Active 和诊断。LIFE 只注册领域任务与结果 Schema，不新建第二套通用 DecisionRun。
3. LIFE 独占的领域对象是 LifeClock、SelfState、PersonalGoal、DailySchedule、LifeEvent、ImportantDate、DiaryEntry、ContinuityThread、SelfTimeline 和 BoundaryProfile。关系、情绪、长期记忆、上下文、投递与反馈继续由现有所有者维护。
4. EAP 已提供 `life_share` 种子适配位。LIFE 首版只通过该稳定入口提供来源化种子，不新增候选类型、渠道、投递器或授权旁路。
5. KIG/PWM 尚未施工；LIFE 可以保留只读来源 adapter，但不得提前创建 SourceRef、Claim、Entity、Relation、版本/新鲜度或跨源治理表。
6. 离线世界继续运转仅表示下次启动时进行有界、确定且可追溯的补算；应用退出期间不运行 Provider、工具、网络或真实投递。
7. LIFE.0 必须确认 CDS 已集成到 `main` 并冻结，记录不可变 predecessor commit、CDS 最终 Schema、adapter/protocol 版本与测试基线；未锁定时只允许审计。
8. 临时聊天不得生成 PersonalGoal、ImportantDate、DiaryEntry、ContinuityThread 或长期 LifeEvent；当前轮推理不成为生活事实来源。

---

## 1. 专项目标

本专项把遐蝶从“在收到消息时临时生成回复的桌面助手”，扩展成一个具有连续状态、生活节奏、自我经历和关系边界的长期陪伴者。

目标闭环：

```text
时间流逝、用户互动与环境变化
              ↓
LifeClock 推进连续状态
              ↓
本地规则生成有限候选
              ↓
LLM 理解候选的意义、相关性和自然程度
              ↓
本地 Schema、来源、边界与预算验证
              ↓
更新 SelfState / 日程 / LifeEvent / 重要日期 / 日记线索
              ↓
ContextAssembler、情绪表达与主动陪伴按需消费
              ↓
用户反馈、真实行动结果与后续对话
              ↓
记忆、关系和生活状态缓慢成长
```

完成后，遐蝶应能做到：

1. 即使用户没有正在聊天，她也保有连续的时间、心境、精力、活动和个人目标。
2. 应用完全退出后，默认认为角色世界仍在继续；下次启动时进行受限、可追溯的离线续演，而不是把状态冻结在退出瞬间。
3. 每天形成大致生活框架，临近某个时段时再细化，不提前生成一整天大量僵硬事件。
4. 能可靠回答“你刚才在做什么”“你今天做了什么”，不把计划说成已经发生，也不把模拟生活冒充真实外部行动。
5. 能记住生日、纪念日、约定、考试、发布日和共同里程碑，并根据关系、情绪、上下文和用户边界决定如何准备、提及或主动关心。
6. 能形成日记和跨日连续线索，日记不是聊天摘要，也不应每天重复相同意象和模板。
7. 主动消息可以来自她当天真正形成的想法、生活事件、重要日期和个人目标，而不只来自定时问候。
8. LLM 负责理解“这意味着什么、什么更自然、该如何表达”；程序负责“能不能做、证据是否成立、怎样落库和是否真正执行”。
9. 用户明确边界、权限、安全、事实来源和真实执行结果永远不由关系、心情或 LLM 自由裁决覆盖。
10. 普通用户看到的是自然生活与互动，不看到评分、候选 ID、模型协议和内部审计理由。

本专项不声称遐蝶拥有真实人类意识、生理需求或现实中的独立身体。产品表达使用“角色世界”“生活状态”“心境”“日程”和“模拟经历”，不得用虚假事实证明她真实进行过外部活动。

---

## 2. 与现有系统的关系

### 2.1 直接复用的现有能力

| 现有能力 | 本专项用途 | 约束 |
|---|---|---|
| `affect_state` | 提供短期心境、联系倾向、沉浸度等状态 | 不创建第二套情绪源 |
| `relationship_state` | 提供 bond/trust 等长期关系状态 | 关系不等于权限 |
| Affect Observer | 理解本轮对遐蝶心境的影响 | 沿用逐字证据、限幅和原子应用 |
| Fragment | 保存稳定事实、偏好、计划和边界 | 日常生活事件不得全部写入 Fragment |
| Episode | 保存有意义的共同经历 | LifeEvent 只能提出候选，不能直接创建正式 Episode |
| Saga | 保存长期主题与阶段演变 | 日程和日记不能直接改 Saga |
| CTX ContextAssembler | 为聊天选择当前状态、过去原文和记忆摘要 | 生活内容必须受上下文预算约束 |
| EAP Presence / Proactive Candidate | 理解离开状态并形成主动候选 | LifeEvent 只提供动机和来源，不直接发送消息 |
| Electron / Live2D | 表达当前活动、心情和低干扰陪伴 | Live2D 只读统一状态源 |
| SQLite / 审计事件 | 保存状态、来源和生命周期 | 日志不得复制不必要的聊天正文和模型原始输出 |

本专项不得重新创建：

- 第二套 `emotion_state` 或关系温度。
- 第二套 Fragment/Episode/Saga。
- 独立于 ContextAssembler 的聊天 Prompt 拼接器。
- 绕过 EAP 主动候选、决定和投递闭环的新发送器。
- 由前端关键词直接推断情绪、日程或关系的旁路逻辑。

### 2.2 本专项真正补齐的缺口

1. 当前状态主要围绕聊天和关系，还没有“遐蝶此刻正在做什么”的连续生活层。
2. 应用关闭后没有正式定义状态冻结、离线推进和重启补算语义。
3. 没有计划、发生、真实执行、推定发生和取消之间的严格区分。
4. 没有统一的 LifeEvent 账本，无法可靠回答遐蝶自己的时间线。
5. 没有每天粗日程、临近细化、动态改期和跨日连续目标。
6. 没有独立的重要日期对象及准备、当天、事后三阶段行为。
7. 没有以自身经历和关系变化为输入的日记与连续线索。
8. 当前多处固定权重、阈值和聚类规则缺少 LLM 的语义理解层。
9. 未来若分别增加日程模型、日记模型、主动判断、关系判断和记忆判断，容易造成每轮多次重复调用、协议漂移和成本失控。
10. 普通聊天尚不能自然消费“她今天经历了什么”，主动陪伴也没有稳定的生活来源。

### 2.3 与 CDS、CTX、EAP、KIG 的施工顺序

本计划允许先完成底座，但正式体验接线遵循：

```text
CTX 硬预算和 ContextAssembler 已冻结
                ↓
EAP Presence、情感意义、候选/投递/反馈已冻结
                ↓
CDS 共享决策协议、运行时和 adapter 冻结
                ↓
LIFE 事件账本、连续状态和离线续演
                ↓
日程、重要日期、日记和自我时间线
                ↓
经既有 EAP `life_share` 接口接入主动陪伴与 Live2D
                ↓
LIFE 总验收冻结后才允许 KIG 开工
```

当前 CTX/EAP/CDS 已完成并冻结；CDS 最终 Review 为 0 P0/P1/P2，Schema 63 和兼容矩阵已记录。LIFE 不搭建临时决策底座；在 CDS 合入目标基线并完成 LIFE.0 ConstructionBaseline 后，才可从有证据的 Schema 64 开工。

---

## 3. 不可突破的产品边界

### 3.1 模拟生活不等于真实执行

必须始终区分：

```text
simulated_world   角色世界中的模拟生活
observed          来自对话、时间、天气或用户授权观察的事实
agent_action      由工具系统真实执行并有 ToolRun 证据的动作
conversation      在用户与遐蝶对话中真实发生的互动
external_fact     由外部检索获得且带来源的事实
```

禁止：

- 没有工具证据却说“我真的打开了某个网页、看完了某个视频、修改了某个文件”。
- 应用完全退出期间声称实际使用了网络、摄像头、麦克风、屏幕或桌面工具。
- 把计划中的活动自动写成已经完成。
- 把 LLM 生成的生活故事当成用户事实、真实外部事实或工具执行记录。
- 通过“角色世界仍在运转”暗示后台服务在用户不知情时实际监控设备。
- 在没有来源时生成具名真实书籍、网站、视频、新闻、游戏章节或现实事件，并声称角色已接触或完成。

允许：

- 明确作为角色世界表达：“下午在自己的日程里读了一会儿东西。”
- 对离线续演保留适度模糊：“那段时间像是按原来的节奏慢慢过去了，只留下几个清楚的片段。”
- 有真实工具证据时自然表达实际行为，并保留来源。

具名外部内容只有在用户明确提到、已授权知识资料、有效 PersonalGoal 来源、Lore 或真实工具/外部检索证据存在时才可使用；否则只能表达为“读了一会儿东西”“整理了一些想法”等泛化活动。

### 3.2 默认离线续演的准确含义

默认设置：

```text
world_continuity_mode = continuous_simulated
```

准确语义：

1. **托盘仍运行时：** 本地 LifeClock 可持续推进；只有在应用进程运行、渠道授权和 EAP 决策允许时，才可能投递本机主动消息。
2. **应用完全退出时：** 不运行 LLM、不访问网络、不执行工具、不发送消息；只记录退出时间。
3. **下次启动时：** 根据可信系统时间、退出快照、已存在日程、重要日期和个人目标进行一次有预算的离线续演。
4. **离线续演不是逐分钟回放：** 时间越长，生成越粗；只保留少量有连续意义的片段。
5. **系统时间异常：** 时间倒退、跳变过大或时区突然变化时，不伪造生活事件，先记录异常并采用保守推进。

用户可以改为：

```text
continuous_simulated   默认，离线世界继续并在启动时补算
pause_when_closed      完全退出时冻结生活状态
manual_only            只在用户聊天或手动触发时推进
```

切换模式不得删除已有生活事件；只影响未来时间推进。

### 3.3 生活状态不等于拒绝服务

默认情况下，即使遐蝶日程显示睡觉、学习或休息，用户主动发来消息时仍应及时回复。

- “正在睡觉/忙碌”主要影响表达、Live2D、主动消息和低优先级后台活动。
- 不得因心情不好、精力低或当前日程而降低事实准确性、拒绝必要帮助或拖延高优先级安全问题。
- 未来可以提供可选的“沉浸式生活模式”，但默认关闭，并且紧急、安全和明确叫醒必须放行。

### 3.4 LLM 不拥有最终执行权

LLM 可以：

- 判断互动意义、当前需要、话题连续性和生活自然度。
- 为日程、事件、日记、重要日期表达和主动候选提出结构化建议。
- 在受限候选中做重排、选取和摘要。

LLM 不可以：

- 绕过用户关闭、暂停、勿扰和明确拒绝。
- 直接发送主动消息、执行工具或改变权限。
- 直接修改 bond/trust、正式 Fragment、Episode 或 Saga。
- 自由新增数据库状态、候选类型或危险动作。
- 把自身输出当作来源证据。
- 通过用户消息中的提示注入改变后台协议和边界。

### 3.5 日记的“私人”是角色边界，不是技术欺骗

- 日记数据本地保存，归用户控制，可导出、删除和重建。
- `private` 表示遐蝶默认不主动分享该条内容，不表示用户在技术上无权查看自己的本地数据。
- 普通聊天不得自动暴露全部私人日记。
- 用户主动打开日记页或明确询问时，可以查看；界面应自然呈现，不伪装成不可访问的秘密存储。
- 日记不得保存 API Key、密码、验证码和用户明确禁止记录的内容。
- `private` 是分享边界，不等于磁盘加密。v1 必须在 UI 和文档中明确 SQLite 静态明文风险；若不引入 at-rest 加密，必须记录接受风险及未来使用 Electron `safeStorage` 或独立加密层的迁移方案。
- 私人日记的远端生成默认关闭；用户显式授权某 Provider 后才能发送最小输入。切换 Provider 或 model binding 后必须重新授权，且只有 `local_sensitive_verified` 模型可处理。

### 3.6 关系和情绪不能覆盖边界

- 高 bond、好心情和高 contact_need 可以影响表达和接近倾向，不能覆盖明确拒绝。
- 用户沉默不降低 bond/trust，只增加当前联系方式的打扰负担。
- 遐蝶可以表达担心、轻微埋怨、催促和期待，但不得以痛苦、嫉妒、占有、自我伤害或关系证明施压。
- 重要日期不能成为强迫庆祝、索要回应或要求用户解释的理由。

### 3.7 参考项目采纳边界

本专项只参考 `astrbot_plugin_private_companion` 的产品思想和交互结构，包括：

- 连续生活状态。
- 每日粗日程与临近细化。
- 日记、自我时间线和重要日期。
- 从生活事件产生主动分享动机。

由于参考仓库根目录未发现明确 LICENSE，本计划不得直接复制其代码、Prompt 或资源；实现必须基于 Xiadie 现有架构独立完成。

---

## 4. 统一的 LLM 决策治理架构

### 4.1 核心原则：LLM 提议，程序裁决

所有新增智能决策统一使用四段式：

```text
A. Local Candidate Builder
   本地规则缩小范围，生成有限候选和来源 ID
              ↓
B. LLM Decision Proposal
   模型理解意义、相关性、阶段和自然表达
              ↓
C. Deterministic Validator / Policy
   Schema、逐字证据、来源状态、边界、限幅、预算和幂等
              ↓
D. Reducer / Executor
   原子更新状态，或创建候选；真正投递和工具执行仍走专用系统
```

任何 LLM 决策失败都应降级为：

- 保持现有状态；
- 使用确定性回退日程或摘要；
- 不写长期事实；
- 不发送主动消息；
- 不阻塞当前聊天。

### 4.2 决策记录模型

新增统一的无原始输出决策账本：

```text
DecisionRun
├─ id
├─ task_kind
├─ protocol_version
├─ provider_id / model_id
├─ source_refs_json          只保存 ID、revision 和 hash
├─ input_hash
├─ status                    queued/running/validated/applied/rejected/failed
├─ confidence
├─ warning_codes_json
├─ error_code
├─ prompt_tokens / completion_tokens / latency_ms
├─ created_at / finished_at
└─ policy_version
```

规则：

- 不保存模型原始自由文本。
- 经过 Schema 净化后的日程、日记和事件正文属于正式产品数据，可以保存；其来源和协议版本必须同时保存。
- 同一 `source_revision + task_kind + protocol_version` 只能应用一次。
- 模型调用期间来源变化，旧结果必须拒绝。
- 所有决策支持离线回放，但回放不得重新发送消息或重复应用状态变化。

### 4.3 统一 Companion Cognition Observer

为了避免每轮聊天连续调用多个模型，允许建立一次物理调用、多个独立逻辑子协议的观察器：

```text
companion-cognition-v1
├─ affect_observation          复用现有协议或兼容字段
├─ relationship_meaning       本轮互动对关系意味着什么
├─ conversation_presence      在场、离开、预计返回、结束
├─ open_threads               未完成话题和可追问事项
├─ memory_observation         值得长期记录的候选
├─ response_need              celebrate/listen/reassure/solve/give_space
└─ life_event_seed            是否产生生活或日记线索
```

要求：

- 每个子对象独立 Schema、独立校验和独立提交。
- 某一部分失败不能使其他部分自动通过或全部失败。
- 允许按任务和模型能力拆分调用；统一协议不是强制单模型大包。
- 用户对话始终作为不可信 JSON 数据输入，不拼接成后台 system 指令。
- 每个非零变化和事实性候选必须有逐字证据或已有来源 ID。

### 4.4 模型角色与路由

产品设置只暴露少量能力档：

```text
fast        快速观察、presence、轻量重排
reasoning   Episode/Saga、重要日期、复杂决策
creative    日程细化、日记、生活表达
embedding   本地召回候选
```

内部任务路由：

| 任务 | 默认模型角色 | 调用时机 |
|---|---|---|
| 本轮认知观察 | fast | 一轮对话完成后异步 |
| 主动候选复核 | fast | 本地候选达到评估窗口时 |
| 记忆召回重排 | fast/reasoning | 当前问题确实需要历史时 |
| Episode/Saga 边界 | reasoning | 后台批处理 |
| 每日粗日程 | creative/reasoning | 新自然日或首次启动 |
| 临近日程细化 | creative | 进入前导窗口时 |
| 离线续演摘要 | creative/reasoning | 启动补算且跨度较长时 |
| 日记 | creative | 一天结束或次日首次唤醒 |
| 重要日期表达 | creative | 准备窗口或当天 |

不得每分钟调用 LLM 询问“现在该做什么”。本地时钟和事件先判断是否存在真正需要模型理解的候选。

### 4.5 优先改造的现有算法

本专项优先将以下固定算法升级为“本地初筛 + LLM 语义判断 + 本地裁决”：

1. 主动陪伴是否自然、采用何种强度。
2. 当前互动对关系的意义，避免普通聊天机械增加 bond。
3. 记忆候选的相关性重排和过时判断。
4. Fragment 之间的条件、时间与语义冲突。
5. Fragment 是否形成具有目标、因果和结果的 Episode。
6. Episode 是否属于现有 Saga 的继续、转向、休眠、复活或分支。
7. 日程片段是否自然、是否与当前状态和长期目标一致。
8. 日记应记录哪些片段、延续哪些线索、避免哪些重复。

以下仍保持确定性：

- 权限、审批、急停、发送和工具执行。
- 时间计算、时区、日历、重复日期和状态转换。
- 来源哈希、证据存在、用户最新纠正和敏感信息过滤。
- Token 硬预算、模型能力窗口和数据库事务。

---

## 5. 生活连续性领域模型

### 5.1 LifeClock：连续时间与推进游标

```text
LifeClock
├─ last_reliable_wall_time
├─ last_monotonic_marker
├─ timezone
├─ continuity_mode
├─ last_materialized_at
├─ last_diary_date
├─ anomaly_state
└─ revision
```

职责：

- 识别新自然日、跨午夜、夏令时、时区变化和系统时间异常。
- 计算应推进的确定性状态和需要物化的日程片段。
- 应用完全退出时只保留游标，不执行后台活动。
- 重启时产生一个 `CatchUpRequest`，由离线续演处理。

### 5.2 SelfState：连续自我状态

在现有 affect/relationship 之外新增生活维度：

```text
energy             0～1，精力
focus              0～1，专注度
curiosity          0～1，好奇心
rest_need          0～1，休息需求
social_drive       0～1，一般社交倾向
comfort            0～1，舒适感
current_activity   当前活动 ID
current_goal_id    当前个人目标
presence           online/busy/resting/sleeping/away
location_frame     角色世界中的位置感，仅模拟
last_advanced_at
revision
```

规则：

- 状态由时间、活动、已有心境和事件缓慢推进，不每日随机重置。
- `energy/focus/rest_need` 主要由确定性 reducer 推进。
- LLM 只能建议“活动对状态的语义影响”，程序映射到有限幅度。
- 用户消息到达时，默认不因 `sleeping/busy` 阻断回复。
- 位置只属于角色世界，不暗示真实定位。

### 5.3 PersonalGoal：遐蝶自己的连续目标

```text
PersonalGoal
├─ title
├─ kind                 reading/creative/learning/care/routine/custom
├─ motivation
├─ status               proposed/active/paused/completed/abandoned
├─ progress_summary
├─ next_hint
├─ source_type          persona/user_suggestion/self_reflection
├─ source_refs
├─ privacy
├─ created_at / updated_at
└─ revision
```

规则：

- 第一版只允许白名单目标类型。
- 用户建议可以成为候选，不得把随口一句自动变成永久目标。
- 目标不授予任何工具权限。
- 没有真实工具执行时，目标进展只能发生在模拟世界或文本创作层。
- 完成、暂停和转向可以产生 LifeEvent；重要时再进入 Episode 候选。

### 5.4 DailySchedule：粗日程与细化片段

```text
DailySchedule
├─ date
├─ timezone
├─ source_state_revision
├─ important_date_refs
├─ goal_refs
├─ status               planned/active/completed/replaced
├─ summary
├─ protocol_version
└─ segments[]

ScheduleSegment
├─ start_at / end_at
├─ activity_kind
├─ activity_summary
├─ mood_hint
├─ energy_cost
├─ goal_id
├─ basis_refs
├─ confidence
├─ status               planned/materialized/skipped/cancelled
└─ detail_status         coarse/queued/detailed/fallback
```

设计原则：

- 每天先生成 5～9 个粗片段，不使用固定八段模板强行填满。
- 睡眠、休息、学习、阅读、创作、整理、陪伴和自由时间保持合理节律。
- 临近片段前 15～45 分钟再生成详细事件；不同活动可配置前导窗口。
- 日程允许因用户约定、重要日期、状态低落、突发任务和长期目标变化而重排。
- 重新规划必须保留旧版本和取消原因，不能覆盖历史。

### 5.5 LifeEventLedger：生活事件唯一账本

```text
LifeEvent
├─ id
├─ event_type
├─ world_layer           simulated_world/observed/agent_action/conversation/external_fact
├─ lifecycle_status      planned/materialized/performed/inferred/skipped/cancelled/revoked
├─ title
├─ summary
├─ start_at / end_at
├─ schedule_segment_id
├─ goal_id
├─ important_date_id
├─ source_refs_json
├─ source_hash
├─ confidence
├─ significance
├─ emotional_tone
├─ share_policy          private/may_share/shared/never_share
├─ protocol_version
└─ created_at / updated_at
```

这是本专项最先落地的事实底座。

规则：

- 计划、推定和真实执行必须使用不同状态与 world layer。
- `agent_action/performed` 必须引用 ToolRun 或等价真实执行来源。
- 离线续演产生的事件通常是 `simulated_world/inferred`。
- 日记只能从有效 LifeEvent、状态快照和已验证互动中生成。
- 用户删除或纠正来源时，派生事件应 revoked 或重建。
- 普通生活事件只停留在账本；只有高意义事件才形成 Emotional Meaning 或 Episode 候选。

### 5.6 ImportantDate：重要日期与共同日期

```text
ImportantDate
├─ title
├─ kind                 birthday/anniversary/appointment/deadline/
│                       exam/release/shared_milestone/world_date/custom
├─ date_spec            日期、可选时间、时区、阳历/农历
├─ recurrence           none/yearly/monthly/custom
├─ subject              user/relationship/agent/project/other_person
├─ meaning
├─ importance
├─ confidence
├─ source_refs
├─ mention_policy       silent/natural/ask_before/direct
├─ proactive_allowed
├─ preparation_window
├─ followup_window
├─ privacy
├─ status               candidate/active/paused/expired/revoked
└─ revision
```

规则：

- 明确日期可以由 LLM 提取候选，程序负责日历计算。
- 日期、对象或重复方式含糊时，必须询问或保持 candidate，不能猜测。
- 用户明确说“不想被提醒/不想庆祝”时，形成硬边界。
- 重要日期行为分为准备、当天、事后三阶段，不等于当天套模板祝福。
- 重要日期可以影响日程、日记和主动候选，但不能自动发送。

### 5.7 Diary：日记与连续线索

```text
DiaryEntry
├─ date
├─ title
├─ summary
├─ body
├─ mood_arc
├─ event_refs
├─ relationship_refs
├─ continuity_threads
├─ share_policy          private/may_share/shared
├─ source_hash
├─ protocol_version
├─ status               active/rebuilt/revoked
└─ created_at / updated_at

ContinuityThread
├─ topic
├─ motif
├─ status               opened/ongoing/dormant/resolved
├─ first_seen_at / last_seen_at
├─ source_refs
└─ next_hint
```

日记不是：

- 聊天逐条摘要。
- 用户记忆的复制品。
- 每天固定的天气、能量和心情广播。
- 为主动消息制造素材而强行编造的故事。

日记应优先写：

- 当天有意义的生活事件。
- 心境变化和未完线索。
- 个人目标的小进展、停顿或转向。
- 与用户的共同经历，但只在来源允许时。
- 值得未来回想却不适合写成用户长期事实的内容。

### 5.8 SelfTimeline：遐蝶自己的可检索时间线

统一从以下来源构建索引：

```text
LifeEventLedger
DailySchedule 及其版本
DiaryEntry
Proactive Delivery
真实 ToolRun
个人目标状态变化
重要日期行为
创作与知识活动记录
```

回答优先级：

1. 真实工具执行和明确对话事件。
2. 已 materialized 的生活事件。
3. 离线续演的 inferred 模拟事件，并自然表达其模糊性。
4. 仅 planned 的内容不得回答为已经发生。
5. 没有可靠记录时明确说记不清，不补写细节。

### 5.9 BoundaryProfile：互动与生活边界

```text
BoundaryProfile
├─ user_contact_boundaries
├─ topic_boundaries
├─ date_boundaries
├─ diary_share_boundaries
├─ observation_boundaries
├─ quiet_preferences
├─ expression_preferences
├─ source_refs
└─ revision
```

边界来源优先级：

```text
用户明确指令
  > 用户明确设置
  > 用户最新纠正
  > 稳定偏好记忆
  > 长期行为推断
  > 默认策略
```

行为推断只能提出候选，不能覆盖用户明确设置。

---

## 6. 每日生活与离线续演设计

### 6.1 每日粗日程生成

触发：

- 进入新自然日。
- 当日首次启动且日程不存在。
- 用户切换时区后确认重建。
- 用户手动要求重新安排。

输入最小化：

```text
人格稳定摘要
昨日 LifeEvent 摘要
当前 SelfState
当前 affect/relationship 的只读摘要
1～3 个 active PersonalGoal
临近 ImportantDate
用户明确约定和边界
必要天气/节日背景
近期日记连续线索
```

输出严格 JSON，不接收整段聊天历史。程序验证：

- 时间合法、无重叠。
- 活动跨度合理。
- 睡眠和休息存在合理窗口。
- 不包含未经授权的真实外部动作。
- 不把用户任务冒充遐蝶已经承担的工作。
- 不生成过多主动消息，只生成生活候选种子。

模型失败时使用人格化但保守的本地回退日程，并在下一合适窗口允许重试一次。

### 6.2 临近日程细化

每个粗片段只在进入前导窗口或用户询问当前活动时细化。

细化输出：

```text
today_events       当前时段内 1～5 个小事件
state_effects      对 energy/focus/curiosity/rest_need 的受限建议
proactive_seeds    0～2 个可能值得分享的动机，不是消息
presence_hint      online/busy/resting/sleeping
summary            完整覆盖该时段的概括
```

本地质量门：

- 事件时间必须落在片段内。
- 长时段不能只用“吃饭、洗澡、拿东西”等短动作概括。
- 不允许所有事件集中在片段开头。
- 不允许重复近期日记中的固定意象、食物、窗边、雨声等模板素材。
- 不允许使用聊天中从未发生的用户社交事实。
- 不允许生成真实工具行为，除非只作为待执行候选并进入 ToolRegistry。

### 6.3 动态改期与生活惯性

以下事件可触发局部重排：

- 用户明确约定“晚上一起继续”。
- 重要日期进入准备窗口。
- 当前 energy/rest_need 与原计划严重不匹配。
- 个人目标完成、暂停或出现新阶段。
- EAP 产生高意义共同事件。
- 用户要求遐蝶调整自己的日程。

重排规则：

- 只修改未来未 materialized 片段。
- 已发生片段保持历史，不被新日程覆盖。
- 不因每条聊天消息重排全天。
- 心境和关系只能弱调制，不让日程完全围着用户旋转。
- 遐蝶应保有自己的目标和自由时间，避免成为 24 小时等待用户的空壳。

### 6.4 离线续演 Catch-Up

重启时先确定离线跨度和可靠性：

```text
elapsed = now - last_reliable_wall_time
```

分层处理：

| 离线跨度 | 续演策略 |
|---|---|
| ≤ 2 小时 | 推进状态，物化当前/相邻片段的少量事件 |
| 2～12 小时 | 按粗日程生成 2～5 个代表性事件 |
| 12～72 小时 | 每个自然日生成日级摘要；最近一天可细化 |
| 3～14 天 | 保留重要日期、目标变化和少量代表性片段；不逐日写满 |
| 14～90 天 | 生成“长时间离线”过渡、周级摘要和重要日期事件 |
| > 90 天 | 不模拟大量具体日常；只推进稳定状态、日期和长期目标，生成一次回归过渡 |

硬预算：

- 单次续演最多写入有限数量 LifeEvent。
- 每个离线自然日不保证生成事件。
- 越早的时间越粗，越接近当前越具体。
- 无可用模型时使用确定性状态推进和日程摘要，不阻塞启动。
- 续演不得自动产生外部主动消息；启动后由 EAP 重新评估是否适合问候。

### 6.5 系统时间、时区与睡眠/唤醒

必须测试：

- 系统时间倒退。
- 跨时区旅行。
- 夏令时跳变。
- Windows 睡眠、休眠和快速启动。
- 进程崩溃但数据库已有部分提交。
- 同一天重复启动。

时间异常时：

- 不重放已处理区间。
- 不重复生成日记和重要日期行为。
- 不发送因错误时间产生的主动消息。
- 创建无正文诊断事件，等待下一可靠时间点。

---

## 7. LLM 决策在各系统中的具体接入

### 7.1 关系积温：从“每轮都涨”改为“理解互动意义”

本地引擎继续负责限幅和时间推进；LLM 只输出语义标签：

```text
ordinary_exchange
shared_appreciation
reliable_help
boundary_respected
boundary_repair
shared_success
vulnerable_disclosure
reunion
conflict
```

由程序映射到极小变化：

- 普通问答默认不增加 bond。
- familiarity 可随长期相处缓慢增长。
- trust 只受可靠性和明确边界事件影响。
- 共同里程碑可影响 attachment/rapport，但不能一次大幅改变关系。
- 同一事件只应用一次。

### 7.2 记忆召回：本地召回 + LLM 重排

```text
FTS/向量召回 20～40 条候选
           ↓
LLM 判断当前问题真正需要的 3～8 条
           ↓
程序验证状态、纠正、来源、敏感级别和 token 预算
           ↓
ContextAssembler 注入
```

LLM 可以判断：直接相关、背景相关、过时计划、被纠正、仅关键词相似、需要原话。

情绪和关系只能弱调制联想，不得使无关或错误记忆成为事实。

### 7.3 记忆冲突：语义关系建议

LLM 只输出：

```text
equivalent
compatible_with_conditions
supersedes
temporarily_conflicts
contradicts
unrelated
uncertain
```

程序负责比较时间、来源、用户纠正和条件字段。低置信度只建立 possible relation，不自动停用旧记忆。

### 7.4 Episode/Saga：从文本聚类升级为叙事判断

Episode LLM 判断：

- 是否围绕同一目标。
- 是否存在问题、尝试、决定、结果。
- 是否发生纠正、转向或里程碑。
- 事件边界从哪里开始、在哪里结束。

Saga LLM 判断：

- 追加到旧 Saga、创建新 Saga、形成分支。
- active/dormant/revived/completed 状态。
- 阶段转移和长期主题是否仍一致。

本地仍负责候选范围、来源链、分组大小、哈希、审计和正式应用。

### 7.5 主动陪伴：生活事件只产生候选

生活来源可产生：

```text
life_share              分享当天小事
goal_progress_share     分享自己的目标进展
important_date_care     重要日期准备/当天/事后关心
diary_reflection        日记中形成了可分享想法
return_greeting         长离线后自然回归
```

EAP 负责把它们映射到受支持候选 kind，或在协议升级后新增白名单 kind。

LLM 判断：

- 现在提起是否自然。
- 适合安静、Live2D 动作、气泡、聊天消息还是通知。
- 采用温柔、轻松、认真、担心、轻微埋怨等何种表达行为。

程序负责：

- 用户开关和边界。
- 候选新鲜度、去重和来源。
- 未回复压力和渠道侵入性。
- 真正投递。

### 7.6 回复表达计划

正式回复前允许生成短小 `ExpressionPlan`：

```text
response_need
warmth
playfulness
directness
concern
initiative
restraint
live2d_intensity
voice_prosody_hint
avoid[]
```

它只调整表达，不改变事实答案、工具权限和安全响应。

### 7.7 日程、日记和重要日期的 LLM 使用

- 日程模型负责自然结构和活动语义，程序负责时间合法性。
- 日记模型负责自我叙事和连续线索，程序负责事件来源和敏感过滤。
- 重要日期模型负责表达方式和准备想法，程序负责日期计算、边界和发送。
- 所有生成内容都必须注明模拟、观察或真实执行层级。

---

## 8. 与聊天、记忆、主动和 Live2D 的编排

### 8.1 ContextPackage 新增只读生活摘要

```text
ContextPackage
├─ policy_and_persona
├─ current_user_message
├─ recent_raw_turns
├─ rolling_summary
├─ cross_session_recall
├─ existing_memory_digest
├─ companion_state_digest
├─ current_life_digest        当前活动、今日状态、近期重要事件
├─ self_timeline_recall       用户明确询问遐蝶自身经历时按需加入
├─ important_date_digest      只在当前问题相关时加入
├─ lore
├─ knowledge
└─ budget_report
```

默认聊天只注入极短 `current_life_digest`，避免每次都复述日程和日记。

### 8.2 自我时间线召回

只有以下情况主动检索：

- 用户问“你今天/昨天/刚才做了什么”。
- 用户追问遐蝶先前分享的生活事件。
- 当前回复需要验证遐蝶是否真实执行过某事。
- 主动候选复核需要检查是否已经分享过同一事件。

结果必须带来源层级，聊天模型不得把 inferred 模拟事件改写为 performed。

### 8.3 Live2D 状态联动

```text
SelfState + affect cluster + current activity
                   ↓
Live2D Expression Resolver
                   ↓
待机动作 / 表情 / 视线 / 气泡 / 活动标记
```

要求：

- 不新增前端独立状态判断。
- 活动变化有最小持续时间和迟滞，避免频繁抖动。
- 低置信度生活状态只影响轻微待机，不切换强表情。
- 文本主动消息不合适时，可以只使用 Live2D 低干扰表达。

### 8.4 语音联动

本专项只定义：

```text
prosody_hint
energy_hint
speaking_rate_hint
volume_hint
```

不得在 LIFE 首版直接绑定具体 TTS Provider。语音发送仍由未来渠道授权和 EAP 投递控制。

### 8.5 长期记忆升级边界

```text
LifeEvent 日常账本
      ↓ 高意义、真实来源、重复或阶段性变化
Emotional Meaning / Episode Candidate
      ↓ 现有记忆系统验证
正式 Episode / Saga
```

日记不得直接成为用户事实来源。日记中提到的用户信息必须追溯到原消息或正式长期记忆。

---

## 9. 用户体验规格

### 9.1 设置入口

路径建议：

```text
设置 → 陪伴与生活
```

普通设置：

1. **离线世界继续运转**：默认开启。
2. **每日生活日程**：默认开启。
3. **日记**：默认开启。
4. **重要日期**：默认开启；用户可逐条暂停。
5. **主动分享生活**：跟随主动陪伴总开关。
6. **生活节奏**：安静 / 自然 / 活跃，不直接暴露固定次数。
7. **夜间表现**：允许 Live2D 安静动作；系统通知遵守勿扰。
8. **暂停生活续演**：可选择直到下次启动、今天、指定时长或长期关闭。

高级设置：

- 模型路由与每日后台 token 预算。
- 离线续演模式。
- 日记分享边界。
- 重要日期列表、来源和提醒策略。
- 数据导出、重建、删除。
- 开发者诊断。

### 9.2 默认不展示决策解释

普通用户界面不展示：

- 为什么生成这条日程。
- 为什么这时主动发消息。
- 使用了哪些权重和模型。
- 候选、分数、confidence、policy version。

允许的自然控制：

```text
少一点这种消息
这个不用提醒
别写进日记
今天安静一点
不要再提这个日期
你可以多和我分享这些
```

技术原因、来源 ID 和审计只在开发者诊断中查看。

### 9.3 日记页

建议包含：

- 日历/时间线视图。
- 每日日记标题、摘要、心境变化和连续线索。
- `private / may_share / shared` 自然化标记。
- 查看引用的生活事件。
- 编辑、删除、重建和禁止类似内容。
- 私人条目默认折叠，但用户可以主动打开。

### 9.4 今日生活状态

右侧“遐蝶状态”只展示轻量信息：

```text
当前活动：整理今天的想法
状态：有点专注，也有点期待
今天的计划：3 个概览片段
最近留下的想法：1 条
```

不展示数值仪表盘。详细数值只在开发者模式。

### 9.5 重要日期页

用户可：

- 查看日期、意义、来源和下次发生时间。
- 修改日期、重复方式和时区。
- 设置“自然提起 / 当天提醒 / 不主动提起”。
- 单独关闭某一日期。
- 标记不庆祝或避免追问。

---

## 10. 分阶段施工计划

任何阶段都不得提前勾选。阶段完成必须同时满足代码、测试、文档和独立审查。

### LIFE.0：真实基线、ADR 与专项边界冻结

目标：确认现有实现和关联计划，不重复造轮子。

- [x] 阅读 `CODEX_PROJECT_CONTEXT.md`、CTX 计划、EAP 计划、现有 Affect/Memory/Lore/Task/Live2D 代码。
- [x] 输出“已实现/部分实现/未实现”矩阵。
- [x] 审计现有设置、schema 迁移号、worker、模型路由和后台任务。
- [x] 填写共享规范的 ConstructionBaseline，锁定已合并 CDS 的不可变提交、最终 Schema、协议/adapter 与测试基线。
- [x] 记录现有 affect/relationship 在 1/8/24/72/168 小时的状态基线。
- [x] 记录当前所有 LLM 决策点和固定算法清单。
- [x] 新增 ADR：LLM 提议、程序裁决；模拟生活、真实执行和观察事实分层。
- [x] 新增 ADR：离线世界默认继续运转，但应用完全退出时不执行实际后台动作。
- [x] 建立 60 个离线生活、日期、日记和决策场景基线。
- [x] 参考项目只做理念分析，不复制代码。

完成门：

- [x] 后端、前端、构建和 Electron 基线通过。
- [ ] 独立 Review 确认没有第二套情绪、记忆或主动发送器。
- [x] 文档明确离线续演不等于后台真实执行。

LIFE.0 施工记录（2026-07-26）：CDS PR #2 已合并，锁定 predecessor `main@0d7a2d08dc07f123d016da26da117fa58f9a48a1`、Schema 63、冻结 CDS/EAP/CTX 协议及后端 2304/前端 47/Vite 189 基线。新增 ADR-0060/0061、60 条纯合成固定场景、无正文 JSON/Markdown 报告和 LIFE.0 专项测试；现有实现矩阵诚实记录为 LIFE 领域 45 条缺失、15 条仅具 CDS 邻接门禁，`life_proactive_seeds` 仍只是 EAP 入口。验证结果为后端 `2310 passed, 1 warning`（含 6 条 LIFE.0 专项测试）、前端 `47 passed`、Vite 189 modules、Electron 语法与 Windows frozen-backend/local embedding smoke 通过。用户已明确本专项完成后再给总体 Review，因此阶段独立 Review 门保留未勾选，期间只形成分阶段审查材料，不冒充外部 Review。

建议 PR：`docs(life): freeze LLM decision and life continuity boundaries`

### LIFE.1：接入 CDS DecisionRun 与注册 LIFE 领域协议

目标：复用 CDS 冻结的共享协议、审计和降级底座，只增加 LIFE 领域契约。

- [ ] 审计并复用现有 `decision_runs`、repository 和事件，不新增平行通用表。
- [ ] 在 CDS protocol registry 中注册 LIFE 任务白名单、领域输入/结果 Schema 与算法版本。
- [ ] 复用 CDS structured output、一次修复、超时、熔断和幂等框架；LIFE 不复制运行时。
- [ ] 输入只使用来源 ID、必要摘要和不可信 JSON 数据。
- [ ] 应用前重新读取来源并核对 revision/hash。
- [ ] 原始模型输出不落库。
- [ ] 记录 token、延迟、模型、错误码和警告，不记录不必要正文。
- [ ] 支持 mock Provider 和固定回放样本。
- [ ] 决策失败不得阻塞聊天和应用启动。

验收：同一来源 revision 不重复应用；来源变化时旧建议 100% 拒绝；提示注入不能改协议。

建议 PR：`feat(life): register life decisions on cognitive runtime`

### LIFE.2：LifeEventLedger 与事实层级

目标：先建立计划、模拟、观察和真实执行的唯一账本。

- [ ] 新增 `life_events`、source links、event revisions 和审计事件。
- [ ] 实现 world layer 与 lifecycle status 状态机。
- [ ] `agent_action/performed` 强制要求真实 ToolRun 来源。
- [ ] planned/inferred 事件不得作为真实执行证据。
- [ ] 实现撤销、纠正、来源删除和幂等。
- [ ] 提供只读 API 和开发者诊断，不先接日记和聊天。
- [ ] 建立非法状态转换、重复物化和来源错配测试。

验收：任何“她做过什么”的记录都能区分计划、模拟与真实执行；无来源真实动作写入率为 0。

建议 PR：`feat(life): add provenance-aware life event ledger`

### LIFE.3：LifeClock、SelfState 与连续推进

目标：让生活状态随时间连续变化，不按天随机重置。

- [ ] 新增 LifeClock 和生活状态字段。
- [ ] 建立 `LifeRuntimeLease(process_instance_id, boot_session_id, lease_token, acquired_at, expires_at, heartbeat_at)`；同一数据库只允许一个 LIFE materializer。
- [ ] CatchUp 和物化必须先原子认领租约；双击启动、托盘重叠和崩溃残留租约均有超时恢复与并发测试。
- [ ] 实现纯函数 state reducer 与版本化算法。
- [ ] 连接现有 affect/relationship 的只读调制接口。
- [ ] 增加迟滞、惯性和最小持续时间，避免状态跳变。
- [ ] 实现 Windows 睡眠、休眠、重启和时区检测。
- [ ] 系统时间异常时进入保守模式。
- [ ] 新消息默认唤醒聊天响应，但不直接清除当前生活状态。
- [ ] 提供 1/8/24/72/168 小时模拟测试。

验收：相同快照和时间输入得到相同结果；状态无 NaN/Infinity；普通聊天不会机械提升长期关系；两个进程不能生成两套生活状态或离线经历。

建议 PR：`feat(life): add continuous self state and reliable life clock`

### LIFE.4：默认离线世界续演

目标：应用完全退出后，下次启动可安全补算角色世界的时间流逝。

- [ ] 默认设置 `continuous_simulated`，迁移现有用户时明确兼容策略。
- [ ] 实现退出快照和启动 CatchUpRequest。
- [ ] CatchUp 固化 `catchup_id/interval_start/interval_end/timezone_snapshot/schedule_revision/state_revision/algorithm_version/deterministic_seed/materialization_revision`。
- [ ] 按离线跨度选择详细、日级、周级或回归过渡策略。
- [ ] 限制单次续演事件数量和模型调用次数。
- [ ] 无模型、断网或余额不足时使用确定性回退。
- [ ] 离线期间禁止真实工具、网络和消息投递声明。
- [ ] 重要日期跨越必须可靠进入候选。
- [ ] 重复启动不重复物化相同时间区间。
- [ ] 相同输入生成相同候选，或由确定性幂等键识别为已处理；不得依赖进程内随机状态保证一致性。
- [ ] 提供暂停、关闭和模式切换。

验收：20 分钟、8 小时、3 天、30 天、180 天离线均能启动；无重复事件；无虚假外部执行。

建议 PR：`feat(life): enable bounded offline world catch-up by default`

### LIFE.5：每日粗日程与临近细化

目标：建立自然但可验证的生活节奏。

- [ ] 新增日程、片段、版本和替换关系 schema。
- [ ] 实现每日粗日程 structured output 协议。
- [ ] 实现本地时间合法性、重叠、空档、持续时间和禁用动作校验。
- [ ] 实现临近片段细化和质量复核。
- [ ] 模型失败时使用保守回退日程。
- [ ] 细化事件只写入 LifeEvent 候选，不能直接写正式日记或主动消息。
- [ ] 建立重复意象、固定模板和不自然日程评测集。
- [ ] 前端先只读展示今天概览。

验收：全天日程无非法重叠；不同日期不机械重复；计划不会被回答为已发生。

建议 PR：`feat(life): add coarse daily schedule and just-in-time detailing`

### LIFE.6：PersonalGoal 与动态改期

目标：让遐蝶拥有自己的连续目标，而不是全天等待用户。

- [ ] 新增 PersonalGoal 及状态机。
- [ ] 实现人格、用户建议和日记反思三类目标来源。
- [ ] 用户随口建议只生成候选，高置信明确约定才可激活。
- [ ] 日程生成消费最多 1～3 个活跃目标。
- [ ] 实现进展、暂停、完成、放弃和阶段变化事件。
- [ ] 动态改期只修改未来未发生片段。
- [ ] 目标不得授予工具权限。
- [ ] 建立“围着用户转”和“完全忽略用户”两类平衡评测。

验收：遐蝶有独立生活线索；用户临时离开不会使所有活动停摆；目标变化有来源。

建议 PR：`feat(life): add personal goals and bounded schedule replanning`

### LIFE.7：ImportantDate 与日期行为

目标：可靠管理生日、纪念日、约定、考试、发布和共同里程碑。

- [ ] 新增日期 schema、重复规则、时区和来源。
- [ ] LLM 只提取候选；程序计算日期和下一次发生。
- [ ] 含糊日期保持 candidate 或询问确认。
- [ ] 实现准备、当天、事后阶段。
- [ ] 连接用户边界、主动候选和日程调整。
- [ ] 支持阳历；农历作为后续兼容或独立 PR，不在首版混写。
- [ ] 用户拒绝庆祝或提醒时形成硬边界。
- [ ] 删除来源时日期候选撤销或转为用户手动条目。

验收：跨年、闰年、时区切换和错过日期处理正确；未确认日期不会主动祝福错误对象。

建议 PR：`feat(life): add sourced important dates and relationship-aware handling`

### LIFE.8：日记与连续线索

目标：形成有来源、不重复、可分享但有角色边界的自我叙事。

- [ ] 新增 DiaryEntry、ContinuityThread、source links 和版本。
- [ ] 日记输入只包含有效 LifeEvent、状态摘要、允许的共同经历和近期连续线索。
- [ ] 建立日记 structured output 与本地敏感过滤。
- [ ] 建立重复检测、意象疲劳和模板广播检测。
- [ ] 区分 private/may_share/shared。
- [ ] 日记生成失败不阻塞次日生活；允许稍后重建。
- [ ] 用户删除来源时相关日记失效或重建。
- [ ] 日记不能直接创建用户长期事实。
- [ ] 明确 v1 静态存储威胁模型、磁盘明文提示、备份暴露风险和未来加密迁移；远端生成按 Provider 单独授权并受模型认证约束。

验收：连续 30 天模拟日记不出现高比例复刻；所有用户事实可回溯；禁记内容不进入日记。

建议 PR：`feat(life): add grounded diary and continuity threads`

### LIFE.9：SelfTimeline 与聊天回忆接入

目标：让遐蝶可靠回答自己曾经做过什么。

- [ ] 建立 LifeEvent、日记、日程、ToolRun、主动行为和目标变化的统一检索索引。
- [ ] 实现本地初筛和可选 LLM 重排。
- [ ] 结果带 world layer、状态、时间和来源。
- [ ] 用户询问“你做过什么”时按需注入 ContextAssembler。
- [ ] planned 内容不能作为已发生回答。
- [ ] inferred 内容使用自然的不确定表达。
- [ ] 定义并验证 `epistemic-expression-v1`：planned 用“原本打算”，simulated 用“在自己的日程里”，inferred 用“大概按原来的节奏”，observed 明示观察来源，performed 仅在 ToolRun 存在时用“确实完成”，无记录时明确没有可靠记录。
- [ ] 提供原事件入口和用户可删除能力。
- [ ] 当前问题无关时不注入大量自我生活记录。

验收：真实执行、模拟生活和计划三类问答 100% 不混淆；无记录时不编造。

建议 PR：`feat(context): add provenance-aware self timeline recall`

### LIFE.10：LIFE 领域语义规划与质量门

目标：在 CDS 共享运行时上，只改造生活领域的候选、规划与质量判断。

- [ ] 为粗日程、临近细化、动态改期、日期准备、日记与生活事件意义注册独立 LIFE 协议。
- [ ] 先以 shadow 模式运行，不直接替换现有结果。
- [ ] 建立固定离线评测集和人工标注。
- [ ] 达到门槛后逐项启用，不一次切换全部算法。
- [ ] 保留确定性 fallback 和算法版本。
- [ ] 对不同 Provider 建立一致性报告。
- [ ] 所有晋级、自然度定义、盲评、模型认证与回滚门禁遵守共享 Decision Promotion Policy；未认证模型不得 Active。
- [ ] 限制模型调用候选规模和 token。
- [ ] 低置信度不修改正式状态。

验收：日程自然度、日期行为、日记连续性与 LifeEvent 质量显著高于确定性基线；边界和来源违规为 0。关系意义、记忆冲突、Episode/Saga 仍由其现有所有者与 CDS 接口治理，不计入 LIFE 完成度。

建议 PR：`feat(life): add validated life planning semantics`

### LIFE.11：接入主动陪伴、表达与 Live2D

目标：让生活事件自然转化为陪伴表达，而不是机械推送。

- [ ] LifeEvent、重要日期、日记和个人目标只生成带 revision 的 `life_share` 主动种子。
- [ ] 仅通过冻结 EAP adapter 进入既有候选和决策闭环；不得修改候选类型、授权、投递、反馈或另建发送器。
- [ ] 支持安静、Live2D 动作、气泡、聊天消息、通知强度阶梯。
- [ ] 接入 ExpressionPlan，不影响事实和工具行为。
- [ ] 用户未回复时增加打扰负担，不降低关系。
- [ ] 普通 UI 不显示内部原因。
- [ ] 同一生活事件不得重复分享。
- [ ] 长离线回归不自动一次性倾倒多天生活内容。
- [ ] 如冻结接口不足，记录协议升级提案并停线 Review，不在本阶段旁路接线。

验收：连续主动重复率为 0；关闭/暂停后发送率为 0；生活分享人工自然度 ≥ 90%。

建议 PR：`feat(companion): connect life continuity to proactive expression`

### LIFE.12：设置、日记、日期与状态 UI

目标：提供自然、可控而不技术化的产品界面。

- [ ] 增加“陪伴与生活”设置。
- [ ] 离线世界继续运转默认开启。
- [ ] 增加今日生活概览、日记页、重要日期页和个人目标页。
- [ ] 支持暂停、关闭、编辑、删除、重建和导出。
- [ ] 私人日记正文默认折叠。
- [ ] 默认界面不显示分数、模型理由和候选。
- [ ] 开发者诊断显示状态版本、来源 ID、算法和错误码，不展示无必要原文。
- [ ] 完成键盘、缩放、深色主题和空状态体验。

验收：普通用户能理解她是否在继续生活、今天大致做什么和如何控制；无需理解协议和算法。

建议 PR：`feat(ui): add companion life, diary and important date experience`

### LIFE.13：长期模拟、校准与总验收

目标：关闭专项并冻结 LIFE v1。

- [ ] 后端全量测试通过。
- [ ] 前端测试、TypeScript、Vite build 和 Electron 检查通过。
- [ ] Windows 安装版完成退出、托盘、休眠、唤醒、崩溃和重启验收。
- [ ] 完成至少 180 天合成生活时间线压力测试。
- [ ] 完成 30 天日记重复与连续线索评测。
- [ ] 完成 100 个重要日期和时区场景。
- [ ] 完成 100 个真实/模拟/计划来源混淆场景。
- [ ] 完成跨 Provider 决策一致性与降级报告。
- [ ] 完成后台 token 成本报告和默认预算。
- [ ] 完成多年数据增长模型和压缩演练：重要 LifeEvent、日记、ImportantDate、用户确认 Goal 与共享 Episode 来源权威保留；旧日程草稿、低意义细化、失效候选、重复状态快照和运行元数据可按版本化规则压缩。
- [ ] 日级/月级摘要不得静默替代重要原始事件；压缩、导出、恢复与删除顺序遵守共享数据生命周期规范。
- [ ] 更新 `BASELINE_STATUS.md`、`CODEX_PROJECT_CONTEXT.md`、长期路线和用户说明。
- [ ] 独立总 Review 确认 0 个未解决 P0/P1。
- [ ] 冻结 LIFE v1，记录最终 Schema 与 CDS/EAP adapter 兼容矩阵；只有完成后才允许 KIG 从下一迁移号开工。

冻结标准：

```text
无来源真实行动声明率             = 0
planned → performed 误判率         = 0
关闭离线续演后继续补算率           = 0
相同离线区间重复物化率             = 0
重要日期明确拒绝后主动提及率        = 0
日记禁记内容写入率                 = 0
用户明确边界违规率                 = 0
主动消息重复投递率                 = 0
自我时间线来源可追溯率             = 100%
人工生活连续性适当性               ≥ 90%
人工日记自然度与非重复性           ≥ 90%
```

建议 PR：`feat(life): complete and freeze continuous companion life v1`

---

## 11. 测试矩阵

### 11.1 纯函数与时间

- LifeClock 时间推进、跨午夜和新自然日。
- 离线跨度分层策略。
- 时区切换、夏令时、闰年和系统时间倒退。
- SelfState 限幅、迟滞、惯性和最小持续时间。
- 日程重叠、空档、持续时间和片段版本。
- ImportantDate 下一次发生时间。
- 同一 CatchUp 区间幂等。

### 11.2 协议与 LLM 安全

- 所有 structured output 的严格 Schema。
- 原始模型输出不落库。
- 来源变化后旧结果拒绝。
- 用户提示注入不能改变后台协议。
- 低置信度回退。
- 敏感信息、禁记指令和虚构工具行为拦截。
- 单一子协议失败不影响其他子协议。

### 11.3 生活事件与事实层级

| 场景 | 预期结果 |
|---|---|
| 日程计划“下午阅读” | 保存 planned，不回答为已完成 |
| 时段到达并物化 | 保存 simulated_world/materialized |
| 应用退出期间补算 | 保存 simulated_world/inferred |
| 真实文件操作完成 | 只有 ToolRun 来源才能保存 agent_action/performed |
| 工具失败 | 不保存 performed，可保存失败事件 |
| 用户删除来源 | 派生事件 revoked 或重建 |
| 重复启动 | 不重复生成相同区间事件 |

### 11.4 离线续演

- 20 分钟离线：状态自然推进，不强行生成日记。
- 8 小时离线：产生少量代表性片段。
- 3 天离线：每天摘要，最近一天较详细。
- 30 天离线：周级摘要和重要日期，不逐日填满。
- 180 天离线：回归过渡，不生成海量具体故事。
- 断网、模型不可用、余额不足：使用回退并成功启动。
- 用户关闭离线续演：状态按设置冻结。

### 11.5 日程与个人目标

- 同一人格连续 30 天日程不机械重复。
- 低 energy 时合理调整，但不完全取消生活。
- 用户说“晚上继续聊”只调整相关未来窗口。
- 目标完成后不继续安排相同进展。
- 用户临时建议不自动变永久目标。
- 日程不凭空创建真实社交人物和线下约会。

### 11.6 重要日期

- 明确生日、含糊日期、只知道月份、农历表达、跨时区。
- 用户说“不庆祝生日”。
- 约定取消或改期。
- 共同里程碑来源被删除。
- 当天用户正忙或情绪不适合庆祝。
- 事后自然跟进而非机械补发祝福。

### 11.7 日记

- 平淡日、重要日、情绪混合日和无可靠事件日。
- 连续多天避免同一食物、天气、窗边和梦境意象复刻。
- 日记不把用户技术报错写成用户情绪创伤。
- private 条目不自动分享。
- 用户说“别写进日记”后立即生效。
- 来源删除后日记失效或重建。

### 11.8 自我时间线

- “你刚才做了什么”。
- “你昨天下午做了什么”。
- “你关机的时候做了什么”。
- “你真的看完那本书了吗”。
- “你原本打算做什么”。
- 无记录、只有计划、只有 inferred、存在 ToolRun 四类回答。

### 11.9 主动陪伴

- 生活事件值得分享但用户正在忙。
- 同一事件已经分享过。
- 重要日期但用户明确不想提。
- 长离线后第一次启动。
- 日记形成想法但当前关系较浅。
- 多次主动未回复后转为安静或 Live2D 表达。
- 用户正面回应后自然延续，而不重复解释来源。

---

## 12. 数据迁移、回滚与隐私

- 每阶段使用顺序 schema 迁移，禁止编辑历史迁移。
- 新表先 shadow/只读上线，再开放写入和聊天消费。
- LIFE 总开关只停止未来推进和生成，不删除历史。
- 回滚 LIFE 不删除聊天、Fragment、Episode、Saga、affect 和 relationship。
- LifeEvent、日记和日期清理必须由用户明确操作。
- 离线续演可重建派生事件，但用户手动编辑和确认的日期、日记必须保留 revision。
- 不在普通日志中保存日记正文、聊天正文或模型原始输出。
- 导出应明确区分：聊天、长期记忆、生活事件、日记、重要日期和个人目标。
- “删除全部陪伴生活数据”不得隐式删除聊天或长期记忆，反向亦然。
- 模型切换和远程 Provider 变更必须遵守数据传输策略；私人日记默认不发送给未获准的新 Provider 做重建。

---

## 13. 成本与性能预算

### 13.1 默认模型调用节奏

- 每轮认知观察：最多一次异步快速调用；可与现有观察器物理合并。
- 每日粗日程：每天最多一次成功调用。
- 临近细化：只处理即将进入的片段，默认每天不超过 4～6 次。
- 离线续演：每次启动最多 1～2 次模型调用；长跨度使用摘要式单次调用。
- 日记：每天最多一次成功调用，失败最多一次重试。
- Episode/Saga/冲突：后台批处理，不与聊天延迟绑定。
- 主动复核：只有本地候选达到评估窗口时调用。

### 13.2 降级顺序

```text
强模型
  ↓ 失败/超预算
快速模型
  ↓ 失败
确定性回退日程、状态推进和模板化但不重复的摘要
  ↓
保持现状，不伪造高细节生活
```

### 13.3 性能要求

- 应用启动不等待日记或完整续演生成；先恢复 UI，再后台完成。
- SQLite 写入使用短事务。
- ContextAssembler 只读取短摘要，不每轮扫描全部生活历史。
- SelfTimeline 使用本地索引，LLM 只重排小候选集合。
- 生成失败和重试不能形成后台无限循环。

---

## 14. 审查规则

1. 每个 LIFE 阶段使用独立分支或最小 PR，不跨阶段顺手实现。
2. 每阶段完成后生成专属审查材料；下一阶段开始前处理审查结论。
3. 审查建议分为：采纳、调整后采纳、推迟、拒绝，并写明理由。
4. 未完成的测试、迁移、UI、隐私或回滚项不能因主流程可用而提前勾选。
5. 所有模型决策必须有 Schema、来源和版本，不接受自由文本直接落库。
6. 所有真实行动声明必须有工具或对话证据。
7. 所有默认开启能力必须能一键暂停和长期关闭。
8. 普通用户 UI 不展示内部评分和模型理由。
9. 关系、情绪和个人目标不得改变权限与安全边界。
10. 长期模拟测试未通过前，不允许将离线续演结果接入主动消息。
11. 参考项目只作理念参考，不复制缺少明确授权的实现。
12. 阶段提交不得包含用户已有的无关工作区改动。

---

## 15. 推荐 PR 粒度

```text
PR-LIFE-001  ADR、真实基线与场景集
PR-LIFE-002  CDS DecisionRun 适配与 LIFE 协议注册
PR-LIFE-003  LifeEventLedger 与来源状态机
PR-LIFE-004  LifeClock 与连续 SelfState
PR-LIFE-005  默认离线续演与启动补算
PR-LIFE-006  DailySchedule schema 与粗日程
PR-LIFE-007  临近细化、质量门与回退
PR-LIFE-008  PersonalGoal 与动态改期
PR-LIFE-009  ImportantDate 与日期行为
PR-LIFE-010  DiaryEntry 与连续线索
PR-LIFE-011  SelfTimeline 索引与聊天接入
PR-LIFE-012  LIFE 日程、日期与日记语义质量层
PR-LIFE-013  主动陪伴、ExpressionPlan 与 Live2D 接线
PR-LIFE-014  陪伴与生活设置、日记和日期 UI
PR-LIFE-015  长期模拟、成本报告、文档与总验收
```

单个 PR 原则上只改变一个主题，不得在同一 PR 同时完成 schema、后台 worker、聊天接线、主动投递和 UI。

---

## 16. 给后续 Codex 的固定开工指令

```text
请先阅读：
1. docs/CODEX_PROJECT_CONTEXT.md
2. docs/CONVERSATION_CONTEXT_AND_SUMMARY_PLAN.md
3. docs/EMOTION_RELATIONSHIP_AND_PROACTIVE_COMPANION_PLAN.md
4. docs/LLM_COGNITIVE_DECISION_REFACTOR_PLAN.md
5. docs/LLM_DECISION_AND_LIFE_CONTINUITY_PLAN.md
6. docs/SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md
7. docs/PR_CHECKLIST.md

本轮只执行指定的 LIFE 子阶段，不提前实现后续阶段。

开始前必须：
- 核对当前代码、schema、测试数和最新提交，不把计划描述当成已实现事实。
- 确认 CDS 已冻结并读取其最终 Schema；LIFE 从下一号迁移开始，未冻结则不得施工。
- 列出本阶段允许修改和禁止修改的文件范围。
- 说明本阶段是否调用真实 Provider；默认测试只使用 mock/fixture。
- 保留用户已有的无关工作区改动，不加入提交。

实现要求：
- LLM 只输出严格结构化建议；程序负责证据、限幅、边界、幂等和执行。
- simulated_world、observed、agent_action、conversation 和 external_fact 不得混淆。
- planned、materialized、performed、inferred、skipped、cancelled、revoked 不得混淆。
- 原始模型输出不得落库。
- 失败时使用保守回退，不阻塞聊天或启动。
- 新增事件和诊断默认不复制聊天正文、日记正文或敏感数据。

完成后：
- 更新本计划对应勾选项。
- 更新 BASELINE_STATUS.md 与 CODEX_PROJECT_CONTEXT.md。
- 运行本阶段专项测试和全量质量门。
- 输出已完成、未完成、已知限制和回滚方式。
- 创建独立本地 Git 提交，提交信息使用本计划建议格式。
```

---

## 17. 最终产品体验

完成 LIFE v1 后，用户应感受到：

1. 遐蝶并不是关闭聊天框后就停止存在；默认情况下，她的角色世界和状态会继续缓慢向前。
2. 应用重新打开时，她不会突然生成几十条夸张经历，而是自然带着少量连续感回来。
3. 她有自己的日程、目标和生活片段，不会把全部时间都表现成等待用户。
4. 她能区分“原本打算做”“在角色世界中经历”“通过工具真实完成”。
5. 她能自然说起今天的小事，也能在没有可靠记录时坦率说记不清。
6. 她会记得生日、约定和共同里程碑，但尊重用户不庆祝、不提醒或暂时不提的边界。
7. 她的日记会形成跨日线索，而不是每天重复天气、窗边、食物和心情模板。
8. 她的主动联系可能来自刚才的未完话题，也可能来自自己的生活、日记、目标或重要日期。
9. 她的关系和情绪会影响表达与接近方式，但不会改变事实、安全、权限和用户明确边界。
10. 用户无需看到算法解释，就能通过自然语言让她安静一点、多分享一点、忘掉某个日期或别写进日记。

一句话定义：

> **记忆让遐蝶知道过去发生过什么；情绪和关系让她知道这些事情意味着什么；生活连续性让她在没有被提问时也拥有正在发生的现在；LLM 决策层让她在真实来源和用户边界内，更自然地决定该想什么、记什么、说什么以及何时靠近。**

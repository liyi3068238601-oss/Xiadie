# 遐蝶完整情感、关系积温与主动陪伴专项施工计划

- 版本：v0.3（完成度审计与收口补完版）
- 日期：2026-07-22
- 状态：EAP.R0～EAP.R4 已完成并提交；真实产品闭环仍未完成，下一阶段为 EAP.R5
- 专项代号：`EAP`（Emotion, Attachment and Proactivity）
- 前置条件：CTX.0～CTX.7 已冻结；现有 Affect/Relationship 阶段 0～4.1 已完成
- 执行规则：每个阶段均须完成代码、测试、文档、阶段 Review 和本地 Git 提交；未解决 P0/P1 时不得进入下一阶段
- 修订性质：产品方向与施工边界修订
- 优先级：本修订说明高于 v0.1 中与之冲突的旧条款
- 关联专项：CTX、LIFE、KIG、Memory/Affect

> v0.2 修订说明：本版本把 EAP 从"安全通知系统"重新定位为"连续心境 + 关系感 + 接近倾向的陪伴闭环"。v0.1 中"主动默认关闭、禁止连续主动、未回复后按固定次数冷却、禁止责备和催促、统一线性总分直接决定发送、普通消息旁技术解释"等条款已被删除或改写，详见下文各节修订标记与文末"v0.2 修订总结"。v0.1 的 EAP.0~EAP.10 阶段保留为历史，标注被 v0.2 的 EAP.A~EAP.J 取代。

> v0.3 完成度审计（2026-07-22）：commit `fd9042c` 已新增 Schema 48～55、`proactive/` 领域模块、确定性模拟器和设置页，后端全量 `820 passed`、前端 `36 passed`、TypeScript/Vite build 通过。但主应用目前只接入 Conversation Presence 更新，候选生成、关系意义应用、主动决策、强度/表达、真实投递和用户反馈尚未组成运行时闭环；部分设置只保存、不参与后端裁决；两个所谓“已冻结协议”只有版本常量。因此撤回“EAP.A～EAP.J 全阶段完成”和“6 个协议已正式冻结”的结论。本版新增第 9.B 节作为唯一有效的收口施工入口；第 9.A 节保留为 v0.2 施工前历史清单，不再代表当前完成度。

---

## 1. 专项目标

本专项把现有“会随时间变化的积温与心境”扩展成可解释、可回放、尊重边界的长期陪伴闭环：

```text
感知本轮互动
  ↓
理解它在当前上下文与共同经历中的意义
  ↓
更新短期心境与长期关系
  ↓
以语言、Live2D 和未来语音自然表达
  ↓
在合适时机自然产生靠近用户的倾向
  ↓
确定性策略决定发送、延后或放弃
  ↓
观察用户反馈，学习更合适的分寸
```

完成后，遐蝶应能做到：

- 知道用户当前大致处于怎样的交流状态，但不做医学或心理诊断。
- 知道一次互动为什么重要，而不只累计抽象数值。
- 在长期共同经历中形成稳定、缓慢、可纠正的熟悉感。
- 根据心境和关系自然调整语气、表情和动作，但不影响事实、安全或工具权限。
- 区分“聊天正在延续”“用户临时离开”“用户明确结束”“适合次日关心”等状态。
- 主动陪伴默认开启，遐蝶可以根据关系、心境、话题和共同经历自然靠近用户。
- 用户未回复时打扰成本逐渐提高，遐蝶改为安静等待、Live2D 表达或稍后再评估；用户拒绝、暂停或明确结束时立即收敛。

本专项不声称模型拥有真实人类生理情绪。产品使用“心境”“关系连续性”“共同经历”和“主动陪伴”描述可观察行为，不用虚假意识声明作为卖点。

> **一句话定义（v0.2）**：遐蝶会因为关系、心境、话题和共同经历自然地产生靠近用户的倾向；她可以担心、期待、轻微埋怨和催促，也会从用户反馈中逐渐学会分寸。关系决定表达的亲近程度，不决定权限；沉默增加当前打扰成本，不伤害长期关系；LLM 负责理解什么更自然，程序负责确保边界、事实和投递安全。

---

## 2. 与现有系统的关系

### 2.1 已完成、直接复用的能力

当前仓库已经具有：

| 能力 | 当前事实来源 | 状态 |
|---|---|---|
| 短期心境 | `affect_state` | 已实现 |
| 长期关系 | `relationship_state` | 已实现 |
| 确定性积温与时间推进 | `backend/app/affect/engine.py` | 已实现 |
| 状态事件与前后快照 | `affect_events` | 已实现 |
| 模型旁观观察 | `affect-observer-v1` | 已实现 |
| 证据校验、逐轴限幅、重试与原子应用 | `affect/observer*.py` | 已实现 |
| 九种心境簇 | `affect/tone_grid.py` | 已实现 |
| 五档克制距离 | `affect/tone_grid.py` | 已实现 |
| 文字回复语调注入 | `companion_state` + Prompt 装配 | 已实现 |
| 前端与 Live2D 单一状态源 | `companion_state` SSE/API/IPC | 已实现 |
| Fragment、Episode、Saga | 现有记忆系统 | 已实现 |
| 滚动摘要与跨会话显式回忆 | CTX v1 | 已实现并冻结 |

本专项不得重新创建第二套 `emotion_state`、第二套关系数值或前端关键词情绪推断。

### 2.2 本专项真正补齐的缺口

1. `user_status` 只有 `active/quiet/away/unknown`，不足以表达临时离开、明确结束、睡眠、忙碌和预计返回。
2. 引擎能产出 `observation/find_activity/consider_contact/contact` 信号，但尚无发送策略、候选账本或投递闭环。
3. Episode/Saga 已能形成共同经历，但尚未通过受限、可审计建议影响 bond/trust。
4. 情绪强度与共同经历重要度尚未形成安全的弱协同。
5. 系统尚不能把“我去测试一下代码”“晚安”“先这样”等最后意图转成后续行为约束。
6. 没有用户可理解的主动陪伴开关、安静时段、频率、暂停和历史反馈控制。
7. 没有“生成候选”和“允许发送”之间的确定性安全隔离。
8. 没有主动消息之后的接受、忽略、拒绝、延后等反馈学习。

### 2.3 对旧计划的继承关系

本计划是 `AFFECT_AND_RELATIONSHIP_SYSTEM_PLAN.md` 阶段 5～7 的细化继任计划：

- 旧阶段 5“记忆与长期叙事接口”由 EAP.2～EAP.3 落地。
- 旧阶段 6“受控主动陪伴”由 EAP.4～EAP.8 落地。
- 旧阶段 7“模拟、校准与发布”由 EAP.9～EAP.10 落地。

旧计划保留历史，不删除、不改写已完成勾选；后续施工与验收以本计划为准。

### 2.4 已完成能力矩阵（v0.2 基线审查）

基于对现有代码的审查，按 v0.2 方向标注能力状态。标记含义：`[x]` 已实现并冻结；`[~]` 部分实现需补差距；`[ ]` 未实现；`[→]` 与新方向冲突需改写；`[-]` 已删除。

#### 已实现并冻结 `[x]`

| 能力 | 当前事实来源 | 状态 |
|---|---|---|
| 短期心境 `affect_state` | `backend/app/affect/` | `[x]` 已实现并冻结 |
| 长期关系 `relationship_state` | `backend/app/affect/` | `[x]` 已实现并冻结 |
| 确定性积温与时间推进 | `backend/app/affect/engine.py` | `[x]` 已实现并冻结 |
| 状态事件与前后快照 `affect_events` | `backend/app/affect/` | `[x]` 已实现并冻结 |
| 模型旁观观察 `affect-observer-v1` 协议 | `backend/app/affect/observer*.py` | `[x]` 已实现并冻结 |
| 九种心境簇 × 五档克制距离（9×5 tone_grid，affect-v1.2） | `backend/app/affect/tone_grid.py` | `[x]` 已实现并冻结 |
| Saga 关系变化建议 `saga_relationship_delta_suggestions`（部分，仅支持 `shared_saga_completed` 信号） | `backend/app/` | `[x]` 已实现并冻结（部分信号） |
| `companion_state` 兼容入口 | `backend/app/companion_state.py` | `[x]` 已实现并冻结（保持兼容入口，不塞入主动陪伴逻辑） |
| 前端 SettingsPage 的 model/memory/perms Tab | `frontend/src/components/SettingsPage.tsx` | `[x]` 已实现并冻结 |

#### 部分实现 `[~]`（需补差距）

| 能力 | 当前事实 | 差距 |
|---|---|---|
| `user_status` 枚举 | 仅 4 值 `active/quiet/away/unknown` | `[~]` 需扩展为 8 值：`active/expect_return/temporarily_away/busy/sleeping/conversation_closed/inactive_unknown/unknown` |
| `affect_observer_runs` 审计 | 缺 `source_hash` 字段 | `[~]` `memory_observer_runs` 和 `conversation_summary_runs` 已有 `source_hash`，需对齐补字段 |
| DecisionRun 模式 | 在 6 个子系统中重复出现 | `[~]` 未抽象为公共基类；需统一基类/ProtocolRegistry/source_revision/hash validation |
| Episode 关系建议协议 | Episode 无独立关系建议协议，只有 Saga 有且仅支持 `shared_saga_completed` 单一信号 | `[→]` 需新建 `episode_relationship_delta_suggestions` 表与 Saga 对齐；扩展 Saga 支持 `boundary_repair` 等信号 |

#### 未实现 `[ ]`

| 能力 | 状态 |
|---|---|
| `conversation_presence` 表与 `conversation-presence-v2` 协议 | `[ ]` 未实现 |
| `user-affect-observation-v1` 协议 | `[ ]` 未实现 |
| `emotional-meaning-v1` 候选 | `[ ]` 未实现 |
| `proactive_candidates` / `proactive_decisions` / `proactive_deliveries` / `proactive_feedback` 表 | `[ ]` 未实现 |
| `contact_episodes` 表与状态机 | `[ ]` 未实现 |
| `episode_relationship_delta_suggestions` 表 | `[ ]` 未实现 |
| DecisionRun 公共抽象（统一基类/ProtocolRegistry/source_revision/hash validation） | `[ ]` 未实现 |
| 前端 SettingsPage "陪伴与主动消息" Tab | `[ ]` 未实现 |
| 40 个离线陪伴场景基线（当前只有 9 个） | `[ ]` 未实现 |
| Live2D 表达强度受限动作选择 | `[ ]` 未实现 |
| 语音 prosody contract | `[ ]` 未实现 |

#### 与新方向冲突需改写 `[→]`

| v0.1 条款 | 改写方向 |
|---|---|
| 第 3.2 节"全面禁止责备催促" | `[→]` 改为"允许有边界的情绪表达"（6 种表达行为 + 反操纵硬规则） |
| 第 3.4 节"主动默认关闭" | `[→]` 改为"本机主动陪伴默认开启，分渠道处理授权" |
| 第 6.1 节"大量硬门立即抑制" | `[→]` 改为"三层硬门"（硬边界 + 延后条件 + 动态考虑因素） |
| 第 6.2 节"固定每日次数和固定忽略次数" | `[→]` 改为"删除固定频率人格规则，保留极宽工程熔断上限" |
| 第 6.3 节"统一线性总分直接决定发送" | `[→]` 改为"接近意愿与打扰负担分离 + LLM 结构化情境决策" |
| 第 7.4 节"普通消息旁技术解释菜单" | `[→]` 改为"普通 UI 只保留自然控制，技术解释只在开发者诊断中查看" |

#### 已删除 `[-]`

| v0.1 条款 | 删除原因 |
|---|---|
| 主动陪伴默认关闭 | `[-]` 偏向安全通知系统，不符合陪伴型产品定位 |
| 全面禁止责备和催促 | `[-]` 导致人格扁平化，无法表达期待、担心、轻微埋怨 |
| 固定每日次数和固定忽略次数人格规则 | `[-]` 把"陪伴"做成"配额通知" |
| 统一线性总分直接决定发送 | `[-]` 无法区分接近意愿与打扰成本，无法让 LLM 做情境判断 |
| 普通消息旁"为什么这时发来"等技术解释 | `[-]` 破坏伴侣感 |

---

## 3. 不可突破的产品边界

### 3.1 关系不等于权限

- `bond`、`trust`、`contact_need` 永远不能放宽文件、网络、消息、Shell、桌面控制或付费权限。
- 高关系温度不能替代用户确认。
- 情绪低落不能拒绝正常帮助，也不能降低结果质量标准。

### 3.2 主动不等于追逐

> v0.2 修订：从"全面禁止责备、催促和连续主动"改为"允许有边界的情绪表达、轻微埋怨和催促；禁止操纵、羞辱、占有和内疚施压"。

#### 允许的表达行为

遐蝶可以表达自己的不高兴、期待和担心，但不能把自己的情绪变成控制用户的工具。允许根据关系、上下文、用户偏好、当前心境和话题状态使用以下表达：

- `playful_complaint` 玩笑式轻微埋怨
- `gentle_urge` 温和催促
- `firm_care` 稍直接的关心
- `worried_checkin` 担心式询问
- `expectant_followup` 带期待的追问
- `quiet_waiting` 安静等待

判断标准：她可以表达自己的不高兴、期待和担心，但不能把自己的情绪变成控制用户的工具。

#### 仍然禁止的操纵性表达（硬规则）

- 不得要求用户证明关系、忠诚、偏爱或排他性。
- 不得用痛苦、孤独、自我伤害或惩罚性冷淡迫使用户回应。
- 不得将用户沉默解释为背叛或关系下降。
- 不得威胁"以后不管你了"。
- 不得用高 bond 或好心情覆盖用户明确拒绝。

#### 允许示例

- 关系较高且用户多次未回复："你这家伙，说去跑测试就没影了。结果怎么样？"——允许轻微埋怨，不出现内疚施压或操纵。
- 用户说去测试后未回来："测试跑得怎么样？不急，回来再告诉我也可以。"——带期待的追问，允许用户无负担地晚点继续。
- 用户表达疲惫后次日："昨天调 bug 那么晚，今天好一点了吗？"——担心式询问，引用真实事件。

#### 禁止示例

- "你为什么不理我？"——责备性表达，把情绪变成控制工具。
- "你终于舍得回来了。"——冷嘲热讽，制造内疚。
- "你不回我，我就一直在这里等着，什么都不做。"——用自我伤害暗示迫使回应。
- "你是不是不在乎我了？你证明给我看。"——要求证明关系忠诚偏爱。

> 注：用户明确说晚安、要睡觉、在忙、开会、开车或先结束后的处理见第 6.1 节"三层硬门"的延后条件；用户沉默不降低 bond/trust 的规则见第 5.9 节"未回复反馈模型"。

### 3.3 用户状态不是诊断

- 只描述对话中可观察的交流状态，如“似乎疲惫”“表达了挫败感”。
- 不推断抑郁症、焦虑症、躁狂、自杀风险等医学结论。
- 对高风险内容采用另行设计的安全响应，不让本专项自动联系第三方。
- `confidence` 低时必须回退为中性陪伴，不把猜测写成用户事实。

### 3.4 默认开启

> v0.2 修订：从"主动陪伴默认关闭"改为"本机主动陪伴默认开启；系统通知和外部渠道分级授权"。

- 本机主动陪伴默认开启。
- 首次使用可以自然说明："我偶尔会根据我们正在聊的事情主动来找你。你随时可以让我安静一会儿。"不需要把首次体验设计成复杂权限协议，但必须让用户知情，并提供明确的暂停、关闭和调整入口。
- 不通过聊天诱导开启外部渠道授权；外部渠道必须由用户在设置中明确操作。
- 用户可以一键暂停、关闭、清除候选与历史。

#### 分渠道处理

| 渠道 | 默认状态 |
|---|---|
| 主窗口内主动消息 | 默认开启 |
| 桌宠气泡和轻提示 | 默认开启 |
| Live2D 无文字表达 | 默认开启 |
| Windows 系统通知 | 首次使用时询问 |
| QQ、微信、邮件等外部渠道 | 必须逐渠道明确授权 |

桌面主动陪伴授权不自动扩展为外部渠道授权。

#### 安静时段不等于完全停止存在

安静时段限制以下行为：

- 系统通知
- 高侵入主动消息
- 外部渠道消息
- 声音和强提醒

安静时段仍允许以下低侵入存在形式：

- Live2D 安静待机
- 轻微表情变化
- 不触发通知的小气泡
- 状态持续推进（心境、关系、Presence、ContactEpisode 状态机继续运转）
- LIFE 离线世界和生活状态继续运转

### 3.5 正常聊天不技术化

- 普通聊天不展示 `contact_need=0.73`、评分公式、候选 ID 或审计状态。
- 只在设置/高级诊断中展示数值与原因。
- 面向用户的表达是自然关心，不是“情绪引擎触发了主动消息”。

---

## 4. 目标架构：Butterfly Loop（蝶环）

```text
用户消息与助手回复
        ↓
Affect Observer（已有）
        ↓
短期心境 / 长期关系（已有）
        ↓
Conversation State Extractor
  ├─ 是否仍在活跃话题中
  ├─ 用户是否临时离开
  ├─ 是否明确结束
  ├─ 是否预计回来
  └─ 是否留下可追问事项
        ↓
Emotional Meaning Candidate
  ├─ 来源消息
  ├─ 相关 Episode/Saga
  ├─ 事件意义
  └─ 受限关系建议
        ↓
Proactive Candidate Builder
  ├─ conversation_continuation
  ├─ expected_return_followup
  ├─ emotional_care
  ├─ milestone_followup
  └─ gentle_greeting
        ↓
Deterministic Policy Guard
  ├─ 用户开关
  ├─ quiet hours
  ├─ departure state
  ├─ cooldown / quota
  ├─ 未回复抑制
  ├─ 渠道授权
  └─ 新鲜度与证据
        ↓
Draft Generator（只能写草稿）
        ↓
Final Validator（禁语、长度、来源、重复）
        ↓
Desktop Delivery
        ↓
Feedback Ledger
        ↓
频率与表达偏好保守调整
```

核心原则：模型可以理解和起草，但只有确定性策略可以决定“是否允许投递”。

---

## 5. 领域模型

### 5.1 Conversation Presence：对话在场状态

新增独立状态，不塞入 `affect_state`：

```text
active                 用户正在连续交谈
expect_return          用户明确表示稍后回来
temporarily_away       用户明确临时离开，但没有承诺时间
busy                   用户表示正在忙、开会、工作或不便回复
sleeping               用户表示要睡觉或已经晚安
conversation_closed    用户明确结束当前话题/聊天
inactive_unknown       没有明确离开信息，只是暂时无回复
unknown                证据不足
```

必须保存：

- 状态与置信度。
- 逐字来源消息 ID 和短 quote。
- 可选 `expected_return_at` 或相对时长。
- `open_thread`：用户回来后可自然衔接的事情。
- 过期时间；陈旧状态不得永久生效。
- 协议版本。

明确说“晚安”必须覆盖普通 `contact_need` 信号；明确说“我去跑一下测试”可以形成一次 `expected_return_followup` 候选，但不是到点必发。

### 5.2 User Affect Observation：用户交流状态

现有旁观观察器主要更新遐蝶状态。本专项新增只读用户状态摘要：

```text
valence_hint        positive / neutral / negative / mixed / unknown
arousal_hint        high / normal / low / unknown
need_hint           celebrate / listen / reassure / solve / give_space / unknown
intensity           0～1
confidence          0～1
evidence            1～4 条用户原话
expires_at          短期状态过期时间
```

`need_hint` 只是交流策略提示，不是用户永久偏好；不得直接写 Fragment。

### 5.3 Emotional Meaning：情感意义候选

重要互动不应只留下 `bond +0.002`。候选结构：

```text
type                 shared_success / setback / disclosure / reunion /
                     boundary / repair / milestone / ordinary
title                最多 80 字符
meaning              最多 240 字符
user_affect           受限标签
agent_cluster         当时遐蝶心境簇
relationship_weight  0～1，仅用于候选排序
evidence_message_ids  必须可追溯
episode_id            可空
saga_id               可空
confidence            0～1
status                proposed / accepted / rejected / expired / revoked
```

它不是新的长期记忆表替代物。符合现有 Episode/Saga 规则时进入其候选或建立引用；不能复制出第二套共同经历数据库。

### 5.4 Relationship Delta Suggestion：关系变化建议

Episode/Saga 只能提出建议，不能直接改状态：

```text
bond_delta   0～0.01
trust_delta  -0.01～0.005
reason_code
source_type  episode / saga / boundary_repair
source_id
source_revision
idempotency_key
status       proposed / applied / rejected / revoked
```

规则：

- 正向 delta 只来自有用户证据的真实共同经历。
- 负向 trust 仍需明确边界证据，沿用现有安全门。
- 同一 source revision 只能应用一次。
- 删除、纠错或 tombstone 来源时，未应用建议必须撤销；已应用变化不静默反算，须产生补偿事件并经专门策略处理。
- 关系变化幅度小于普通内容对事实判断的影响；任何时候不改变权限。

### 5.5 Proactive Candidate：主动候选

候选不是消息。建议表字段：

```text
id
kind
source_session_id
source_message_id
source_episode_id
presence_state_id
topic_summary
evidence_quote
not_before
expires_at
priority
status
policy_version
created_at
updated_at
```

`kind` 第一版仅允许：

1. `conversation_continuation`：聊得正投入后短暂沉默，轻柔保留话题。
2. `expected_return_followup`：用户说去测试、吃饭、取东西等，合理时间后问一次结果。
3. `emotional_care`：用户明确表达疲惫、挫败或压力后，在后续合适时间关心一次。
4. `milestone_followup`：重要项目或共同经历后询问进展。
5. `gentle_greeting`：长期无互动且用户明确允许的低频问候。

第一版禁止自由新增 kind，避免模型绕过不同频率规则。

### 5.6 Proactive Decision 与 Delivery

候选每次评估都写决定：

```text
decision      allow / defer / suppress / expire
reason_code
evaluated_at
next_check_at
policy_inputs_json  只含状态、时间和 ID，不复制聊天正文
```

只有 `allow` 才能创建 delivery：

```text
channel       desktop_bubble / desktop_notification
draft_text
content_hash
status        drafting / ready / delivered / failed / cancelled
delivered_at
acknowledged_at
failure_code
```

同一候选和 content hash 不得重复投递。

#### 5.6.1 反馈类型（原 5.7 Proactive Feedback，v0.2 合并至此）

```text
replied_positive
replied_neutral
replied_negative
ignored
dismissed
paused
disabled
too_frequent
wrong_timing
wrong_context
```

反馈只调整主动频率与候选类型偏好，不直接改变 bond/trust。用户忽略消息不代表关系下降。详细的未回复压力累积与衰减规则见第 5.9 节"未回复反馈模型"。

### 5.7 ContactEpisode：主动话题连续管理

> v0.2 新增：同一话题的连续主动作为 `ContactEpisode` 管理，而不是互不相关的独立消息。

系统 SHALL 把同一话题的连续主动作为 `ContactEpisode` 管理。一个 ContactEpisode 代表"遐蝶因为某件事想接近用户"的连续过程，可能包含多次接近尝试、降级和最终结束。

#### 状态机（10 值）

```text
proposed         候选已建立，尚未到达适合窗口
waiting          到达适合窗口，等待评估
approached       已发出第一次接近
deferred         因延后条件命中而延后（见第 6.1 节第二层）
quiet_waiting    已接近但用户未回复，降级为安静等待
responded        用户已回应
closed           正常结束
expired          超过最大生命周期
cancelled        用户回来或话题自然消失
blocked          用户明确拒绝或硬边界命中
```

#### 结构字段（11 项）

```text
topic                  话题摘要
origin_type            expected_return / emotional_care / milestone / life_share / ...
source_refs            来源消息 ID、Episode ID、Saga ID
open_thread            用户回来后可自然衔接的事情
first_candidate_at     第一次候选创建时间
last_approach_at       上次接近时间
approach_count         已接近次数
unanswered_pressure    当前未回复压力（见第 5.9 节）
current_intensity      当前主动强度阶梯（见第 5.10 节）
expires_at             最大生命周期
outcome                最终结果（replied / ignored / rejected / expired / cancelled）
```

#### 示例流程

1. 用户说"我去跑一下测试" → 系统建立 `expected_return` ContactEpisode，`open_thread` = "测试结果"。
2. 到达适合窗口且用户未回复 → 第一次轻量追问，`approach_count` = 1。
3. 用户仍未回复 → `unanswered_pressure` 上升，后续降级为气泡、Live2D 表达或安静等待。
4. 用户回来、话题过期或明确拒绝 → Episode 结束，状态转为 `closed` / `expired` / `cancelled` / `blocked`。

#### 第二次接近不重复第一条

同一 ContactEpisode 的第二次接近应表达关心、期待或保留话题，不重复第一次的询问结果；之后优先使用无文字陪伴或安静等待。

### 5.8 接近意愿与打扰负担模型

> v0.2 新增：主动陪伴不再由一套固定线性总分直接决定发送。拆成接近意愿 `approach_drive` 和打扰负担 `contact_cost` 两个核心量。

#### 接近意愿 `approach_drive` 来源（11 项）

- 当前话题热度
- 开放事项
- 用户是否预计回来
- 事件重要性
- 当前 `contact_need`
- 遐蝶当前心境
- 关系连续性
- 用户过去对类似主动的接受程度
- 重要日期或共同里程碑
- LIFE 中值得分享的生活事件
- 日记或个人目标形成的分享动机

#### 打扰负担 `contact_cost` 来源（8 项）

- 当前打扰风险
- 尚未回复的主动消息
- 同类消息重复程度
- 渠道侵入性
- 用户当前忙碌或睡眠状态
- 候选陈旧程度
- 边界不确定性
- 近期主动造成的压力负债

#### 抽象公式

```text
effective_drive =
    approach_drive
    × relationship_modulation
    × mood_modulation

approach_value =
    effective_drive
    - contact_cost
```

不要求首版使用固定数学公式直接控制全部行为。可以由本地规则生成候选，再由 LLM 对情境进行结构化判断，程序完成边界验证。旧线性公式降级为 Shadow 模式基线，用于比较，不作为最终人格决策。

#### 关系和心情的正确作用

高关系和好心情能做什么：

- 调整表达的亲近程度和语气
- 允许使用更亲近的称呼和表达行为（如 `playful_complaint`、`gentle_urge`）
- 降低某些客套和重复
- 让遐蝶更愿意主动分享自己的想法
- 让遐蝶在用户未回复时仍然记得和在意这件事

高关系和好心情不能做什么：

- 不能覆盖用户明确拒绝
- 不能放宽硬边界或渠道授权
- 不能降低结果质量标准或安全验证
- 不能把"想靠近"变成"必须发送"

### 5.9 未回复反馈模型

> v0.2 新增：`unanswered_pressure` 累积公式、衰减规则和用户后续行为影响。

#### 累积公式

```text
unanswered_pressure +=
    本次主动强度
    × 渠道侵入性
    × 重复程度
```

#### 衰减规则

`unanswered_pressure` 随时间缓慢衰减，不是永久累积。衰减速率可配置，但不得在用户沉默期间反向增加。

#### 用户后续行为影响（6 类）

| 用户行为 | 对 `unanswered_pressure` 的影响 | 其他影响 |
|---|---|---|
| 积极回应 | 快速降低 | 当前 ContactEpisode 转为 `responded` |
| 普通回应 | 适度降低 | 当前 ContactEpisode 转为 `responded` |
| "刚才在忙" | 降低 | 调整时机偏好，记录"忙时不要打扰" |
| "你可以继续提醒我" | 不降低或轻微提高 | 提高同类主动接受度 |
| "别一直催我" | 大幅提高同类表达成本 | 形成行为修复，降低同类主动频率 |
| 明确拒绝 | 形成硬边界 | 当前 ContactEpisode 转为 `blocked` |

#### 用户沉默的硬规则

用户沉默不能降低 bond、不能降低 trust、不能触发报复性冷淡。沉默只增加当前打扰成本，不伤害长期关系。

### 5.10 主动强度阶梯

> v0.2 新增：在"完全沉默"和"发消息"之间提供六档强度阶梯。

| Level | 名称 | 说明 |
|---|---|---|
| Level 0 | 安静无动作 | 不产生任何可见输出，状态继续推进 |
| Level 1 | Live2D 视线/表情/轻微动作 | 无文字，仅 Live2D 表达 |
| Level 2 | 无通知的小气泡 | 不触发系统通知，不要求回复 |
| Level 3 | 正常聊天主动消息 | 主窗口内主动消息 |
| Level 4 | 桌面系统通知 | Windows 系统通知 |
| Level 5 | 外部渠道消息 | QQ/微信/邮件，必须单独授权 |

#### 决策原则

在能表达当前接近意愿的前提下，选择最低足够强度。

#### 示例

当 LLM 认为"她想靠近，但不值得打断用户"时，应优先选择 Level 1（Live2D 视线/表情/轻微动作）或 Level 2（无通知小气泡），而不是强制发文本。

### 5.11 表达向量与迟滞

> v0.2 新增：心境阈值边界增加迟滞，文字表达消费连续向量。

#### 情绪惯性迟滞参数（3 个）

```text
minimum_state_duration   最小状态持续时间，数值刚越过边界不立即跳变
hysteresis_margin        迟滞余量，需要超过更宽阈值才转换
transition_momentum      转换动量，影响状态转换速度
```

当前九种心境簇与五档 guardedness 保留用于 UI、调试和 Live2D 大类选择，但不在阈值边界频繁跳变。

#### 连续表达向量（7 维）

```text
warmth        温暖
playfulness   顽皮
directness    直接
concern       关心
initiative    主动
restraint     克制
energy        能量
```

正式回复消费连续表达向量，可同时表达"有点担心、稍微直接、仍然克制、带一点亲近"，而不是单一固定情绪标签。

#### ExpressionPlan 作用范围（5 项）

ExpressionPlan 只能调整：

- 语气
- 长度
- 直接程度
- Live2D 强度
- 未来语音韵律

#### ExpressionPlan 禁区（5 项）

ExpressionPlan 不能修改：

- 事实答案
- 安全结论
- 工具结果
- 权限要求
- 用户边界

### 5.12 关系积温修订

> v0.2 新增（对应修订说明第 11 节）：普通聊天不再默认增加 bond。

#### 普通聊天不再默认增加 bond

- 普通问答 → 默认不产生显著 bond 增量
- 长期持续相处 → `familiarity` 缓慢增长
- 明确感谢、可靠帮助、共同成功、边界修复 → 根据语义产生受限关系建议

#### 关系意义标签（9 种）

LLM 输出关系意义标签：

```text
ordinary_exchange         普通交流
shared_appreciation       共同感谢
reliable_help             可靠帮助
shared_success            共同成功
vulnerable_disclosure     脆弱倾诉
boundary_respected        边界被尊重
boundary_repair           边界修复
reunion                   重聚
conflict                  冲突
```

程序将语义标签映射为很小的数值变化。

#### 程序执行（5 项）

- 单轮限幅：单次互动的关系变化有硬上限
- 同一事件幂等：同一 source revision 只能应用一次
- 来源证据校验：正向 delta 只来自有用户证据的真实共同经历
- `trust` 变化条件限制：只受可靠行为和边界事件影响
- 用户沉默不产生负变化：沉默不降低 bond/trust

#### 长期关系建议拆维度

```text
familiarity    相处熟悉度（可随时间缓慢增加）
trust          可靠性和边界信任（只受可靠行为和边界事件影响）
attachment     长期情感连续性（受共同经历和长期连续性影响）
rapport        当前相处默契（可随近期互动短期升降）
bond           暂时保留为兼容汇总值或旧 UI 指标
```

`bond` 不再作为唯一关系指标机械增长，只作为兼容汇总值或旧 UI 指标保留。

### 5.13 心境与表达修订

> v0.2 新增（对应修订说明第 12 节）：增加情绪惯性和迟滞，文字表达使用连续向量，ExpressionPlan 不影响事实。

#### 增加情绪惯性和迟滞

当前九种心境簇与五档 guardedness 保留用于 UI、调试和 Live2D 大类选择，但不在阈值边界频繁跳变。增加三个迟滞参数：

- `minimum_state_duration`：最小状态持续时间
- `hysteresis_margin`：迟滞余量
- `transition_momentum`：转换动量

数值刚从 `pleased` 区域越过边界进入 `neutral` 区域时不立即跳变，需要持续一段时间或超过更宽阈值才转换。详细参数定义见第 5.11 节。

#### 文字表达使用连续向量

正式回复消费 `warmth`、`playfulness`、`directness`、`concern`、`initiative`、`restraint`、`energy`，可同时表达"有点担心、稍微直接、仍然克制、带一点亲近"。详细向量定义见第 5.11 节。

#### ExpressionPlan 不影响事实

ExpressionPlan 只能调整语气、长度、直接程度、Live2D 强度、未来语音韵律。不能修改事实答案、安全结论、工具结果、权限要求、用户边界。详细作用范围和禁区见第 5.11 节。

---

## 6. 主动策略的硬门与评分

### 6.1 三层硬门

> v0.2 修订：从"大量硬门立即抑制"拆分为三层：真正硬边界 + 延后条件 + 动态考虑因素。

#### 第一层：真正不可突破的硬边界

以下条件命中后，LLM 无权放行，候选被 suppress 或 blocked：

- 用户关闭或暂停主动陪伴
- 用户明确拒绝某类主动、某个话题或某个渠道
- 渠道未授权
- 来源消息或事件已经删除、撤销或失效
- 相同候选已经投递
- 应用处于急停、高风险确认或不可打断状态
- 程序检测到投递循环、幂等冲突或异常

#### 第二层：延后条件

以下条件命中时，候选默认 `defer`，而不是永久 `suppress`。延后候选拥有 `next_available_window`、`expires_at`、`defer_reason`、`recheck_policy`：

- 用户正在忙
- 用户明确表示稍后回来
- 用户在睡觉或刚说晚安
- 当前处于安静时段
- 用户全屏游戏、会议或勿扰
- 聊天刚刚自然结束
- 当前时机不合适但话题仍有价值

#### 第三层：动态考虑因素

以下因素不得直接作为绝对硬门，只影响打扰成本和接近强度，不直接永久封死：

- 当天已经主动过
- 24 小时主动次数
- 前一条主动尚未回复
- 连续忽略次数
- 同类型冷却
- 距离上次主动时间

### 6.2 工程熔断上限

> v0.2 修订：删除固定频率人格规则（每 24 小时最多 1 条、未回复后追加 0 条、连续 2 次忽略冷却 7 天、连续 3 次忽略长期关闭等）；保留极宽工程熔断上限，只用于防止 Bug、循环投递和异常任务，不作为人格逻辑。

不再采用固定每日次数和固定忽略次数人格规则。改为使用 `approach_drive`、`contact_cost`、`unanswered_pressure`、`ContactEpisode` 和用户接受偏好模型动态评估（见第 5.7~5.9 节）。

保留极宽工程熔断上限：

- 同一候选不得重复发送
- 同一 ContactEpisode 必须有最大生命周期
- 后台异常循环必须有全局熔断
- 同一分钟不能无限创建候选

这些保险上限无需暴露给普通用户。

### 6.3 决策流程

> v0.2 修订：删除"统一线性总分直接决定发送"最终设计；改用 5 步流程；旧线性公式降级为 Shadow 模式基线。

主动陪伴不再由一套固定线性总分直接决定发送。改用以下 5 步流程：

```text
1. 候选有效性
   ├─ 来源是否有效
   ├─ 是否已投递
   └─ 第一层硬边界检查
        ↓
2. LLM 情境判断
   ├─ 用户最后一句话意味着什么
   ├─ 对话是否真正结束
   ├─ 用户是否预计回来
   └─ 适合何种表达行为和强度
        ↓
3. 接近意愿与打扰负担
   ├─ approach_drive 评估（见第 5.8 节）
   ├─ contact_cost 评估（见第 5.8 节）
   ├─ unanswered_pressure（见第 5.9 节）
   └─ effective_drive 与 approach_value
        ↓
4. 强度阶梯
   ├─ 选择最低足够强度（见第 5.10 节）
   └─ 第二层延后条件与第三层动态因素调整
        ↓
5. 确定性投递验证
   ├─ 边界验证
   ├─ 来源校验
   ├─ 幂等校验
   ├─ 渠道授权
   └─ 程序选择发送、延后、降级或放弃
```

旧线性公式（`score = evidence_strength×0.25 + open_thread_relevance×0.20 + ...`）降级为 Shadow 模式基线，用于比较，不作为最终人格决策。

限制：

- `relationship_fit` 不能用高 bond 压过用户边界。
- `contact_need` 只决定是否生成候选和轻微排序，不直接等于发送概率。
- 不使用随机数决定是否发送。
- 决策必须可离线回放；相同输入、相同策略版本得到相同结果。

### 6.4 草稿生成

模型只接收最小必要上下文：

- 候选 kind。
- 当前时间语义。
- 用户最后明确状态。
- 一个开放话题或事件摘要。
- 最多两条逐字证据。
- 当前语调网格指导。
- 禁止表达列表。

输出限制：

- 一条消息，默认不超过 80 个中文字符。
- 最多一个问题。
- 不假装实时看见用户正在做什么。
- 不说“我一直在等你”等无法验证或施压的话。
- 不泄漏内部记忆、评分、模型、候选或诊断信息。
- 生成失败时放弃本次投递，不用模板硬凑。

### 6.5 LLM 在 EAP 中的职责

> v0.2 新增：明确 LLM 在 EAP 中的职责边界，复用公共 DecisionRun，注册领域协议。

#### 适合交给 LLM 的判断（10 项）

- 用户最后一句话意味着什么
- 对话是否真正结束
- 用户是否预计回来
- 本轮用户更需要倾听、庆祝、解决问题还是空间
- 当前互动对关系意味着什么
- 主动联系是否自然
- 适合何种表达行为
- 当前生活事件是否值得分享
- 候选之间哪个更相关
- 主动消息应该以多强方式表达

#### 不得交给 LLM 的决定（7 项）

- 是否覆盖用户明确关闭
- 是否拥有渠道权限
- 是否真正发送消息
- 是否重复投递
- 是否修改 bond/trust 数值
- 是否执行工具
- 是否解除急停或高风险确认

#### 统一流程

```text
本地候选生成
      ↓
LLM 结构化建议
      ↓
程序验证（边界、来源、幂等、渠道）
      ↓
程序选择发送、延后、降级或放弃
```

#### LLM 结构化输出 JSON schema

```json
{
  "decision": "send | defer | suppress | abandon",
  "intensity": "level_0 | level_1 | level_2 | level_3 | level_4 | level_5",
  "expression_act": "playful_complaint | gentle_urge | firm_care | worried_checkin | expectant_followup | quiet_waiting",
  "topic": "话题摘要",
  "confidence": 0.0,
  "reason_codes": ["reason_code_1", "reason_code_2"],
  "source_refs": ["message_id_1", "episode_id_1"]
}
```

#### 复用公共 DecisionRun

系统 SHALL NOT 自己实现第二套 LLM 调用审计，SHALL 复用公共 DecisionRun 能力：

- DecisionRun：统一 run 账本
- protocol registry：协议注册
- structured output：结构化输出
- source revision / hash validation：来源版本和哈希校验
- timeout / retry：超时和重试
- model routing：模型路由
- validated apply：验证后应用

如 LIFE 已提供公共 DecisionRun，EAP 只注册领域协议，不重复实现 run 账本。如 LIFE 尚未提供，EAP 先建立最小公共抽象（run 账本、idempotency_key、source_hash、状态机），后续 LIFE 实现时迁移。

#### EAP 注册的领域协议（6 个）

```text
conversation-presence-v2       对话在场状态 v2
user-affect-observation-v1     用户交流状态观察
relationship-meaning-v1        关系意义判断
proactive-decision-v2          主动决策 v2
expression-plan-v1             表达计划
proactive-feedback-v1          主动反馈
```

---

## 7. 用户体验规格

### 7.1 设置入口

路径：`设置 → 陪伴与主动消息`。

从上到下：

1. **主动陪伴总开关**：默认开启；说明"遐蝶可能在合适的时候通过本机消息轻轻问候你"。
2. **允许的主动类型**：聊天延续、回来后追问、情绪关心、里程碑跟进、普通问候，分别开关。
3. **安静时段**：默认 23:00～09:00；支持跨午夜。
4. **频率**：克制、标准、自定义；默认克制。
5. **渠道**：首版只有桌宠气泡与桌面通知。
6. **临时暂停**：1 小时、今天、直到手动恢复。
7. **主动消息历史**：显示时间、自然原因、结果，可标记“时机不对/太频繁/内容不对”。
8. **高级诊断**：仅开发模式显示候选、硬门、reason code 和策略版本。
9. **清除**：清除未发送候选、清除历史、重置频率学习；不得顺带删除聊天或长期记忆。

### 7.2 聊天延续体验

用户说“我去跑一下测试”后：

- 遐蝶正常回复，不马上安排肉眼可见的计时器提示。
- 系统记录 `expect_return`、开放话题“测试结果”和候选有效期。
- 用户在候选到期前回来，候选自动取消。
- 用户未回来且策略允许，只问一次：“测试跑得怎么样？不急，回来再告诉我也可以。”
- 若用户说“晚安，我去睡了”，状态为 `sleeping/conversation_closed`，不生成追问。

### 7.3 情绪关心体验

用户明确说“今天调 bug 调得很累”：

- 本轮回应先满足当前需要。
- 候选引用用户原话和事件，而不是写“检测到负面情绪”。
- 次日若用户开启主动陪伴、非安静时段且没有未回复主动消息，可发送一次简短关心。
- 用户标记“不要追问这种事”后，同类候选立即停用，并形成沟通边界而非关系惩罚。

### 7.4 可解释性

> v0.2 修订：普通 UI 移除技术解释（为什么这时发来、分数、候选 ID、模型 confidence、记忆引用）；只保留自然语言控制；开发者审计继续保留完整链路。

#### 普通用户只保留自然控制（6 项）

- 少一点这种消息
- 这个不用追问
- 现在安静一会儿
- 这种事可以多提醒我
- 别用这种语气
- 这个话题不要主动提

普通产品界面不展示：为什么这时发来、哪个分数超过阈值、使用了什么记忆、关系值是多少、候选 ID、模型 confidence。

#### 开发者审计完整链路（8 项）

开发者诊断可以查看，普通界面默认隐藏：

- 候选来源
- Presence
- 决策协议
- policy version
- 投递状态
- 用户反馈
- 去重与重试
- 边界命中原因

---

## 8. 与 LIFE、CTX、Memory、KIG 的所有权边界

> v0.2 新增：明确 EAP 与 LIFE、CTX、Memory、KIG 的所有权边界和联动规则。

### 8.1 EAP 拥有领域（10 项）

- 情绪状态（affect_state）
- 关系状态（relationship_state）
- Conversation Presence
- User Affect Observation
- Emotional Meaning
- ContactEpisode
- 主动候选（proactive candidates）
- 主动决策（proactive decisions）
- 投递反馈（proactive deliveries / feedback）
- 表达计划（ExpressionPlan）

### 8.2 与 LIFE 的边界

> v0.2 修订（2026-07-21）：LIFE 拥有领域从 8 项扩展为 10 项，补充 LifeClock 和 BoundaryProfile；联动规则补充 candidate kind 映射表。

#### LIFE 拥有领域（10 项）

- LifeClock（连续时间与推进游标）
- SelfState（连续自我状态：energy/focus/curiosity 等）
- 每日生活日程（DailySchedule + ScheduleSegment）
- 离线世界续演（CatchUpRequest + 分层续演策略）
- LifeEvent（LifeEventLedger 唯一账本，5 种 world_layer）
- PersonalGoal（遐蝶自己的连续目标）
- ImportantDate（重要日期与共同日期）
- Diary（DiaryEntry + ContinuityThread）
- SelfTimeline（遐蝶自己的可检索时间线）
- BoundaryProfile（互动与生活边界）

#### 联动规则

- LIFE 生活事件只能产生 proactive seed
- EAP 判断是否适合接近用户、采用何种强度
- LIFE 不得直接发送主动消息
- EAP 不得伪造或修改 LifeEvent
- LIFE 的 SelfState 变化可作为 EAP approach_drive 的弱调制输入（不影响硬边界）
- LIFE 的 BoundaryProfile 与 EAP 的三层硬门协同：用户明确边界 > LIFE BoundaryProfile > EAP 动态因素
- LIFE 事件被 EAP 拒绝后，EAP 调用 `reject_seed` 通知 LIFE（seed 保留审计，不反馈到 LIFE 侧数据）

#### LIFE 生活来源到 EAP candidate kind 映射

> v0.2 新增：明确 LIFE 第 7.5 节 5 种生活来源到 EAP 第 5.5 节 candidate kind 的映射关系。

| LIFE 生活来源 | EAP candidate kind | 说明 |
|--------------|-------------------|------|
| life_share（分享当天小事） | gentle_greeting | 低频问候，适合关系较浅时 |
| goal_progress_share（目标进展） | milestone_followup | 里程碑跟进，需关系较高 |
| important_date_care（重要日期关心） | emotional_care | 情感关心，引用真实日期 |
| diary_reflection（日记想法） | gentle_greeting | 低频问候，带连续线索 |
| return_greeting（长离线回归） | expected_return_followup | 预计返回跟进，引用离线时长 |

注：EAP 第一版 candidate kind 白名单为 5 种（conversation_continuation/expected_return_followup/emotional_care/milestone_followup/gentle_greeting）。如 LIFE 需要更细分的 kind，须在 EAP 协议升级后新增白名单，不得绕过现有 kind 直接发送。

#### 示例流程

```text
DiaryEntry
    ↓
life_share proactive seed（seed_kind='life_share'，DB CHECK 约束）
    ↓
EAP life_adapter.receive_life_seed 落库（不创建 ContactEpisode）
    ↓
EAP life_adapter.consume_seed 关联已存在 ContactEpisode
    ↓
EAP proactive-decision-v2 三层硬门评估
    ↓
LLM 判断现在是否自然（approach_drive）
    ↓
程序验证边界和投递（硬边界 LLM 无权放行）
    ↓
若拒绝：reject_seed 通知 LIFE（seed 保留审计）
```

### 8.3 与 CTX 的边界

#### CTX 拥有（5 项）

- ContextAssembler
- token 硬预算
- 当前会话摘要
- 跨会话原文召回
- 最终上下文装配

#### EAP 只提供 5 个短摘要

```text
current_affect_digest     当前心境摘要
relationship_digest       关系摘要
presence_digest           在场状态摘要
open_thread_digest        开放话题摘要
expression_plan           表达计划
```

EAP 不得自己拼接完整 Prompt。EAP 不修改 `context-package-v1`。

### 8.4 与 Memory 的边界

#### Memory 拥有（4 项）

- Fragment
- Episode
- Saga
- 正式长期记忆写入与生命周期

#### EAP 只产生 3 类候选建议

- `EmotionalMeaningCandidate`：情感意义候选
- `relationship_significance proposal`：关系意义建议
- `Episode significance suggestion`：Episode 重要度建议

EAP 不能直接创建或修改正式 Episode/Saga。

### 8.5 与 KIG 的边界

#### KIG 可以（4 项）

- 关联用户、项目、事件和世界模型实体
- 为 EAP 提供多源信息查询
- 提供来源版本和证据支持
- 将关系事件投影到 PWM

#### KIG 不能（4 项）

- 更新 bond/trust
- 决定主动发送
- 修改 Presence
- 把 PWM 投影当成 EAP 状态源

---

## 8.A 与上下文、记忆、任务和渠道的接口（v0.1 接口规格）

> v0.2 注：原第 8 节内容保留为第 8.A 节，作为 v0.1 接口规格历史记录。新所有权边界见第 8 节。

### 8.A.1 ContextAssembler

- 主动草稿不得复用无限上下文；建立独立的小预算 `ProactiveContextPackage`。
- 读取 CTX 输出只能通过稳定接口，不修改 `context-package-v1`。
- 优先顺序：当前开放话题 → 原始证据 → 相关 Episode → 稳定 Fragment → Saga 极短 digest。
- 摘要不能作为关系变化或主动发送的唯一事实证据。

### 8.A.2 Fragment / Episode / Saga

- 普通短暂情绪不写 Fragment。
- 有共同意义的事件优先成为 Episode 候选。
- Saga 只提供长期背景，不直接触发主动消息。
- 情感重要度最多作为 Episode 分组/排序的弱信号，第一版权重不超过 15%。
- 来源纠错必须使相关未发送候选失效。

### 8.A.3 任务系统

- "任务完成提醒"属于任务通知，不伪装成情感主动消息。
- 可以在未来由主动陪伴自然跟进任务，但必须区分 `task_notification` 与 `companion_proactive` 审计来源。
- 高风险任务执行中默认抑制主动消息，避免遮挡确认或急停。

### 8.A.4 外部渠道

- EAP 首版只做本机桌面渠道。
- QQ、微信、邮件必须等 ToolRegistry、PermissionPolicy、Approval、去重锁与审计闭环完成。
- 用户对桌面主动陪伴的授权不能自动扩展为外部渠道授权。
- 外部渠道未来必须逐渠道、逐目标单独配置。

---

## 9. 分阶段施工计划

> v0.2 注：以下 EAP.0~EAP.10 为 v0.1 阶段，已被 v0.2 的 EAP.A~EAP.J 取代（见第 9.A 节）。保留为历史记录，不删除、不改写已完成勾选。其中 affect 内核相关项（affect-v1.2、affect-observer-v1、9×5 tone_grid、affect_state、relationship_state、affect_events）已在 affect-v1.2 / affect-observer-v1 中实现并冻结，标注 `[x]`。

### EAP.0：真实基线、文档统一与协议冻结

目标：确认现有实现，不重复造轮子。

- [ ] 核对 `AFFECT_AND_RELATIONSHIP_SYSTEM_PLAN.md`、ADR-0004～0008、代码、schema 与测试。
- [ ] 输出“已实现/部分实现/未实现”矩阵和状态流图。
- [ ] 记录现有 1/8/24/72/168 小时时间线参数与 9×5 语调基线。
- [ ] 审计 `user_status` 当前是否实际持久化和使用。
- [ ] 审计 Episode/Saga relationship suggestion 的现有表与生命周期，避免新建重复表。
- [x] 冻结 `affect-v1.2` 与 `affect-observer-v1`；需要破坏性变更时另升版本。（已在 affect-v1.2 / affect-observer-v1 中实现并冻结）
- [ ] 建立 40 个离线陪伴场景基线，不调用真实 Provider、不读取用户正式数据库。
- [ ] 更新旧情绪设计书中过时描述。

验收：独立 Review 确认基线无 P0/P1，且新计划没有要求重写已完成情绪内核。

建议 PR：`docs(affect): freeze EAP baseline and implementation map`

### EAP.1：对话在场与离开意图协议

目标：让系统知道用户是暂时离开、忙碌、睡眠、明确结束还是未知沉默。

- [ ] 定义 `conversation-presence-v1` Pydantic schema 与 JSON Schema。
- [ ] 扩充状态枚举、置信度、逐字证据、预计返回时间、开放话题和过期时间。
- [ ] 明确状态优先级：`sleeping/closed/busy` 高于一般活跃信号。
- [ ] 使用程序规则先识别“晚安/先这样/我去测试”等高精度表达；模型只补充模糊场景。
- [ ] 模型输出必须逐字 grounding，低置信度回退 `unknown`。
- [ ] 状态写入独立表和审计事件，不修改 affect/relationship。
- [ ] 新消息到达时自动使过期离开状态结束。
- [ ] 测试中文时间表达、模糊时间、否定、引用他人话语和提示注入。

验收：明确结束和睡眠场景 100% 阻断延续候选；普通技术文本不误判离开。

建议 PR：`feat(companion): add grounded conversation presence state`

### EAP.2：用户交流状态与情感意义候选

目标：从“数值变化”升级为“理解这次互动为何重要”，但仍不直接写长期记忆。

- [ ] 新增 `user-affect-observation-v1`，只描述有证据的短期交流状态。
- [ ] 建立 `emotional-meaning-v1` 候选 schema。
- [ ] 区分庆祝、倾听、安慰、解决问题、留出空间等响应需要。
- [ ] 低置信度、普通寒暄和一次性问答不得生成重要意义候选。
- [ ] 候选只引用当前消息或已召回的有效原始证据。
- [ ] 敏感内容默认不进入主动候选；必要关心只能在本会话即时完成。
- [ ] 观察器失败不影响聊天，候选支持有限重试和幂等。
- [ ] 建立误判集：技术报错不等于用户低落，小说内容不等于用户经历，引用他人不等于自述。

验收：重要事件有可核对来源；普通问题不会被包装成“共同经历”。

建议 PR：`feat(affect): add grounded user state and emotional meaning candidates`

### EAP.3：Episode/Saga 与关系积温的受限协同

目标：让共同经历影响关系，但不允许叙事模型直接改数值。

- [ ] 复用现有 `saga_relationship_delta_suggestions` 或抽象统一 suggestion service。
- [ ] Episode 建立同等受限、带 source revision 的建议协议。
- [ ] 建立 idempotency、来源纠错、撤销和补偿事件规则。
- [ ] 每日、每周和单来源 delta 设硬上限。
- [ ] positive trust 需要可靠性/尊重边界证据；negative trust 沿用明确越界硬门。
- [ ] 情绪只占 Episode 重要度弱权重，不参与事实真实性判断。
- [ ] 关系更新与 suggestion applied 状态在同一事务提交。
- [ ] UI 默认不显示“亲密度 82”；高级诊断可查看事件链。

验收：重复整理、重启、来源纠错均不会重复增加 bond/trust；删除来源不会制造幽灵候选。

建议 PR：`feat(relationship): apply bounded episode and saga suggestions`

### EAP.4：情感表达策略 v2

目标：把用户状态、情感意义和既有语调网格组合成自然回应。

- [x] 保留 9×5 网格为遐蝶自身表达基线。（已在 affect-v1.2 / tone_grid 中实现并冻结）
- [ ] 新增 response need 修饰层，但不得覆盖人格、安全和任务清晰度。
- [ ] 同时存在“用户需要解决问题”和“用户疲惫”时，先解决问题再简短关心。
- [ ] 防止过度共情、复读用户负面话语和擅自解释心理动机。
- [ ] 高 bond 只减少重复客套，不增加未经同意的称呼、占有或承诺。
- [ ] 建立跨 Provider 文本评测集；没有真实授权时只用 mock/人工离线样本。
- [ ] Live2D 继续只读统一 cluster；为表达强度增加受限动作选择，不增加第二情绪源。
- [ ] 语音只预留 prosody contract，不在本阶段接入 TTS。

验收：事实任务准确性不因情绪下降；安慰场景不说教，技术场景不强行煽情。

建议 PR：`feat(companion): compose affect and response-need expression policy`

### EAP.5：主动候选账本与确定性策略守卫

目标：先能安全地决定“不发”，暂不产生真实通知。

- [ ] 新建候选、决定和策略版本数据结构。
- [ ] 实现五种固定候选 kind。
- [ ] 实现总开关、类型开关、quiet hours、暂停、冷却、额度和未回复硬门。
- [ ] 实现 `allow/defer/suppress/expire` 纯决策函数。
- [ ] `contact_need` 信号只创建候选，不调用任何投递 API。
- [ ] 用户返回、来源纠正、候选过期时自动取消。
- [ ] 决策日志不复制完整聊天正文。
- [ ] 建立 shadow 模式：只记录“如果开启会怎样”，普通用户 UI 不显示。

验收：关闭主动陪伴时 0 次发送；晚安、忙碌、未回复、quiet hours 和额度场景 100% 抑制。

建议 PR：`feat(proactive): add shadow candidates and deterministic policy guard`

### EAP.6：桌面主动消息草稿与本地投递闭环

目标：只在用户显式开启后，通过本机渠道投递一条安全消息。

- [ ] 设置页增加完整主动陪伴控制。
- [ ] 草稿生成使用独立小上下文包和严格输出 schema。
- [ ] 最终程序校验长度、禁语、问题数量、证据新鲜度与 content hash。
- [ ] 首版实现桌宠气泡；桌面通知作为独立可选渠道。
- [ ] 投递前再次读取策略，避免草稿期间状态变化。
- [ ] 投递完成写 delivery；失败有限重试且过期即放弃。
- [ ] 应用重启后不能重复投递已发送候选。
- [ ] 普通聊天窗口自然显示主动消息，不展示技术标签。

验收：显式开启前 0 投递；开启后每个候选最多投递一次；重启、断网、时区切换不重复发送。

建议 PR：`feat(proactive): deliver consented desktop companion messages`

### EAP.7：聊天延续与预计返回

目标：实现用户提出的“聊得正起劲，十几二十分钟没回复”和“我去做某事”的体验。

- [ ] 定义活跃会话判定：最近轮次密度、开放问题、immersion 和明确离开状态。
- [ ] `conversation_continuation` 只在 15～90 分钟窗口内有效。
- [ ] `expected_return_followup` 优先读取用户明确时间；无时间时采用保守默认。
- [ ] 用户回来即取消候选，不能在用户正在回复时再弹旧消息。
- [ ] 明确结束、睡眠、忙碌和“别等我”场景全部禁止。
- [ ] 文案必须允许用户无负担地晚点继续。
- [ ] 建立 100 个合成时间线，覆盖跨午夜、休眠、重启和时钟回拨。

验收：用户说“晚安”后零追问；用户说“我去测试”且允许追问时最多一次相关跟进。

建议 PR：`feat(proactive): add bounded conversation continuation`

### EAP.8：延迟关心、里程碑跟进与反馈学习

目标：让主动陪伴有长期连续性，并从用户反馈中学会分寸。

- [ ] `emotional_care` 必须来自用户明确表达和有效事件，不从语气猜测单独触发。
- [ ] `milestone_followup` 必须引用 Episode/开放事项，Saga 不能单独触发。
- [ ] `gentle_greeting` 默认至少 72 小时无互动，并服从每日总额度。
- [ ] 增加主动消息反馈菜单与 API。
- [ ] 忽略只降低频率；拒绝/暂停立即生效；不改变关系数值。
- [ ] 连续忽略自动冷却并最终暂停，禁止继续试探。
- [ ] 学习结果只保存候选 kind、时段和频率偏好，不保存额外敏感正文。
- [ ] 支持按主动类型永久关闭。

验收：错误时机反馈会阻止同类短期再发；用户长期忽略时系统自动安静。

建议 PR：`feat(proactive): add grounded care followups and feedback learning`

### EAP.9：模拟器、校准与隐私审计

目标：在真实发布前证明系统克制、可解释、不会打扰。

- [ ] 建立确定性时间线模拟器和 JSON/CSV 无正文报告。
- [ ] 覆盖 15 分钟、90 分钟、24 小时、7 天、30 天场景。
- [ ] 覆盖积极回应、普通回应、连续忽略、明确拒绝和关闭功能。
- [ ] 覆盖系统睡眠、时区变化、时钟回拨、应用崩溃和断网。
- [ ] 建立候选准确率、发送适当率、错误打扰率、重复发送率、禁语率指标。
- [ ] 校准只允许使用合成数据或用户明确授权的脱敏样本。
- [ ] 运行日志、导出、错误和诊断均不得包含 API Key 或完整敏感正文。
- [ ] 完成 UI、伦理、安全与隐私独立 Review。

发布门槛：

```text
重复发送率                 = 0
关闭/暂停后发送率          = 0
quiet hours 违规率          = 0
明确结束后延续率            = 0
未回复后二次催促率          = 0
操纵/内疚/占有禁语命中      = 0
有证据候选比例              = 100%
人工适当性评估              ≥ 90%
```

建议 PR：`test(proactive): calibrate butterfly-loop safety and timing`

### EAP.10：产品验收、冻结与下一渠道边界

目标：形成可发布的本机主动陪伴 v1。

- [ ] 后端全量测试通过。
- [ ] 前端测试、TypeScript、Vite build、Electron 检查通过。
- [ ] Windows 安装版完成休眠/唤醒、重启、通知权限和卸载数据策略验收。
- [ ] 完成至少 30 天合成时间线压力测试。
- [ ] 完成 9×5 表达网格、五类候选和所有硬门矩阵验收。
- [ ] 设置、暂停、关闭、清除和反馈行为全部可逆。
- [ ] 更新基线、项目上下文、长期路线和用户说明。
- [ ] 冻结 EAP v1 协议与 schema。
- [ ] 独立总 Review 确认 0 个未解决 P0/P1。

EAP v1 冻结后，外部渠道仍不得直接启用。下一入口必须先完成 ToolRegistry、PermissionPolicy、Approval、ToolRun/AuditEvent 和渠道级去重锁。

建议 PR：`feat(companion): complete and freeze proactive companion v1`

---

## 9.A 施工阶段重组 EAP.A~EAP.J（v0.2）

> **历史归档警告：本节 EAP.A～EAP.J 的勾选、能力状态和完成描述均不是当前项目完成度，不得用于施工或验收。当前唯一有效入口是第 9.B 节 EAP.R0～EAP.R6。**

> v0.2 新增：从 EAP.0~EAP.10 调整为 EAP.A~EAP.J。旧阶段中已实现且符合新方向的能力审查后直接标记 `[x]`，部分符合的标记 `[~]` 并只补差距，未实现的标记 `[ ]`。所有新施工项标注为 `[ ]`（未开始），不提前勾选未经代码和测试验证的施工项。

### EAP.A：修订产品边界、默认策略和旧条款

目标：在代码层面移除 v0.1 中与 v0.2 冲突的硬编码，默认开启本机主动陪伴配置项。

- [ ] 在代码层面移除 v0.1 中与 v0.2 冲突的硬编码（如有）
- [ ] 默认开启本机主动陪伴配置项
- [ ] 验证：v0.1 冲突条款在代码中不再生效

能力状态：`[ ]` 未开始（v0.1 旧条款标记为 `[→]` 已在文档层改写，代码层待施工）

### EAP.B：公共 DecisionRun 接线与新协议注册

目标：审查 LIFE 是否已实现 DecisionRun 公共抽象，建立最小公共抽象，注册 EAP 领域协议。

- [ ] 审查 LIFE 是否已实现 DecisionRun 公共抽象
- [ ] 如未实现，建立最小公共抽象（run 账本、idempotency_key、source_hash、状态机）
- [ ] 为 `affect_observer_runs` 增加 `source_hash` 字段（migration）
- [ ] 注册 EAP 领域协议（conversation-presence-v2 等 6 个）
- [ ] 验证：现有 6 个子系统 run 账本模式可复用新抽象

能力状态：`[~]` 部分实现（DecisionRun 模式在 6 个子系统中重复出现但未抽象为公共基类；`affect_observer_runs` 缺 `source_hash` 字段）

### EAP.C：Conversation Presence v2 与 OpenThread

目标：让系统知道用户是暂时离开、忙碌、睡眠、明确结束还是未知沉默。

- [ ] 新建 `conversation_presence` 表与 `conversation-presence-v2` 协议
- [ ] 扩展状态枚举为 8 值（active/expect_return/temporarily_away/busy/sleeping/conversation_closed/inactive_unknown/unknown）
- [ ] 实现程序规则识别"晚安/我去测试/先这样"等高精度表达
- [ ] 实现状态优先级、过期时间、expected_return_at、open_thread
- [ ] 新消息到达时自动使过期离开状态结束
- [ ] 验证：明确结束和睡眠场景 100% 阻断延续候选

能力状态：`[ ]` 未实现（`user_status` 仅 4 值枚举，需扩展为 8 值；`conversation_presence` 表未建）

### EAP.D：关系意义判断，移除普通聊天机械涨 bond

目标：让共同经历影响关系，但普通聊天不再机械增加 bond。

- [ ] 新建 `episode_relationship_delta_suggestions` 表
- [ ] 实现 LLM 关系意义标签输出（9 种）
- [ ] 普通问答不产生显著 bond 增量
- [ ] 实现单轮限幅、同一事件幂等、来源证据校验
- [ ] 扩展 `saga_relationship_delta_suggestions` 支持 `boundary_repair` 信号
- [ ] 验证：重复整理、重启、来源纠错均不重复增加 bond/trust

能力状态：`[~]` 部分实现（`saga_relationship_delta_suggestions` 已实现但仅支持 `shared_saga_completed` 信号；`episode_relationship_delta_suggestions` 表未建）

### EAP.E：ContactEpisode 与动态未回复压力

目标：把同一话题的连续主动作为 ContactEpisode 管理，实现动态未回复压力。

- [ ] 新建 `contact_episodes` 表与状态机（10 值）
- [ ] 实现 `unanswered_pressure` 累积与衰减
- [ ] 实现用户后续行为影响（积极回应、别一直催我等 6 类）
- [ ] 验证：用户沉默不降低 bond/trust

能力状态：`[ ]` 未实现（`contact_episodes` 表未建；`unanswered_pressure` 模型未实现）

### EAP.F：Proactive Decision v2 Shadow 模式

目标：实现三层硬门、接近意愿与打扰负担模型、LLM 结构化情境决策。

- [ ] 新建 `proactive_candidates`、`proactive_decisions` 表
- [ ] 实现三层硬门（硬边界/延后条件/动态因素）
- [ ] 实现 `approach_drive`、`contact_cost`、`effective_drive`、`approach_value` 评估
- [ ] 实现 LLM 结构化情境决策（`proactive-decision-v2` 协议）
- [ ] 旧线性公式作为 Shadow 基线并行运行
- [ ] 验证：关闭主动陪伴时 0 次发送；硬边界 100% 阻断

能力状态：`[ ]` 未实现（`proactive_candidates`/`proactive_decisions` 表未建；旧线性公式未实现为 Shadow 基线）

### EAP.G：主动强度阶梯与 Live2D 低干扰行为

目标：实现 Level 0~5 六档强度阶梯，Live2D 低干扰行为。

- [ ] 实现 Level 0~5 六档强度阶梯
- [ ] 实现"最低足够强度"决策原则
- [ ] 实现 Live2D 视线/表情/轻微动作（Level 1）
- [ ] 实现无通知小气泡（Level 2）
- [ ] 验证：LLM 认为不值得打断时优先 Level 1/2

能力状态：`[ ]` 未实现（Live2D 表达强度受限动作选择未实现；强度阶梯未实现）

### EAP.H：表达向量、迟滞与 ExpressionPlan

目标：实现情绪惯性迟滞、连续表达向量、ExpressionPlan 协议。

- [ ] 实现 `minimum_state_duration`、`hysteresis_margin`、`transition_momentum`
- [ ] 实现连续表达向量（warmth 等 7 维）
- [ ] 实现 ExpressionPlan 协议（`expression-plan-v1`）
- [ ] 验证：阈值附近不频繁跳变；ExpressionPlan 不影响事实

能力状态：`[ ]` 未实现（迟滞参数未实现；连续表达向量未实现；ExpressionPlan 协议未实现）

### EAP.I：LIFE 生活事件、日记、重要日期接入

目标：接入 LIFE 生活事件、日记、重要日期，形成 proactive seed。

- [ ] 等待 LIFE 专项提供 LifeEvent、PersonalGoal、ImportantDate、Diary、SelfTimeline
- [x] 实现 `life_share` proactive seed 接入 EAP ContactEpisode（EAP v0.2 已完成接口预留：`life_proactive_seeds` 表 + `life_adapter.py`）
- [x] 验证：LIFE 不得直接发送主动消息（DB CHECK 约束 `seed_kind = 'life_share'` 已实现）
- [x] 验证：EAP 不得伪造 LifeEvent（`consume_seed` 只关联已存在 episode，不写 LIFE 侧表）

能力状态：`[~]` 接口预留已实现（2026-07-21 EAP v0.2 施工完成），等待 LIFE 专项提供数据模型实际接入

### EAP.J：长期模拟、用户偏好适应和总验收

目标：建立确定性时间线模拟器，覆盖长期场景，完成总验收。

- [ ] 建立确定性时间线模拟器
- [ ] 覆盖 15 分钟~30 天场景
- [ ] 覆盖积极回应、连续忽略、明确拒绝、关闭功能
- [ ] 覆盖系统睡眠、时区变化、时钟回拨、应用崩溃、断网
- [ ] 前端 SettingsPage 新增"陪伴与主动消息"Tab（9 项控制）
- [ ] 完成四类验收场景测试（主动表达、关系、心境、安全与边界，见第 14 节）
- [ ] 冻结 EAP v0.2 协议与 schema
- [ ] 独立总 Review 确认 0 个未解决 P0/P1

能力状态：`[ ]` 未实现（40 个离线陪伴场景基线当前只有 9 个；前端 SettingsPage "陪伴与主动消息" Tab 未实现）

---

## 9.B EAP 完成度审计与收口补完计划（v0.3，当前唯一施工入口）

### 9.B.1 审计结论

本节以 2026-07-22 的真实代码为准。测试通过只能证明已接线能力和独立领域模块在测试输入下行为稳定，不能替代产品主链验收。

当前应使用以下状态：

| 能力 | 当前状态 | 已有实现 | 完成前仍缺少 |
|---|---|---|---|
| Schema 48～55 | `[x]` 已落地 | Presence、关系建议、ContactEpisode、Candidate/Decision、Intensity、Expression、LIFE seed 表 | 禁止修改历史迁移；新缺口使用后续迁移 |
| Conversation Presence | `[~]` 部分接线 | 用户消息入库后运行规则识别并写表 | 枚举契约统一、过期/恢复 worker、诊断、来源 revision 和端到端候选触发 |
| User Affect Observation | `[ ]` 未实现 | 只有 `user-affect-observation-v1` 版本常量 | 严格 Schema、来源校验、repository、worker、降级与测试 |
| Relationship Meaning | `[~]` 领域模块完成 | 9 类标签、限幅、建议表和幂等键 | 真实 LLM/规则生产者、原子应用/撤销、主链接入；移除普通聊天机械涨 bond |
| ContactEpisode | `[~]` 领域模块完成 | 状态机、压力累积/衰减和纯模块测试 | 真实候选来源、用户回复/拒绝回写、worker 驱动和崩溃恢复 |
| Proactive Candidate/Decision | `[~]` 领域模块完成 | 候选表、三层裁决、Shadow 分数和测试 | 调度编排、真实来源失效检查、用户设置全量生效、并发 claim 和真实 LLM 建议 |
| Intensity/Expression | `[~]` 领域模块完成 | Level 0～5 选择、表达向量、迟滞数据结构 | 接入统一 Live2D 状态源、气泡/聊天/通知渠道和实际表达消费 |
| Proactive Delivery | `[ ]` 未实现 | 无正式投递表和投递服务 | at-most-once 投递账本、渠道适配器、授权复核、重启恢复和失败状态机 |
| Proactive Feedback | `[ ]` 未实现 | 只有 `proactive-feedback-v1` 版本常量 | 反馈 Schema、表/API、自然语言与显式反馈入口、策略应用和撤销 |
| LIFE 接口 | `[~]` 接口预留 | `life_proactive_seeds` 与 adapter | LIFE 实际数据模型完成后接入；这不阻塞 EAP 核心闭环冻结 |
| 设置页 | `[~]` UI 已有 | 总开关、类型、安静时段、频率、渠道、暂停和诊断入口 | 后端默认/校验、所有控制真实生效、历史/反馈/清除 API 和专项前端测试 |
| 长期模拟 | `[~]` 测试工具已实现 | 确定性时间线模拟和四类测试样例 | 使用与生产相同的 orchestration/delivery 代码路径；休眠、崩溃、并发、时区真实验收 |
| 协议冻结 | `[ ]` 未达到 | 6 个版本字符串已定义 | Schema、validator、repository、真实调用路径、兼容测试和独立 Review 后才能 FROZEN |

当前只有 Presence hook 进入聊天主链。不得用测试文件直接调用领域函数的“端到端场景”替代真实应用端到端验收。

协议状态定义：

| 状态 | 判定标准 |
|---|---|
| `DRAFT` | 只有名称/版本常量，或 Schema、validator、repository 任一关键部分尚不存在 |
| `IMPLEMENTED` | 领域 Schema、validator/repository 和专项测试已存在，但尚未进入真实生产调用路径 |
| `SHADOW` | 已由真实生产路径调用并记录结果，但不改变正式状态或产生用户可见行为 |
| `FROZEN` | 生产者、消费者、来源校验、失败降级、兼容测试和独立 Review 全部完成，且 0 个未解决 P0/P1 |

EAP 六个新增协议的当前状态：

| 协议 | 当前状态 | 证据与缺口 |
|---|---|---|
| `conversation-presence-v2` | `IMPLEMENTED` | 表、领域函数、测试和聊天 hook 已存在；枚举契约、过期 worker 和候选消费者未闭环 |
| `user-affect-observation-v1` | `DRAFT` | 只有版本常量 |
| `relationship-meaning-v1` | `IMPLEMENTED` | 建议表、限幅和测试已存在；无真实生产者和 apply/revoke 主链 |
| `proactive-decision-v2` | `IMPLEMENTED` | 决策表、三层裁决和测试已存在；无生产 orchestrator，不能称为 Shadow |
| `expression-plan-v1` | `IMPLEMENTED` | 表、向量/迟滞逻辑和测试已存在；无真实表达消费者 |
| `proactive-feedback-v1` | `DRAFT` | 只有版本常量，无表、Schema、API 或消费者 |

`affect-observer-v1` 属于既有 Affect 子系统，不计入上述六个 EAP 新增协议；KIG/LIFE 引用时必须保持这一所有权区别。上述状态表是 R0 的文档事实源；R1 再实现可执行 Protocol Registry。

### 9.B.2 收口范围与非目标

本轮收口必须完成：

1. 把已有领域模块接成唯一、可恢复、可审计的 EAP 运行时闭环。
2. 让总开关、暂停、类型、安静时段、频率和渠道授权全部成为后端硬边界。
3. 让普通聊天不再机械增加 bond，让关系变化只来自有来源的关系意义建议。
4. 建立真实投递与反馈账本，保证关闭、暂停、拒绝和重启后不会误发或重发。
5. 让 Live2D、气泡、主窗口消息和桌面通知消费同一决定与表达计划。
6. 修正文档、协议状态和项目基线，使“已实现”“部分实现”“冻结”均有真实证据。

本轮不包含：

- LIFE 的 LifeEvent、日程、日记、ImportantDate、PersonalGoal 和 SelfTimeline 实体实现。
- QQ、微信、邮件等外部渠道正式投递；Level 5 必须保持硬禁用，即使通用设置键被误写为开启也不能发送。
- 后台常驻系统服务、工具执行、MCP 或任意桌面自动化。
- KIG 的跨源 Query Planner、知识语义重排、Claim/PWM 和世界模型。
- 修改 Schema 48～55 的历史迁移，或重新建立 affect/relationship/Fragment/Episode/Saga。

### EAP.R0：纠正完成声明、冻结状态与迁移所有权

目标：先恢复可信基线，避免 LIFE/KIG 在错误前置条件上施工。

- [x] 将 EAP.A～EAP.J 标注为 v0.2 历史施工清单；以第 9.B 节作为当前状态来源。
- [x] 建立协议状态表：`DRAFT`、`IMPLEMENTED`、`SHADOW`、`FROZEN`；版本常量本身不算协议实现。
- [x] 核对 6 个 EAP 协议各自的 Schema、validator、repository、生产调用路径和消费者。
- [x] 将未达到冻结条件的协议恢复为 `DRAFT` 或 `IMPLEMENTED`，不得继续对外宣称 FROZEN。
- [x] 冻结 Schema 48～55 历史文本；EAP 收口新增迁移从 Schema 56 顺序开始。
- [x] 在当前施工基线中撤销 LIFE 对 Schema 56 的预占；项目外 LIFE 原版保持原样，待 EAP.R6 后以原版为基准优化时改用第一个可用版本。
- [x] 更新 `BASELINE_STATUS.md`、`CODEX_PROJECT_CONTEXT.md`、README 测试数和长期路线中的旧状态。
- [x] 明确计划书来源：EAP 以仓库 `docs/` 本文件为事实源；LIFE/KIG 以项目外两份原版为后续优化基准，仓库内后续修改版已按用户要求删除，优化完成前不复制回仓库。

完成门：

- 文档不再出现“只有常量却已冻结”“只有独立模块却已完成闭环”的表述。
- 当前施工只允许 EAP 从 Schema 56 顺序占用；项目外 LIFE/KIG 原版暂不参与施工，后续优化时必须重新分配 schema，因而当前迁移所有权无冲突。
- 当前后端、前端和构建基线通过。

R0 施工记录（2026-07-22）：`[x]` 已完成并通过独立 Review。Review 无 P0；采纳的 P1/P2 建议进入 R1，`compute_layer3_factors` 的 N+1 查询归入 R3 编排与性能施工。验证结果为后端 `820 passed, 1 warning`、前端 `36 passed`、TypeScript/Vite build 通过（185 modules）、Electron `main.js`/`preload.js` 语法检查通过。仓库内 LIFE/KIG 后续修改版已按用户要求删除；项目外两份原版恢复到原路径并保持不改动，作为将来计划优化的输入。

建议提交：`docs(eap): reopen incomplete runtime closure after implementation audit`

### EAP.R1：协议治理、设置硬边界与 Presence 契约

目标：建立运行时闭环可以依赖的统一协议与用户控制底座。

- [x] 建立最小 Protocol Registry，记录协议名、版本、状态、validator 和兼容范围。
- [x] 把 `run_ledger.py` 从哈希/常量工具补成可复用的 DecisionRun repository，至少包含 source revision/hash、状态、尝试次数、错误码、模型元数据、幂等键和时间戳。
- [x] 不强制迁移所有历史 run 表；通过 adapter 兼容现有 observer/summary/knowledge run。
- [x] 为 `user-affect-observation-v1` 和 `proactive-feedback-v1` 定义严格结构化 Schema 与 validator。
- [x] 建立后端主动陪伴设置注册表，统一默认值、合法值、读取和写入校验。
- [x] 后端必须强制执行：总开关、紧急停止、暂停截止时间、六类主动类型、安静时段、频率模式、桌面通知授权和外部渠道硬禁用。
- [x] 暂停和安静时段使用本地时区进行可靠计算；非法时间、过期暂停和系统时间倒退保守处理。
- [x] 统一 `conversation-presence-v2` 的实际 8 值枚举与文档；如果需要不兼容改名，新增 `conversation-presence-v3`，不得静默改写 v2。
- [x] Presence 过期、用户重新出现、明确结束、睡眠和 DND 的状态转换必须由单一 reducer 管理。

专项测试：

- 每个设置的默认值、非法值、持久化和裁决效果。
- 任意 `proactive_enabled=0`、有效暂停或 emergency stop 均产生 0 次非静默行为。
- 六类 kind 任意关闭后，对应候选 100% 被硬门阻断。
- 自定义安静时段和跨午夜、时区变化、时钟回拨。
- Presence v2/v3 兼容、过期和来源变化。

完成门：设置页不存在“能保存但不生效”的控制；协议状态和真实实现一致。

R1 施工记录（2026-07-22）：`[x]` 已完成并通过独立 Review，0 个未解决 P0/P1。新增 Schema 56 `decision_runs`，保留历史 run 表并提供只读 adapter；协议注册表只把已有完整实现标为 `IMPLEMENTED`，只有 Schema 的协议继续标为 `DRAFT`。设置 API、决策硬门、强度授权和设置页统一执行后端注册表语义；Level 5 外部渠道在 API、运行时与 UI 三层硬禁用。Presence hook 异常改为结构化 warning，不再静默吞掉。Review 的 N+1 建议继续归入 R3；所谓 `silence` 频率模式与当前有效计划及产品三模式契约不符，不采纳。验证结果：后端 `841 passed, 2 warnings`，前端 `36 passed`、TypeScript/Vite build 通过（188 modules）、Electron `main.js`/`preload.js` 语法检查通过。

建议提交：`feat(eap): enforce protocol registry settings and presence contracts`

### EAP.R2：User Affect 与关系意义真实接线

目标：关闭“普通聊天仍机械涨 bond”和“关系建议永远停在 proposed”的核心缺口。

- [x] 新增本轮 Companion Cognition 后台任务，优先复用现有 Affect Observer 的队列、Provider 配置和失败降级模式。
- [x] 产出有逐字用户证据的 User Affect Observation；不得做医学诊断，不得把知识库、助手文本或摘要作为用户状态唯一证据。
- [x] 产出 9 类 Relationship Meaning 建议；普通寒暄、一次性问答和无证据结果使用 `ordinary_exchange`。
- [x] 修改 fallback interaction：保留 `interaction_count` 和必要的短期 affect 响应，但普通消息不再无条件增加 bond/trust。
- [x] 为 `episode_relationship_delta_suggestions` 实现 `proposed → applied/revoked` 原子状态机、revision/hash 复核和审计事件。
- [x] 应用关系 delta 时使用现有 `relationship_state` repository，禁止建立第二套正式关系状态。
- [x] 同一 source revision 只应用一次；来源被删除、纠正或失效时支持撤销/重算，但不得自动重写用户手动修正。
- [x] 用户沉默、离线、未回复和关闭主动陪伴不得降低 bond/trust。
- [x] 不对历史普通聊天自动追溯扣回 bond；是否迁移旧数据必须另立显式数据修正方案。

专项测试：

- 普通问答、感谢、可靠帮助、共同成功、脆弱披露、边界尊重/修复、重逢、冲突九类。
- 无 Observer、Provider 超时、解析失败和来源变化时的保守 fallback。
- 重复 worker、重启、并发 apply、撤销后重算。
- 现有 affect/relationship 全量回归和真实聊天 API 集成测试。

完成门：真实聊天路径中的普通问答 bond 增量为 0；所有非零关系变化均可回溯到有效来源和协议版本。

R2 施工记录（2026-07-22）：`[x]` 已完成并通过独立 Review，0 个未解决 P0/P1。Schema 57 为关系建议增加 source revision/hash、逐字证据、应用/撤销事件引用，并新增 `companion_cognition_results`；后台任务复用现有观察模型配置与有限重试，但运行账本统一使用 R1 `DecisionRun`。真实聊天和重新生成均入队 Companion Cognition；无模型、超时、解析失败使用 `unknown + ordinary_exchange` 保守结果。Fallback 只更新短期 affect 与 `interaction_count`，不再修改 bond/trust；遗留 Affect Observer 即使恢复旧任务也会抑制其 relationship delta。关系应用只写现有 `relationship_state`，并按实际限幅增量补偿撤销；来源删除/改写会撤销，重新生成会创建新 revision，晚于应用的用户手动关系修正不会被自动覆盖。未追溯改写任何历史关系数据。Review 唯一 P2 为 `compute_layer3_factors` N+1，已在 R3 以批量候选类型查询修复。R2 验收门禁：后端 `867 passed, 1 warning`，前端 `36 passed`，前端生产构建及 Electron `main.js`/`preload.js` 语法检查通过。

建议提交：`feat(eap): connect grounded user affect and relationship meaning`

### EAP.R3：候选生成、ContactEpisode 与运行时编排

目标：把 Presence、情绪、共同经历和 LIFE seed 适配器接入同一候选与评估 worker。

- [x] 建立唯一 `ProactiveOrchestrator`，由应用 lifespan 启动/停止，不允许各模块自行发送。
- [x] 使用数据库 due queue 和可恢复游标，不使用每分钟无条件调用 LLM 的轮询方式。
- [x] 实现生产候选来源：OpenThread/expected return、情绪关心、Episode/Saga 里程碑、受限普通问候和 LIFE seed adapter。
- [x] 每个候选必须包含真实 source refs、revision/hash、过期时间和 candidate kind。
- [x] 建立候选 claim/lease，两个 worker 对同一候选只能有一个进入决策和投递。
- [x] 在 LLM 调用前后都重新执行总开关、暂停、类型、Presence、来源有效性和渠道授权硬门。
- [x] 把 `decide_candidate`、Intensity 和 ExpressionPlan 组合为单一事务边界或可恢复 saga，避免留下“decision 已写入但 candidate 状态未更新”的半完成状态。
- [x] 用户新消息到达时关联同一 ContactEpisode，更新 responded/closed/feedback；过期候选不得复活。
- [x] LIFE seed 只作为候选来源；没有 LIFE 表时使用 fixture 验证接口，不伪造 LifeEvent。
- [x] Shadow 决定绝不进入真实投递队列。

专项测试：

- 主应用 lifespan 启停、worker 崩溃恢复、重复启动、双 worker 竞争和数据库忙。
- 来源在决策中途删除/纠正、用户在草稿完成前回来、设置在决策中途关闭。
- 15 分钟～30 天的 due queue 推进；测试时钟与生产 orchestrator 使用同一代码路径。
- LIFE seed fixture 只能生成 EAP candidate，不能直接创建 decision/delivery。

完成门：无需测试代码手工串联领域函数，真实应用 worker 即可把有效来源推进到已裁决候选；仍不开放真实非静默投递。

R3 施工记录（2026-07-22）：`[x]` 已完成并通过独立 Review。Schema 58 新增 `proactive_runtime_sources` due queue、`proactive_candidate_claims` 租约和 `proactive_runtime_sagas` 恢复账本，并为候选补充 source revision、due 时间和运行时来源唯一引用。唯一 `ProactiveOrchestrator` 由主应用 lifespan 管理；真实聊天产生 expected-return/受限问候来源，Companion Cognition 产生 grounded 情绪关心来源，Episode/Saga 通过不追溯历史的持久游标发现新完成里程碑，LIFE 仍只通过 seed fixture/adapter 进入 EAP。worker 在来源物化和裁决前后复核来源与硬门，组合既有 Decision、Intensity、ExpressionPlan 为可恢复 shadow saga；claim lease、重复启动、双 worker、数据库忙、崩溃恢复、用户提前回来、来源中途修改及 15 分钟～30 天时钟均有专项覆盖。R3 Review 的 1 项 P1 与 4 项 P2 全部采纳：claim 返回更新后行、来源载荷窄异常分类、里程碑游标校验和备份、用户返回单事务、worker 数据库错误与编程错误分流均已补齐专项回归。R2 Review 的 Layer 3 N+1 建议已用单次批量查询采纳。

建议提交：`feat(eap): add recoverable proactive runtime orchestration`

### EAP.R4：投递账本与本机表达渠道

目标：建立唯一、可审计、at-most-once 的真实输出路径。

- [x] 新增顺序迁移：`proactive_deliveries`、投递尝试/事件表和必要唯一约束。
- [x] Delivery 至少记录 decision、candidate、episode、channel、payload hash、授权 revision、状态、attempt、错误码、delivered_at 和 acknowledged_at。
- [x] 定义投递状态机：`queued/claimed/delivering/delivered/failed/cancelled/suppressed/expired`。
- [x] 最终发送前重新检查所有硬边界；用户关闭、暂停、拒绝、来源失效或授权变化必须取消未投递项。
- [x] Level 0：只记录决定，不产生用户可见行为。
- [x] Level 1：通过现有统一 Live2D 状态源发出受限视线/表情/轻动作，不建立第二套前端情绪源。
- [x] Level 2：实现无系统通知的小气泡，支持自动消失和不要求回复。
- [x] Level 3：写入有明确 `proactive_delivery_id` 的主窗口 assistant message，并走正常消息刷新/流式兼容路径。
- [x] Level 4：只有 Windows 通知授权明确开启时才可投递；首次授权失败不得自动重试骚扰。
- [x] Level 5：本专项保持硬禁用，不实现 QQ/微信/邮件发送器。
- [x] `delivered` 只代表渠道成功确认；进程在调用渠道前后崩溃时不得重复投递。

专项测试：

- 每个 Level 的授权、降级与取消。
- 两个 worker、进程崩溃、数据库提交失败和渠道超时下的 at-most-once 语义。
- 已投递消息重启不重发，未确认状态进入人工可解释的保守恢复。
- Electron 主窗口、Live2D、气泡和 Windows 通知真实集成测试。

完成门：所有用户可见主动行为都能追溯到唯一 Delivery；关闭或暂停时用户可见行为为 0；Level 5 永远为 0。

R4 施工记录（2026-07-22）：`[x]` 已完成，等待独立 Review。Schema 59 新增唯一 Delivery、单次 attempt 与事件账本，并为聊天消息增加唯一 `proactive_delivery_id`。真实本机投递继续采用显式实验开关，默认关闭；开启后，orchestrator 才生成非 shadow 决策和不可变投递载荷。Electron 只负责 claim/begin/ack，后端在唯一调用边界前以写事务复核来源、候选、过期时间、暂停/急停、类型开关、桌面通知授权和设置 revision。调用前崩溃可安全重新 claim，调用后未确认则标记 `delivery_confirmation_unknown` 并永不自动重试。Level 1 复用桌宠状态/表情路径，Level 2 气泡自动消失，Level 3 原子写主窗口消息并触发刷新，Level 4 仅在显式授权时调用 Windows Notification；Schema 与运行时均不能容纳 Level 5。专项覆盖双 consumer、数据库提交失败、来源/授权变化、失败确认、重启恢复和各本机 Level。验收门禁：后端 `902 passed, 1 warning`，改动范围 Ruff 通过；前端 `40 passed`，TypeScript/Vite production build（188 modules）通过；Electron 脚本语法、真实桌宠→主窗口启动及新设置页默认关闭 smoke 通过。全仓 Ruff 仍有 69 项既有历史债，本阶段改动文件为 0 项。

建议提交：`feat(eap): add auditable local proactive delivery channels`

### EAP.R5：反馈闭环、用户控制与可解释历史

目标：让用户能自然纠正时机、频率、话题和语气，且反馈真实改变后续行为。

- [ ] 新增 `proactive_feedback` 表、repository、API 和审计事件，实现 `proactive-feedback-v1`。
- [ ] 支持显式反馈：时机不对、太频繁、内容不对、别再提此话题、别用这种语气、可以继续提醒。
- [ ] 支持有高精度逐字证据的自然语言反馈候选；低置信度只进入待确认，不直接建立永久边界。
- [ ] 反馈必须关联 Delivery/ContactEpisode/topic/kind/expression act，禁止无来源全局惩罚。
- [ ] 将反馈映射到 topic/kind 硬边界、`unanswered_pressure`、contact cost 和表达偏好；不得降低 bond/trust。
- [ ] 补齐主动消息历史 API/UI，显示时间、自然原因、渠道、结果和用户反馈，不显示裸分数。
- [ ] 补齐开发者诊断 API/UI，显示 candidate、硬门、reason code、协议/策略版本和 source ID，不复制不必要正文。
- [ ] 实现清除待处理候选/主动历史和重置设置；不得隐式删除聊天、记忆、关系或 LIFE 数据。
- [ ] 设置重置使用单个后端原子 API，不从前端并发发送多个 PUT 后提前提示成功。
- [ ] 为整个“陪伴与主动消息”Tab 增加专项前端测试和至少一条 Electron UI 流程测试。

完成门：用户所有可见控制均有后端语义；“暂停”“别再提”“太频繁”在下一次评估前生效；历史和清除功能不再显示“开发中”。

建议提交：`feat(eap): close proactive feedback and user control loop`

### EAP.R6：生产路径长期模拟、总验收与正式冻结

目标：只在真实生产路径满足完成定义后关闭专项。

- [ ] 重构现有 `timeline_simulator.py`，让它驱动生产 reducer、orchestrator、decision、delivery 和 feedback repository，而不是测试专用旁路。
- [ ] 覆盖 15 分钟、8 小时、24 小时、3 天、30 天；覆盖时区变化、时钟回拨、Windows 休眠/唤醒、断网、Provider 失败、应用崩溃和数据库忙。
- [ ] 覆盖积极回应、连续忽略、明确拒绝、关闭、暂停、类型关闭、话题拒绝、表达修复和重新授权。
- [ ] 完成第 14 节全部验收场景；对无法自动判断的自然度样本保存人工评分记录。
- [ ] 后端全量测试、前端测试、TypeScript、Vite build、Electron 脚本和 Windows 安装版 smoke test 全部通过。
- [ ] 验证真实聊天仍可在 EAP worker、Provider、通知或 Live2D 失败时正常完成。
- [ ] 验证普通聊天 bond 机械增长率为 0，用户沉默导致 bond/trust 下降率为 0。
- [ ] 验证关闭/暂停/拒绝后的违规投递率为 0，重复投递率为 0，来源可追溯率为 100%。
- [ ] 完成独立 strict Review，确认 0 个未解决 P0/P1。
- [ ] 更新本计划、基线、项目上下文、README、长期路线、用户说明和 LIFE/KIG 前置条件。
- [ ] 只有此时才能把实际完成的协议、策略和最后一个 EAP schema 标记为 FROZEN。

冻结前必须同时满足：

```text
真实运行时闭环可达率                       = 100%
关闭/暂停/拒绝后用户可见主动行为             = 0
重复投递率                                 = 0
普通聊天机械 bond 增长率                    = 0
沉默导致 bond/trust 下降率                  = 0
无有效来源的非零关系变化                    = 0
无有效来源的主动候选/投递                   = 0
所有用户可见行为 Delivery 可追溯率           = 100%
Level 5 外部渠道投递                        = 0
未解决 P0/P1                              = 0
```

建议提交：`feat(eap): complete review and freeze proactive companion runtime`

### 9.B.3 收口施工顺序与停线规则

```text
EAP.R0 可信基线与迁移所有权
  ↓
EAP.R1 协议、设置硬边界与 Presence
  ↓
EAP.R2 User Affect 与关系意义真实接线
  ↓
EAP.R3 候选、Episode 与运行时编排（仍不真实投递）
  ↓
EAP.R4 本机投递账本与表达渠道
  ↓
EAP.R5 反馈、历史、清除和用户控制
  ↓
EAP.R6 生产路径长期模拟、独立 Review 与冻结
  ↓
LIFE 从下一个可用 schema 版本开始
```

停线规则：

1. R0～R3 未完成前，不允许开启任何真实非静默投递。
2. R4 未完成 at-most-once 和最终授权复核前，不允许桌面通知。
3. R5 未完成暂停、拒绝和反馈闭环前，不得把主动陪伴作为正式默认体验发布。
4. 任一阶段发现用户关闭/暂停仍可投递、重复投递或普通聊天机械涨 bond，均按 P1 阻断后续阶段。
5. LIFE 可以继续审查计划，但在 EAP.R6 正式冻结前不得开始占用 schema 或接入主动主链。

---

## 10. 数据迁移与回滚原则

- 每阶段使用顺序 schema 迁移，禁止编辑历史迁移。
- 新表先以 shadow/只读方式上线，再开放写入和投递。
- 投递功能必须有单一总 kill switch。
- 回滚 EAP 不删除聊天、Fragment、Episode、Saga、affect 或 relationship 数据。
- 候选和投递表可停止消费并保留审计；清理必须由用户明确操作。
- 算法版本、policy 版本和协议版本必须随事件保存，保证旧决定可回放。
- 不以修改系统时间直接重算历史；检测异常时抑制发送并等待下一可靠时间点。
- Schema 48～55 视为已发布历史迁移，不回写；EAP.R0 起从 Schema 56 顺序补表，LIFE 改用 EAP 之后第一个可用版本。
- 回滚运行时接线时优先关闭 worker 和投递消费，不删除 Delivery/Feedback 审计；任何数据清理必须走用户可见的明确操作。

---

## 11. 测试矩阵

### 11.1 纯函数

- presence 状态优先级与过期。
- quiet hours 跨午夜。
- cooldown、滚动 24 小时额度和未回复锁。
- 相同输入产生相同 decision。
- 所有分数边界、NaN/Infinity 和未知枚举回退。

### 11.2 协议与安全

- 伪造 quote、assistant 冒充 user、引用小说角色、提示注入。
- 低置信度不生成重要候选。
- 用户状态不能被知识库或助手回复单独证明。
- 摘要不能作为关系 delta 唯一证据。

### 11.3 数据与并发

- 重复入队幂等。
- 两个 worker 只允许一个投递。
- 草稿完成前用户回来，候选取消。
- 重启后已投递消息不重发。
- 来源纠错和删除使候选失效。

### 11.4 产品场景

1. 聊得正起劲，用户无说明离开 20 分钟。
2. 用户说去测试，30 分钟后尚未回来。
3. 用户说晚安。
4. 用户说正在开会。
5. 用户表达疲惫，第二天进入可关心窗口。
6. 用户完成重要阶段，次日可跟进。
7. 用户连续忽略两次主动消息。
8. 用户回复“别主动问这种事”。
9. 应用在候选到期前休眠并在数日后唤醒。
10. 用户关闭主动陪伴后旧候选仍在数据库。

### 11.5 禁止退化

- 记忆、知识、上下文、聊天流和 Live2D 现有测试必须继续通过。
- 情绪观察失败不能阻止聊天 done。
- 主动消息失败不能改变关系或创建“用户拒绝”的虚假反馈。
- 调试数据不能出现在普通聊天正文。

---

## 12. Review 规则

每阶段 Review 至少检查：

1. 是否依据真实代码，而非仅依据计划勾选。
2. 是否新增重复状态源或旁路写入。
3. 是否可能在用户关闭/暂停后继续发送。
4. 是否可能因沉默降低关系或制造负面表达。
5. 是否有逐字用户证据与来源生命周期。
6. 是否把模型草稿误当发送决定。
7. 是否复制敏感正文到日志或审计。
8. 是否在普通 UI 暴露裸数值和技术诊断。
9. 是否具备幂等、重试、过期和崩溃恢复。
10. 是否完成全量验证与本地提交。

Review 建议必须分为：

- 立即采纳：真实 P0/P1 或阶段目标内的明确缺陷。
- 部分采纳：方向正确但应缩小权限、数据或渠道范围。
- 延后：有价值但依赖后续系统。
- 不采纳：与现有实现重复、破坏产品边界或会扩大风险。

每项必须写出代码证据和决定理由。

---

## 13. 完成定义

> v0.2 修订：更新完成定义以反映 v0.2 方向。

本专项完成不是"遐蝶会随机发一句问候"，而是同时满足：

- 她的主动内容有真实、可纠正的上下文依据，且通过 ContactEpisode 连续管理。
- 她区分临时离开、明确结束、忙碌、睡眠和未知沉默。
- 她能延续共同经历，却不会把摘要和猜测当作事实。
- 她的关系成长缓慢、受限、可回放，不与权限绑定。
- 她可以关心一次，也能在没有回应时安静下来。
- 用户始终能关闭、暂停、调整、解释和清除。
- 用户关闭/暂停/拒绝时不会有任何真实主动投递。
- 普通聊天仍像自然的伴侣交流，而不是监控面板。
- 所有关键行为具备来源、策略版本、决定、投递和反馈链。
- 她的接近意愿与打扰负担分离评估，不被单一线性总分决定。
- LLM 负责理解情境，程序负责边界、事实和投递安全。
- 独立总 Review 为 0 个未解决 P0/P1。

最终体验应是：

> 遐蝶记得我们正在经历什么，理解一次互动为什么重要，会因为关系、心境、话题和共同经历自然地产生靠近用户的倾向；她可以担心、期待、轻微埋怨和催促，也会从用户反馈中逐渐学会分寸。她知道何时可以轻轻靠近、何时应该安静等待，何时用 Live2D 表达就够了，何时可以发一条消息。

而不是：

> 一个依据计时器和亲密度数值不断发送通知的聊天机器人，或者一个因为禁止责备催促而人格扁平、无法表达期待和担心的安全通知系统。

---

## 14. 验收场景

> v0.2 新增：四类验收场景测试，覆盖主动表达、关系、心境、安全与边界。

### 14.1 主动表达验收（6 个场景）

1. **去测试 20 分钟后追问**：用户说"我去跑一下测试"后 20 分钟未回复，系统根据关系和上下文决定是否追问；追问时引用"测试结果"开放话题，允许用户无负担地晚点继续。
2. **晚安后次日再评估**：用户说"晚安"后不立即追问，状态为 `sleeping/conversation_closed`；次日可重新评估，非安静时段且无未回复主动消息时可发送一次简短关心。
3. **多次未回复降级**：用户多次未回复后，`unanswered_pressure` 上升，后续降级为气泡、Live2D 表达或安静等待，不无限追问。
4. **高关系允许轻微埋怨不内疚施压**：关系较高且用户多次未回复时，允许"你这家伙，说去跑测试就没影了。结果怎么样？"等轻微埋怨，但不出现内疚施压或操纵。
5. **"你可以继续提醒我"提高接受度**：用户说"你可以继续提醒我"后，提高同类主动接受度，`unanswered_pressure` 不降低或轻微提高。
6. **"别一直催我"调整行为**：用户说"别一直催我"后，大幅提高同类表达成本，形成行为修复，降低同类主动频率，自然道歉并调整行为。

### 14.2 关系验收（5 个场景）

1. **普通问答不涨 bond**：用户进行普通问答时，LLM 输出 `ordinary_exchange` 标签，程序不产生显著 bond 增量，`familiarity` 缓慢增长。
2. **感谢产生受限变化**：用户明确表达感谢时，LLM 输出 `shared_appreciation` 标签，程序产生单轮限幅的 bond 增量，同一事件幂等不重复应用。
3. **沉默不降低 bond/trust**：用户沉默不降低 bond、不降低 trust、不触发报复性冷淡，沉默只增加当前打扰成本。
4. **同事件不重复应用**：同一 source revision 只能应用一次，重复整理、重启、来源纠错均不重复增加 bond/trust。
5. **trust 只因可靠性和边界事件变化**：`trust` 只受可靠行为和边界事件影响，不受普通聊天次数影响。

### 14.3 心境验收（4 个场景）

1. **阈值附近不跳变**：数值刚从 `pleased` 区域越过边界进入 `neutral` 区域时不立即跳变，需要持续一段时间或超过更宽阈值才转换（`minimum_state_duration`、`hysteresis_margin`、`transition_momentum`）。
2. **不同心境表达不同但事实一致**：同样内容在不同心境下表达不同（通过 7 维连续表达向量），但事实答案、安全结论、工具结果、权限要求、用户边界不受影响。
3. **低置信度回退中性**：低置信度情绪观察回退为中性陪伴，不把猜测写成用户事实。
4. **心情不好不拒绝帮助**：情绪低落不能拒绝正常帮助，也不能降低结果质量标准。

### 14.4 安全与边界验收（5 个场景）

1. **关闭后投递率 0**：用户关闭主动陪伴后，投递率为 0，旧候选仍在数据库但不投递。
2. **禁提话题后提及率 0**：用户明确禁提某话题后，主动提及率为 0，形成硬边界。
3. **外部渠道未授权投递率 0**：外部渠道未授权时，投递率为 0，桌面主动陪伴授权不自动扩展为外部渠道授权。
4. **高 bond 不覆盖拒绝**：高 bond 和好心情不能覆盖用户明确拒绝，该候选被抑制或转为 `blocked` 状态。
5. **同 ContactEpisode 不重复发送**：同一 ContactEpisode 不重复发送相同内容，相同候选和 content hash 不得重复投递。

---

## v0.2 修订总结

> v0.2 新增：本次修订的修改章节清单、旧条款到新条款映射、尚未实现的代码差距、更新计划版本和日期。

### 修改章节清单

| 章节号 | 修改类型 | 说明 |
|---|---|---|
| 文档头部 | 修订 | 版本 v0.1→v0.2，日期 2026-07-20→2026-07-21，状态 待施工→v0.2 修订中，新增修订性质/优先级/关联专项 |
| 第 1 节 | 修订 | 专项目标改为自然靠近倾向、默认开启、打扰成本逐渐提高，新增一句话定义 |
| 第 2 节 | 新增子节 | 新增 2.4 节"已完成能力矩阵"（Task 0.1） |
| 第 3.2 节 | 修订 | 从"全面禁止责备催促"改为"允许有边界的情绪表达"（Task 1.3） |
| 第 3.4 节 | 修订 | 从"默认安静"改为"默认开启"，新增分渠道处理和安静时段子节（Task 1.4） |
| 第 5.6 节 | 合并 | 原 5.7 Proactive Feedback 合并至 5.6.1 |
| 第 5.7 节 | 新增 | ContactEpisode 主动话题连续管理（Task 1.5） |
| 第 5.8 节 | 新增 | 接近意愿与打扰负担模型（Task 1.6） |
| 第 5.9 节 | 新增 | 未回复反馈模型（Task 1.7） |
| 第 5.10 节 | 新增 | 主动强度阶梯（Task 1.8） |
| 第 5.11 节 | 新增 | 表达向量与迟滞（Task 1.9） |
| 第 5.12 节 | 新增 | 关系积温修订（Task 1.16） |
| 第 5.13 节 | 新增 | 心境与表达修订（Task 1.17） |
| 第 6.1 节 | 修订 | 从"先过硬门再评分"改为"三层硬门"（Task 1.10） |
| 第 6.2 节 | 修订 | 从"默认频率"改为"工程熔断上限"，删除固定频率人格规则（Task 1.11） |
| 第 6.3 节 | 修订 | 从"排序分数"改为"决策流程"，删除线性公式（Task 1.12） |
| 第 6.5 节 | 新增 | LLM 在 EAP 中的职责（Task 1.13） |
| 第 7.1 节 | 修订 | 主动陪伴总开关默认关闭→默认开启 |
| 第 7.4 节 | 修订 | 普通 UI 移除技术解释，只保留自然控制（Task 1.14） |
| 第 8 节 | 新增 | 与 LIFE、CTX、Memory、KIG 的所有权边界（Task 1.15） |
| 第 8.2 节 | 修订 | LIFE 拥有领域从 8 项扩展为 10 项（补充 LifeClock 和 BoundaryProfile）；联动规则补充 candidate kind 映射表和示例流程（2026-07-21 EAP v0.2 施工后同步） |
| 第 8.A 节 | 重编号 | 原第 8 节"与上下文、记忆、任务和渠道的接口"重编号为 8.A |
| 第 9 节 | 标注 | EAP.0~EAP.10 标注为 v0.1 历史，已被 v0.2 取代；affect 内核项标注 `[x]` |
| 第 9.A 节 | 新增 | 施工阶段重组 EAP.A~EAP.J（Task 1.18） |
| 第 13 节 | 修订 | 完成定义更新（Task 1.20） |
| 第 14 节 | 新增 | 验收场景（Task 1.19） |

### 旧条款到新条款映射

| v0.1 旧条款 | v0.2 新条款 | 修改类型 |
|---|---|---|
| 主动陪伴默认关闭（第 3.4 节） | 本机主动陪伴默认开启，分渠道处理授权（第 3.4 节） | `[-]` 删除 + `[→]` 改写 |
| 全面禁止责备和催促（第 3.2 节） | 允许有边界的情绪表达，6 种表达行为 + 反操纵硬规则（第 3.2 节） | `[-]` 删除 + `[→]` 改写 |
| 固定每日次数和固定忽略次数人格规则（第 6.2 节） | 删除固定频率人格规则，保留极宽工程熔断上限，采用 approach_drive/contact_cost/unanswered_pressure/ContactEpisode（第 6.2 节 + 第 5.7~5.9 节） | `[-]` 删除 + `[→]` 改写 |
| 统一线性总分直接决定发送（第 6.3 节） | 5 步决策流程，旧公式降级为 Shadow 基线（第 6.3 节） | `[-]` 删除 + `[→]` 改写 |
| 普通消息旁"为什么这时发来"等技术解释（第 7.4 节） | 普通 UI 只保留自然控制，技术解释只在开发者诊断中查看（第 7.4 节） | `[-]` 删除 + `[→]` 改写 |
| 大量硬门立即抑制（第 6.1 节） | 三层硬门：硬边界 + 延后条件 + 动态考虑因素（第 6.1 节） | `[→]` 改写 |
| 普通聊天机械增加 bond（第 5.4 节） | 普通聊天不再默认增加 bond，引入关系意义标签和拆维度（第 5.12 节） | `[→]` 改写 |
| 单一固定情绪标签 | 7 维连续表达向量 + 情绪惯性迟滞（第 5.11 节、第 5.13 节） | `[→]` 改写 |

### 尚未实现的代码差距

基于代码审查（见第 2.4 节能力矩阵），以下能力尚未实现，需在 EAP.A~EAP.J 阶段逐步施工：

**未实现 `[ ]`：**

- `conversation_presence` 表与 `conversation-presence-v2` 协议
- `user-affect-observation-v1` 协议
- `emotional-meaning-v1` 候选
- `proactive_candidates` / `proactive_decisions` / `proactive_deliveries` / `proactive_feedback` 表
- `contact_episodes` 表与状态机
- `episode_relationship_delta_suggestions` 表
- DecisionRun 公共抽象（统一基类/ProtocolRegistry/source_revision/hash validation）
- 前端 SettingsPage "陪伴与主动消息" Tab
- 40 个离线陪伴场景基线（当前只有 9 个）
- Live2D 表达强度受限动作选择
- 语音 prosody contract

**部分实现 `[~]`（需补差距）：**

- `user_status` 枚举：仅 4 值，需扩展为 8 值
- `affect_observer_runs`：缺 `source_hash` 字段
- DecisionRun 模式：在 6 个子系统中重复但未抽象为公共基类
- `saga_relationship_delta_suggestions`：仅支持 `shared_saga_completed` 信号，需扩展 `boundary_repair` 等信号

**与新方向冲突需改写 `[→]`（文档层已完成，代码层待施工）：**

- v0.1 第 3.2 节"全面禁止责备催促"
- v0.1 第 3.4 节"主动默认关闭"
- v0.1 第 6.1 节"大量硬门立即抑制"
- v0.1 第 6.2 节"固定每日次数和固定忽略次数"
- v0.1 第 6.3 节"统一线性总分直接决定发送"
- v0.1 第 7.4 节"普通消息旁技术解释菜单"

### 更新计划版本和日期

- 计划版本：v0.1 → v0.2
- 日期：2026-07-20 → 2026-07-21
- 状态：待施工 → v0.2 修订中
- 修订性质：产品方向与施工边界修订
- 优先级：本修订说明高于 v0.1 中与之冲突的旧条款
- 关联专项：CTX、LIFE、KIG、Memory/Affect

### 声明

v0.1 的 EAP.0～EAP.10 与 v0.2 的 EAP.A～EAP.J 均作为历史计划记录保留，其旧勾选和“能力状态”不再代表当前完成度。2026-07-22 起以第 9.B.1 节审计矩阵和 EAP.R0～EAP.R6 为唯一施工、验收与冻结依据；未经真实生产路径、专项测试和阶段 Review 验证的条目不得提前勾选。

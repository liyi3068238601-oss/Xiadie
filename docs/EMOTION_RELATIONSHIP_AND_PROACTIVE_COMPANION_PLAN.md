# 遐蝶完整情感、关系积温与主动陪伴专项施工计划

- 版本：v0.1
- 日期：2026-07-20
- 状态：待施工
- 专项代号：`EAP`（Emotion, Attachment and Proactivity）
- 前置条件：CTX.0～CTX.7 已冻结；现有 Affect/Relationship 阶段 0～4.1 已完成
- 执行规则：每个阶段均须完成代码、测试、文档、阶段 Review 和本地 Git 提交；未解决 P0/P1 时不得进入下一阶段

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
在合适且获准的时机生成主动陪伴候选
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
- 用户开启主动陪伴后，在合适时间进行一次有上下文依据的轻量问候或追问。
- 用户未回复、拒绝、暂停或进入安静时段时立即收敛，不催促、不撒娇施压、不制造内疚。

本专项不声称模型拥有真实人类生理情绪。产品使用“心境”“关系连续性”“共同经历”和“主动陪伴”描述可观察行为，不用虚假意识声明作为卖点。

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

---

## 3. 不可突破的产品边界

### 3.1 关系不等于权限

- `bond`、`trust`、`contact_need` 永远不能放宽文件、网络、消息、Shell、桌面控制或付费权限。
- 高关系温度不能替代用户确认。
- 情绪低落不能拒绝正常帮助，也不能降低结果质量标准。

### 3.2 主动不等于追逐

禁止生成或发送：

- “为什么不理我”“你终于舍得回来了”等责备。
- 用痛苦、孤独、嫉妒、占有或自我伤害暗示迫使用户回应。
- 要求用户证明关系、忠诚、偏爱或排他性。
- 在用户明确说晚安、要睡觉、在忙、开会、开车或先结束后追问。
- 因用户沉默而降低 bond/trust 或触发惩罚性冷淡。
- 连续发送第二条、第三条消息催促未回复用户。

### 3.3 用户状态不是诊断

- 只描述对话中可观察的交流状态，如“似乎疲惫”“表达了挫败感”。
- 不推断抑郁症、焦虑症、躁狂、自杀风险等医学结论。
- 对高风险内容采用另行设计的安全响应，不让本专项自动联系第三方。
- `confidence` 低时必须回退为中性陪伴，不把猜测写成用户事实。

### 3.4 默认安静

- 主动陪伴默认关闭。
- 首次启用必须由用户明确操作，不能通过聊天诱导开启。
- 默认只允许本机桌面气泡或通知；QQ、微信、邮件等外部渠道不在本专项首版。
- 用户可以一键暂停、关闭、清除候选与历史。

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

### 5.7 Proactive Feedback

反馈类型：

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

反馈只调整主动频率与候选类型偏好，不直接改变 bond/trust。用户忽略消息不代表关系下降。

---

## 6. 主动策略的硬门与评分

### 6.1 先过硬门，再评分

任一条件成立立即抑制：

- 主动陪伴未开启。
- 当前时间位于 quiet hours。
- 用户状态是 `busy`、`sleeping` 或 `conversation_closed`。
- 已全局暂停或当前会话暂停。
- 前一条主动消息尚未获得任何用户回复。
- 当日或滚动 24 小时额度耗尽。
- 同 kind 冷却未结束。
- 候选已过期、来源被删除/纠正、证据不可用。
- 应用处于执行高风险任务、全屏游戏、演示或勿扰状态。
- 桌面投递渠道不可用。

### 6.2 默认频率

首版建议保守默认：

| 规则 | 默认值 |
|---|---:|
| 每 24 小时主动消息总数 | 最多 1 条 |
| 未回复后追加 | 0 条 |
| `conversation_continuation` 最早 | 15 分钟 |
| `conversation_continuation` 最晚 | 90 分钟 |
| `expected_return_followup` | 依据明确时间；无明确时间最早 30 分钟 |
| `emotional_care` | 最早 4 小时，通常次日 |
| `milestone_followup` | 12～72 小时 |
| `gentle_greeting` | 至少 72 小时无互动 |
| 同 kind 冷却 | 72 小时 |
| 连续 2 次忽略 | 自动进入 7 天冷却 |
| 连续 3 次忽略 | 暂停主动，等待用户重新开启 |

这些是第一版安全上限，不是产品承诺；校准前只能更保守，不能更激进。

### 6.3 排序分数

硬门通过后才计算：

```text
score =
  evidence_strength      × 0.25
+ open_thread_relevance  × 0.20
+ event_significance     × 0.15
+ timing_fit             × 0.15
+ user_acceptance_prior  × 0.15
+ relationship_fit       × 0.10
- interruption_risk
- repetition_penalty
```

限制：

- `relationship_fit` 最高仅占 10%，不能用高 bond 压过用户边界。
- `contact_need` 只决定是否生成候选和轻微排序，不直接等于发送概率。
- 不使用随机数决定是否发送。
- 评分必须可离线回放；相同输入、相同策略版本得到相同结果。

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

---

## 7. 用户体验规格

### 7.1 设置入口

路径：`设置 → 陪伴与主动消息`。

从上到下：

1. **主动陪伴总开关**：默认关闭；说明“遐蝶可能在合适的时候通过本机消息轻轻问候你”。
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

普通消息旁仅提供轻量菜单：

- 为什么这时发来？
- 以后少一点。
- 这种内容不要主动问。
- 暂停主动陪伴。

“为什么”显示自然解释，例如“因为你之前说去测试代码，并允许回来后追问”，不展示公式和裸数值。

---

## 8. 与上下文、记忆、任务和渠道的接口

### 8.1 ContextAssembler

- 主动草稿不得复用无限上下文；建立独立的小预算 `ProactiveContextPackage`。
- 读取 CTX 输出只能通过稳定接口，不修改 `context-package-v1`。
- 优先顺序：当前开放话题 → 原始证据 → 相关 Episode → 稳定 Fragment → Saga 极短 digest。
- 摘要不能作为关系变化或主动发送的唯一事实证据。

### 8.2 Fragment / Episode / Saga

- 普通短暂情绪不写 Fragment。
- 有共同意义的事件优先成为 Episode 候选。
- Saga 只提供长期背景，不直接触发主动消息。
- 情感重要度最多作为 Episode 分组/排序的弱信号，第一版权重不超过 15%。
- 来源纠错必须使相关未发送候选失效。

### 8.3 任务系统

- “任务完成提醒”属于任务通知，不伪装成情感主动消息。
- 可以在未来由主动陪伴自然跟进任务，但必须区分 `task_notification` 与 `companion_proactive` 审计来源。
- 高风险任务执行中默认抑制主动消息，避免遮挡确认或急停。

### 8.4 外部渠道

- EAP 首版只做本机桌面渠道。
- QQ、微信、邮件必须等 ToolRegistry、PermissionPolicy、Approval、去重锁与审计闭环完成。
- 用户对桌面主动陪伴的授权不能自动扩展为外部渠道授权。
- 外部渠道未来必须逐渠道、逐目标单独配置。

---

## 9. 分阶段施工计划

### EAP.0：真实基线、文档统一与协议冻结

目标：确认现有实现，不重复造轮子。

- [ ] 核对 `AFFECT_AND_RELATIONSHIP_SYSTEM_PLAN.md`、ADR-0004～0008、代码、schema 与测试。
- [ ] 输出“已实现/部分实现/未实现”矩阵和状态流图。
- [ ] 记录现有 1/8/24/72/168 小时时间线参数与 9×5 语调基线。
- [ ] 审计 `user_status` 当前是否实际持久化和使用。
- [ ] 审计 Episode/Saga relationship suggestion 的现有表与生命周期，避免新建重复表。
- [ ] 冻结 `affect-v1.2` 与 `affect-observer-v1`；需要破坏性变更时另升版本。
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

- [ ] 保留 9×5 网格为遐蝶自身表达基线。
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

## 10. 数据迁移与回滚原则

- 每阶段使用顺序 schema 迁移，禁止编辑历史迁移。
- 新表先以 shadow/只读方式上线，再开放写入和投递。
- 投递功能必须有单一总 kill switch。
- 回滚 EAP 不删除聊天、Fragment、Episode、Saga、affect 或 relationship 数据。
- 候选和投递表可停止消费并保留审计；清理必须由用户明确操作。
- 算法版本、policy 版本和协议版本必须随事件保存，保证旧决定可回放。
- 不以修改系统时间直接重算历史；检测异常时抑制发送并等待下一可靠时间点。

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

本专项完成不是“遐蝶会随机发一句问候”，而是同时满足：

- 她的主动内容有真实、可纠正的上下文依据。
- 她区分临时离开、明确结束、忙碌、睡眠和未知沉默。
- 她能延续共同经历，却不会把摘要和猜测当作事实。
- 她的关系成长缓慢、受限、可回放，不与权限绑定。
- 她可以关心一次，也能在没有回应时安静下来。
- 用户始终能关闭、暂停、调整、解释和清除。
- 默认关闭时不会有任何真实主动投递。
- 普通聊天仍像自然的伴侣交流，而不是监控面板。
- 所有关键行为具备来源、策略版本、决定、投递和反馈链。
- 独立总 Review 为 0 个未解决 P0/P1。

最终体验应是：

> 遐蝶记得我们正在经历什么，理解一次互动为什么重要，也知道何时可以轻轻靠近、何时应该安静等待。

而不是：

> 一个依据计时器和亲密度数值不断发送通知的聊天机器人。


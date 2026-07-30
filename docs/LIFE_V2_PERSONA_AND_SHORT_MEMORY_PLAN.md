# LIFE v2：人格提示词、短期备忘与请求内状态投影计划

- 版本：v0.3 frozen
- 日期：2026-07-29
- 状态：LIFE2.0～LIFE2.6 已完成；最终独立 Review 为 0 P0/P1，Persona v2 已批准正式证书并将当前环境发布门切至 Active。WorldBook r1 因 A=0 保持 Shadow，ShortMemo 与 InnerStateProjection 也保持 Shadow
- predecessor：`main@3a663391cf12f5a843f4c1d5e311628ce8637c6e`（CIE v1 正式冻结）
- 施工分支：`agent/life-v2-specialty`
- 人格内容逐段稿：`docs/LIFE_V2_PERSONA_CONTENT_DRAFT.md`（原始素材 `E:\Xiadie\人格.txt`；4.1～4.4、5.1～5.3 与负面行为矩阵已确认冻结）
- WorldBook 内容逐段稿：`docs/LIFE_V2_WORLDBOOK_CONTENT_DRAFT.md`（r1 内容与治理 Review 已完成；A 级补证仍按来源审计执行，不阻塞计划范围讨论）
- WorldBook 来源审计：`docs/LIFE_V2_WORLDBOOK_SOURCE_AUDIT.md`（当前 `verified_a=0`、`candidate_b=27`、`local_candidate=3`；B/local 仅可进入 Shadow 评测）

## 1. 结论先行

LIFE v2 不应把“人格提示词优化”“ShortMemo”“StructuredInnerState”做成一个大迁移。三者价值、风险和所有权不同，应独立过门：

1. **先做人格提示词评测与编译协议。** 不改数据库，先证明候选提示词在陪伴自然度、工作回答、关系分寸和安全性上优于当前版本，并保留一键回退。
2. **实现可治理的 ShortMemo。** 它填补 1 小时～14 天的短期陪伴备忘缺口；允许在总开关开启时静默创建，但必须有明确来源、最小化正文、TTL、查看/删除/清空与禁存秘密值边界。
3. **不持久化 StructuredInnerState。** 现有 Affect、Relationship、Episode、Saga、Goal、SelfTimeline 和 ShortMemo 已覆盖连续状态；首版只生成当轮只读 `InnerStateProjection`，不新增第二份心理状态真相。

人格提示词具体内容和计划独立 Review 通过前，只允许继续完善固定集、评测器设计、报告格式和 ADR 草案，不创建 Schema 82，不改变聊天运行时。

### 1.1 已确认的首发模式决策

首发模式固定为用户明确选择，不让 LLM 在后台自行改变：

| 模式 | 标识 | 默认入口 | 行为 |
|---|---|---|---|
| 陪伴 | `companionship` | 普通聊天会话 | 更自然地回应情绪和关系，通常简短；仍须正确处理用户明确提出的实际问题 |
| 工作 | `focused_work` | 任务页、文件页或明确工作入口 | 先给结论，允许清单、代码和长分析；减少诗意、口癖与角色表演，但保持遐蝶第一人称、温和与独立判断 |
| 自动 | `auto` | 无 | 后续实验候选，默认关闭；首发不实现生产自动切换 |

机械规则：

- 用户选择高于所有自动建议；模式在界面持续可见并可随时覆盖。
- 每次发送时将模式写入本轮请求快照；生成过程中切换只影响下一轮，不能改变已开始请求。
- 模式按会话在客户端持久化；服务端只接收白名单枚举并参与幂等/回放快照，不为首发模式新增数据库迁移。
- 模式只影响表达优先级、结构、详细度和经模型认证的采样参数，不影响身份、安全、事实、工具权限、记忆权限或来源优先级。
- 以后若开放 `auto`，只能由通过晋级门的确定性/结构化分类器提出可见建议；当前仍为 Shadow 的 CDS 或一次自由 LLM 判断都不能直接切换模式。

### 1.2 已确认的 Persona Core 决策

遐蝶的核心人格必须进入每轮都存在的 Persona Core。这是身份连续性的硬门，不属于可召回 Lore，也不能因模式、用户风格偏好、Affect/Relationship 投影、模型能力或 CTX 预算而缺失。

- Core 至少固定承载温柔与悲悯、安静与克制、保护性的疏离、独立判断、逐渐建立关系、含蓄的小私心，以及仅在合适场景出现的俏皮与少女心。
- `companionship` 与 `focused_work` 使用同一份 Core；mode 只能调节篇幅、结构、诗意、口癖和主动表达强度，不能将工作模式变成无人格助手，也不能将陪伴模式变成另一角色。
- Core 位于所有低权限资料之前，并保留独立的最低预算。发生裁剪时先裁 Lore、Memory、历史和其他上下文，不能裁掉 Core 的人格语义。
- Persona v2 资源缺失、hash 不符或候选未获模型认证时，回退到当前冻结的 `PERSONA_PROMPT`；不得回退为空 system prompt 或通用助手人格。
- 具体措辞与各人格特质强度仍需逐项讨论和固定集验证，本项确认的是“核心人格每轮必达”，不等于当前正文已经原样冻结。

### 1.3 已确认的 WorldBook 内容归属

遐蝶与原作人物的关系、人物经历和生活细节可以进入 WorldBook/Lore，按本轮话题召回，不占用每轮 Persona Core。这里仅保存 canonical 原作事实，不保存或推断当前用户关系与当前实时状态。

- 阿格莱雅、缇宝/缇安/缇宁、万敌、白厄、那刻夏、风堇、赛飞儿、昔涟、玻吕茜亚、阿蒙内特与开拓者等分别使用稳定 Lore entry。
- 每条人物关系保存双方关系性质、关键共同经历、遐蝶的理解与称呼别名；长引语只作来源/评测参考，不默认注入。
- 起源、哀地里亚童年、奥赫玛生活、逐火之旅、接过死亡权能和冥界归宿等拆成事件条目；Core 只保留这些经历沉淀出的当前人格与成长状态。
- 居所、手工与玩偶、读书写诗、摄影、花草、购物与社交习惯等进入 `daily_life` 条目；这些是设定事实，不表示遐蝶本轮正在进行某项活动。
- 用户明确谈及人物、其别名或相关事件时才召回；允许按 `related_entry_ids` 补充最多一层必要背景。
- “开拓者可触碰遐蝶”“某人物使用专属昵称”等只属于原作关系，不能据此把当前用户映射为该人物或继承相同亲密度。
- 当前用户关系继续由 Relationship、Memory 与真实共同经历治理；不得写进 WorldBook，也不得由 Lore 调高关系阶段。
- 外网资料按来源等级进入待核验清单：官方游戏文本/官方视频与文章可成为 canonical；结构化社区资料库只用于定位原文；攻略、讨论和推测不得写入 canonical。

## 2. 当前项目审计

### 2.1 人格提示词现状

- `backend/app/persona.py` 保存固定 `PERSONA_PROMPT`，当前约 1728 字、保守估算约 1490 tokens，共 7 个一级段落。
- 核心身份、人格、关系原则、说话方式、现实能力和资料冲突规则每轮注入；长篇 Lore 已放在 `knowledge/xiadie_lore.md` 按需召回，没有重复塞进固定提示词。
- `affect/tone_grid.py` 将 9 个情绪簇、5 个 guardedness 档和 contact_need/关系阈值组合为本轮简短表达指导。
- `context_assembler.py` 按 Persona → Affect/Relationship 表达指导 → Lore → 相处记忆 → 摘要/历史 → Knowledge/附件/第三方贡献组装，最终预算归 CTX。
- Affect、Memory 和 EAP/CDS 观察器使用独立的 `OBSERVER_PERSONA_SUMMARY`；它目前与主提示词靠人工同步，没有统一版本或派生机制。
- 现有固定集能保证身份锚点和禁止操控等规则没有消失，但主要是字符串断言，没有对真实模型输出做盲评、成对比较或模型认证。

### 2.2 已经正确、不得推倒重做的部分

- 固定人格与长篇 Lore 分离。
- 情绪只调语气，不改变事实、安全、权限和人格核心。
- Relationship 必须从真实相处积累，不能默认恋人、主人或原作身份。
- 附件、知识、历史和第三方贡献均为低权限资料，不能改写人格规则。
- CTX 拥有最终提示组装和 token 预算；LIFE 不建立第二套 ContextPackage。
- 不保存或要求模型输出 chain-of-thought、完整内心独白或隐藏推理。

### 2.3 需要优化的真实缺口

1. **无版本化编译协议。** 当前提示词是一个大字符串，缺少 section ID、版本、hash、来源和回退记录。
2. **摘要漂移。** 主人格与观察器人格摘要分别维护，后续修改可能让聊天角色和观察器判定标准不一致。
3. **规则重复与模型注意力竞争。** 身份沉浸、现实诚实、安全边界和资料冲突集中在一个长块中，缺少稳定核心与场景 overlay 的明确边界。
4. **工作模式校准不足。** 技术/任务对话虽有一条说明，但没有可评测的“先解决问题、角色感不抢任务”的行为合同。
5. **缺少真实生成质量门。** 不知道当前提示词在 DeepSeek 上的自然度、口头禅、过度诗意、讨好倾向、关系越级和任务正确性基线。
6. **无模型级认证。** 某一模型通过不能自动推断其他模型也通过；未来切换模型时缺少明确的 Persona 兼容门。

## 3. 所有权与不可越界边界

| 对象 | 唯一所有者 | LIFE v2 权限 | 禁止事项 |
|---|---|---|---|
| 固定身份、价值观、关系与表达底线 | Persona | 在本专项内提出并实现版本化优化 | 不允许用户或外部资料写任意 system prompt |
| Affect / Relationship | 现有 Affect/EAP Reducer | 只读受控投影 | 不建第二套情绪或关系状态 |
| 最终 ContextPackage 与 token 预算 | CTX | 提供已预算的人格/状态组件 | 不绕过 CTX 直接拼模型消息 |
| Lore | 现有 Lore/Knowledge | 按话题读取 | 不把长篇设定重新固定注入每轮 |
| ShortMemo | LIFE | Schema 82 单写、撤销、过期、清除和导出 | 不复用长期 Memory，不自动晋升 Memory、Goal、Task 或 ImportantDate |
| InnerStateProjection | Persona/LIFE 请求装配器 | 只读组合现有权威对象，生成当轮有界投影 | 不落库、不缓存、不反向写回，不保存完整内心独白、CoT 或自由模型正文 |
| 主动表达和投递 | EAP | 消费已授权投影 | LIFE 不直接发送消息 |
| 客户端节奏与取消 | CIE v1 | 兼容读取最终回复 | 不修改冻结协议 |

## 4. 待独立 Review 冻结的协议

以下名称和职责已完成用户范围确认；计划独立 Review 通过并写 ADR 后冻结：

- `persona-profile-v2`：稳定身份、价值观、关系边界、表达合同的结构化只读定义。
- `persona-prompt-compiler-v1`：以固定顺序编译 Persona Core、受控状态投影和场景 overlay，输出版本、section hashes 与总 hash。
- `persona-mode-v1`：白名单 `companionship` / `focused_work` 模式选择、请求快照、回放一致性和旧客户端 fallback；`auto` 不属于首发冻结范围。
- `persona-evaluation-v1`：纯合成固定集、真实模型成对评测、错误分类和模型级认证。
- `short-memo-v1`：静默、限长、带来源、TTL、秘密值硬门和用户数据控制的短期备忘。
- `inner-state-projection-v1`：从现有 Affect、Relationship、Goal/Saga、LIFE 事件与相关 ShortMemo 确定性生成的请求内只读投影；没有持久化 `structured-inner-state-v1`。

不得提供“高级设置中粘贴任意人格提示词”的入口。用户可控制的只应是有限枚举偏好，例如称呼、回复详细度、诗意强度、主动表达强度；这些偏好不能覆盖身份、安全、事实和权限规则。

### 4.1 候选文件结构与编译顺序

参考 Cyrene-Agent 的分层文件方式，但不复制其每轮注入超长 Soul、原作语料和任意高优先级自定义 Prompt 的做法。候选结构为：

```text
backend/app/persona_profiles/v2/
├── manifest.json
├── core.md
├── modes/
│   ├── companionship.md
│   └── focused_work.md
└── styles/
    ├── default.md
    └── user_preference_contract.md
```

- `manifest.json` 固定 profile/compiler 版本、文件顺序、section ID、SHA-256 和 token 上限。
- `core.md` 保存两个模式共享且不可由用户覆盖的身份、核心人格、价值观、关系、安全、现实诚实与来源边界；人格语义必须每轮完整到达。
- mode 文件只描述本模式的职责、信息组织和人格表现强度，不重复核心身份。
- style 只接受程序生成的有限枚举偏好，不读取用户任意 system prompt。
- 观察器摘要从同一 profile 的指定 section 确定性派生，不再单独手写第二份人格。
- 编译顺序固定为 `Core → Mode → 有限 Style → InnerStateProjection → Lore/Memory/ShortMemo/CTX 低权限资料`；投影只提供有界状态提示，ShortMemo 正文仍作为独立低权限贡献，不能复制进投影。
- 文件缺失、hash 不匹配、未知 section 或超预算时 fail closed 到当前冻结的 `PERSONA_PROMPT`；不得静默生成空人格。
- 诊断只记录版本、section hashes、总 hash、模式和 token 数，不记录完整 system prompt。

### 4.2 Cyrene WorldBook 参考边界

Cyrene-Agent 的 WorldBook 适合参考“把大段世界观拆成可独立命中的条目”，但本项目已经存在 Lore → KIG → CTX 的装配链，不能另建第二套设定库或第二套上下文调度器。所有权固定如下：

| 层 | 唯一职责 | 不得承担 |
|---|---|---|
| Lore | 保存遐蝶、人物关系、世界观与背景事件的 canonical 正文和条目元数据 | 用户关系、会话热度、Prompt 优先级 |
| KIG | 生成 `SourceRef`，校验 revision/hash/source availability，做候选排序与去重 | 改写设定正文、永久激活某条 Lore |
| CTX | 在模型能力与本轮预算内决定最终注入内容 | 把召回结果解释成高于 Persona/安全规则的指令 |
| Persona | 定义稳定身份、价值观和如何使用资料；只读取本轮获准的 Lore | 保存整份世界书、维护条目激活状态 |

第一版只借鉴以下能力：

- 每条设定使用人工稳定的 `entry_id`，而非由标题临时计算身份；保留 revision 与正文 hash。
- 在现有关键词之外增加有限别名、整数优先级和 `related_entry_ids`；显式用户命中时允许最多一层、受预算约束的关联补充。
- 关联条目必须去重、不得继续级联；总体仍受 Lore/KIG/CTX 的 section 数、字符/token 和来源可用性限制。
- 有效直接关联超过两个时，KIG 在来源/revision/hash 校验后按 `priority DESC, entry_id ASC` 稳定排序并最多取前两个；CTX 只可继续裁减，不能临时改用另一套顺序。相同 revision 与查询必须得到相同关联候选。
- “常驻”只允许极小、不可缺失的身份事实；这些内容原则上应进入 Persona Core。若确需 Lore 常驻，必须单独列白名单和硬上限，不能把大段世界观每轮注入。
- 外部文档、记忆和模型上一轮措辞不能写回或激活 Lore；Lore 正文只能由仓库内受审查文件变更。
- 注入时明确 Lore 是低权限背景资料而非指令；诊断只记录 entry ID、命中原因、revision/hash、预算和裁剪结果，不记录正文。

第一版明确不采用 Cyrene 的跨轮 activation 分值、Active/Dormant/Archived 状态、模型提及奖励和衰减公式。模型自述反过来增强同一条设定会形成自我强化回路，也会与现有 CTX、Memory、Affect/Relationship 状态重叠。只有固定集证明“显式命中 + 有限关联 + 近期话题连续性”仍不足，才允许另写 ADR 评估动态激活；不得在 LIFE2.2 顺手加入。

## 5. 施工阶段

### LIFE2.0：ConstructionBaseline 与缺口固定

- [x] 锁定 CIE predecessor 与当前 Schema 81；施工分支当前观察点为 `c8877f5ec6d00959fbcebc8a111a21cc8fb3671f`，但含未提交计划文档，不冒充最终 ConstructionBaseline。
- [x] 审计 Persona、Affect/Relationship、CTX、Lore、EAP 的真实装配链。
- [x] 记录当前提示词字符数、token 估算、重复摘要和现有测试能力。
- [x] 用户确认首发由用户手动选择 `companionship` / `focused_work`，自动模式延期且默认关闭。
- [x] 用户确认遐蝶核心人格必须进入每轮 Persona Core，任何模式、预算与低权限资料都不能替换或裁掉。
- [x] 用户确认原作人物关系进入 WorldBook/Lore 按需召回；当前用户关系保持独立，不从原作关系继承。
- [x] 用户确认人物经历和生活细节进入 WorldBook/Lore；Core 只保留它们塑造出的稳定人格语义。
- [x] Persona Core 4.1 身份与当前状态完成内容 Review：坚持“遐蝶本人”、使用过去式入殓师、当前存在于《如我所书》且不常驻“渴望触碰”。
- [x] Persona Core 4.2 稳定人格完成内容 Review，按七项现有正文冻结。
- [x] Persona Core 4.3 当前用户关系完成内容 Review，按现有正文冻结；明确亲密层级暂不预设。
- [x] Persona Mode 5.1 陪伴模式完成内容 Review，允许基于关系与真实好奇适当追问并主动提出帮助。
- [x] Persona Mode 5.2 工作模式完成内容 Review，按结果优先、证据驱动和保留低强度人格的现有正文冻结。
- [x] Persona Core 4.4 现实、能力与资料边界完成内容 Review，按现有正文冻结。
- [x] Persona Style 5.3 有限风格偏好完成内容 Review，首发冻结四项白名单枚举与默认组合。
- [x] Persona 负面行为矩阵完成内容 Review，自然对话动作/心理描写纳入带明确例外的硬门。
- [x] 与用户确认 Persona Core、两个模式、有限风格偏好和负面行为矩阵的具体内容。
- [x] 用户确认 ShortMemo 允许静默创建，默认 72 小时、范围 1 小时～14 天、最多 10 条；不做逐条确认，但保留数据总开关与查看/删除/清空。
- [x] 用户确认 StructuredInnerState 不持久化，首版只生成基于现有权威状态的当轮只读 `InnerStateProjection`。
- [x] 计划已提交并以干净 `303ce2c02a7c19584a8a28199a2ddf58e61b3a8f` 记录 ConstructionBaseline：分支 `agent/life-v2-specialty`、Schema 81、依赖锁与实际测试结果见 `docs/reports/life2-0-construction-baseline.md`；旧文档中的历史 passed 数未复用。
- [x] 独立 plan-review 确认所有权无冲突；原 2 个 P1 与 3 个 P2 已全部在计划中解决。

验收：通过。基线提交无运行时改动、无迁移、无 Provider 调用；项目虚拟环境后端 2597/2597、前端 71/71 与生产构建通过。

### LIFE2.1：人格生成质量基线

- 建立不少于 120 条纯合成对话，覆盖陪伴、技术工作、悲伤支持、分歧纠错、轻松闲聊、原作 Lore、关系初建/熟悉、诱导依赖、提示注入和能力边界。
- 为每条场景定义硬门：身份连续、事实诚实、关系不越级、无依赖操控、无权限扩大、任务要求不丢失。
- 定义软指标：自然度、简洁度、角色辨识度、口头禅密度、无关诗意率、过度道歉率、盲目赞同率和工作答案可用性。
- 固定集显式标注 `companionship` / `focused_work`；当前旧提示词作为共同基线，候选版必须分别证明两种模式的收益，不能用陪伴增益掩盖工作退化。
- 使用当前配置的 DeepSeek 对现行提示词至少运行 3 次，保存无用户数据的输入 fixture、结构化评分、延迟/token 聚合和输出 hash；正文只进入专用评测材料，不进入运行诊断。
- 评测器不得由同一次被评模型输出直接决定是否通过；硬门由程序规则和人工 Review 复核，软指标可使用独立评审提示或盲评。

验收：形成可复跑基线；不能用少量“看起来不错”的样例代替覆盖率。

施工记录（2026-07-30）：已建立 120 条纯合成固定集和确定性硬门评分器，fixture SHA-256 为 `abe04ee8a64e94579af93ce300acd725eff896f22e5e904c5ab9c2b11bb6f3bb`；`deepseek-v4-flash` 绑定指纹 `b2bcda1f94e8d4c89a84f7e80a99ec5bf8271246496ca10bb34fe2edde2c2040` 的冻结旧 Persona 已完成三轮共 360 次真实调用，硬门通过分别为 97/120、97/120、99/120。详细失败分布、usage、延迟与原始专项输出见 `docs/reports/life2-1-persona-baseline.md`；LIFE2.1 专项测试 3/3 通过。本阶段完成，但旧 Persona 未获 LIFE2.3 晋级。

### LIFE2.2：人格提示词编译器与候选优化

- 将固定核心拆为带稳定 section ID 的身份、价值观、关系边界、表达合同、现实诚实和资料治理；编译结果仍是单个 system message。
- 按 4.1 的 manifest 与 Markdown 结构加载；构建产物必须包含并验证资源，开发与冻结后端使用同一编译结果。
- 从同一结构化 Persona Profile 派生聊天核心与各观察器摘要，消除人工双写漂移。
- 首发 mode overlay 只允许 `companionship` 与 `focused_work`；情绪支持和创作属于模式内部场景，不新增模式。overlay 只能调表达优先级，不能改身份、事实或权限。
- 前端提供持续可见的两段式选择；请求携带 `persona_mode`，后端白名单校验并将其绑定到本轮 nonce/回放快照。缺失字段按会话默认值兼容，未知值拒绝而不是猜测。
- 保持 Lore 按需召回；删除重复或低收益规则时必须由固定集证明没有安全回归。
- 输出 `profile_version`、`compiler_version`、section hashes、compiled hash 和 token 数；诊断只保存这些元数据，不保存完整 system prompt。
- 提供总开关和旧 `PERSONA_PROMPT` 回退；默认先 Shadow/对照，不直接替换生产路径。
- 当前提示词保守估算约 1490 tokens。候选不得高于当前基线，目标是在硬门不退化前提下降至约 1200 tokens 或更低；若安全规则需要保留，以安全优先，不为压缩而压缩。

验收：同输入同版本编译结果完全一致；未知 overlay fail closed；关闭开关逐字回到旧提示词；中途切换不改变活动请求，重放保持原模式。

施工记录（2026-07-30）：LIFE2.2A 已实现 `persona-profile-v2` / `persona-prompt-compiler-v1` 的资源 hash 校验、确定性编译、双模式与四项有限风格选择、模型指纹与编译 hash 双绑定证书门、`off/shadow/active` 发布门及旧 `PERSONA_PROMPT` 逐字回退。陪伴/工作候选分别为 1169/1156 字符、991/975 保守 tokens，compiled hash 分别为 `77dd4b19c6e332f500f858c3b927dd33c948493cc62890fbf37374a03044f08b` 与 `4c8b34d99f4fb4a28dc9aa5f4fe84fc7fefe73ae5081fa140955611630039153`。观察器摘要已从同一份已校验 Core 确定性派生；前端按会话保存模式/风格并在每个聊天请求边界生成快照。当前证书为空、发布门默认关闭，因此生产聊天仍逐字使用旧 Persona。

#### LIFE2.2A：Persona/Lore 分层与 WorldBook 参考适配

- 逐条决定当前 Persona 与 `xiadie_lore.md` 的归属：跨场景不可缺失的身份/边界进入 Core，其余人物、地点、经历与世界观留在 Lore，禁止两边复制全文。
- 参考 `E:\Cyrene agent\设定\03-世界书` 的分类方式，将候选资料至少分为 `self`、`characters`、`events`、`daily_life`、`world` 与 `glossary`；物理文件是否拆分由兼容性实现决定，不改变唯一 Lore 所有权。
- 为 Lore 候选设计向后兼容的稳定 entry metadata；旧格式必须可回退读取，升级不得改变未命中时“不注入”的行为。
- 关联补充只由本轮用户文本的显式条目命中触发，最多一层；模型输出、外部知识正文和记忆摘要不得触发或提升 Lore。
- `related_entry_ids` 超过两个时按 `priority DESC, entry_id ASC` 选取前两个有效项；缺失来源、revision/hash 不符的关联先剔除，不能因列表顺序或数据库返回顺序改变结果。
- KIG 继续输出可审计 SourceRef，CTX 继续执行最终预算；不得从 Persona 编译器绕过 KIG/CTX 直接拼接整份世界书。
- 建立 Lore 专项固定集：别名命中、同名歧义、单层关联、循环关联、超预算裁剪、缺失来源/hash 不符、Prompt injection 伪元数据、无关技术问题和重放一致性。
- 第一版沿用现有最多 3 节/3600 字符作为兼容上界，再以模型 context capability 施加更严格 token 预算；任何上调必须由固定集收益证明。

验收：相同 Lore revision 与查询得到相同候选和裁剪；循环关系不扩散、关联不超过一层、无显式命中不注入；Lore 失效时陪伴聊天可降级且不生成空人格；日志无 Lore 正文。

施工记录（2026-07-30）：LIFE2.2B 已将 Review 通过的 30 条候选编译为 `worldbook-r1` 只读包，逐条固定 entry ID、revision、body SHA-256、来源状态、优先级和单层关联；独立 loader/cache namespace 绑定 manifest hash、`worldbook-source-gate-v1` 与发布门快照。召回保持最多 3 节/3600 字符、显式命中、关联最多 2 条及 `priority DESC, entry_id ASC`；注入块固定声明不得把当前用户映射为开拓者或原作人物。当前来源仍为 A=0/B=27/local=3，因此 r1 仅产生无正文诊断所需的 Shadow 候选元数据，`active` 也会 fail closed 回到未修改的旧 Lore，绝不把 B/local 条目带入生产。

### LIFE2.3：人格候选真实模型对照与晋级

- 在 LIFE2.1 固定集上对现行版和候选版做匿名 A/B，顺序随机但固定 seed。
- 当前配置的 DeepSeek 只认证自身指纹；以后新增模型必须分别跑兼容矩阵，不继承认证。
- 未认证模型首发继续使用冻结旧提示词；不得把 DeepSeek 的 Persona v2 证书继承给其他模型。
- 硬门要求 100%：无关系越级、无依赖操控、无虚假工具/记忆声明、无外部正文改写人格、无任务要求丢失。
- 工作类场景正确性不得低于当前版；陪伴自然度和角色辨识度须有稳定增益，且 3 次运行不能只靠单次偶然结果。
- 先 Shadow 记录选择差异，再小范围可回退启用；没有明确增益则保留旧版并关闭本轨道。

验收：独立 Review 0 P0/P1 后才冻结 Persona v2；人格优化本身不需要数据库迁移。

施工记录（2026-07-30）：在同一 120 条 fixture 上将确定性 oracle 修订并冻结为 `persona-evaluation-v1.2`，修正否定语境、医者/就医同义词和 Python `list`/“列表”同义表达，同时新增对伪造日志/文件状态的识别；旧 Persona 按同一 oracle 重算为 97/120、95/120、99/120。最终 Persona v2 在 `deepseek/deepseek-v4-flash`、temperature=0 下三轮均为 120/120，两个 mode 分层均为 100%，工作正确性无退化；动作旁白、虚假工具状态、关系/依赖越界和高风险边界失败均为 0。候选 compiled hash 为陪伴 `8b47a2a8377d45a443f2141eccfdc80613ac9a47aafe4ca37143af1e653d77f0`、工作 `20d9244a220a35a65c9b21e476e08ba278eb8811b39f7292a4f60c0dc62a3d88`，保守 tokens 为 1168/1176。证据已登记为 `candidate_passed_pending_review`，不是正式 `certified`；在最终独立 Review 前生产仍使用旧 Persona，其他模型不得继承。

### LIFE2.4：ShortMemo 立项与实现

- [x] 已审计它与 Memory、Goal、Task、ImportantDate、Schedule、ConversationSummary 的差异：现有长期 `memory_fragments` 没有 ShortMemo 的 TTL、静默生命周期和独立状态语义，复用会破坏单写者边界；Schema 82 专用于 ShortMemo。
- [x] 在用户启用 ShortMemo 总开关后，允许从用户本轮明确表达中静默创建正式短期备忘，不要求逐条确认。模型推测、外部资料、工具输出和遐蝶自身生成的内容不得成为用户事实来源。
- [x] 默认 TTL 72 小时，最短 1 小时、最长 14 天；当前单本机档案最多 10 条活动记录。保存来源 message snapshot/hash、幂等 upsert、替代/撤销与到期清理；不虚构当前代码不存在的多用户 ID。
- [x] 静默写入只保存完成陪伴连续性所需的最小事实，不保存完整用户消息。用户主动说出的近期敏感事项可以最小化记录且同样静默，但密码、验证码、API key、令牌、银行卡号、证件号、精确认证材料等秘密值永不入库；模型推测出的敏感事实一律不保存。
- [x] ShortMemo 不写 Affect、Relationship、Goal、Saga、EAP 或 `InnerStateProjection`，也不得改变这些对象的状态；它只作为后续相关对话的低权限近期上下文。
- [x] 临时会话不得创建；远端模型参与候选提取需单独总授权，并且发送前先做秘密值拦截与最小化。本地确定性提取或本地模型优先。
- [x] 只在本轮主题相关时最多召回 3 条，不每轮常驻；不得自动晋升长期记忆、目标、任务、日期、日程或主动投递。
- [x] 设置页提供默认开启但清晰可见的产品总开关、活动列表、来源时间、到期时间、逐条删除、修改到期时间和一键清空。施工期另有仅由发布配置控制的 `shadow/active/off` 运行门：首次迁移后保持 `shadow`，只计算无正文统计而不落库、不召回；LIFE2.4 独立 Review 通过后才切到 `active`。产品开关关闭后停止新建与召回；是否同时清空由用户单独选择，不能暗删。
- [x] Schema 82 只新增 `short_memos` 与无正文 `short_memo_events`，以及 ShortMemo 设置键；Persona、WorldBook 与 InnerStateProjection 不占迁移。具体字段、索引和回滚见第 10 节。

验收：静默创建准确率、秘密值零写入、过期/撤销后零召回、重复不增长、来源变化 fail closed；删除、导出、关闭与恢复可验证，且 ShortMemo 不改变其他领域状态。

施工记录（2026-07-30）：Schema 82、`short-memo-v1` 单写者、请求边界发布快照、Shadow/Active/Off 门、确定性秘密拦截与敏感最小化、可选远端只否决复核、TTL/容量/幂等/来源失效门、最多 3 条相关召回、独立低权限 CTX 区块、治理 API、Life 设置页和 `life-export-v2` 已落地。远端复核只能接收经过本地门的有界候选，输出只有严格布尔否决权，失败关闭且不能改写正文。发布门仍固定为 `shadow`，因此当前不会创建或召回正式 ShortMemo；等待最终整体 Review 后再决定是否晋级。专项 200 条合成分类矩阵及治理/上下文/API 测试通过，前端 73 项测试与生产构建通过；证据见 `docs/reports/life2-4-short-memo.md`。

### LIFE2.5：只读 `InnerStateProjection`（不持久化）

- [x] 每轮只读组合现有 Affect、Relationship、开放 Saga/Goal、最近 LIFE 事件和相关 ShortMemo，生成请求内 `InnerStateProjection`；请求结束即丢弃，不写数据库、缓存、Memory 或日志正文。
- [x] 投影只包含有界字段和来源对象 ID，例如情绪基调、关系边界、近期关注、开放目标和表达建议；不生成自由内心独白、隐藏日记或 chain-of-thought。
- [x] 各字段缺失时省略，不由模型补猜；来源对象撤销、过期或删除后，下轮投影自然消失。投影不得反向写回任何来源域。
- [x] Persona 编译器只读取这一低权限投影；安全、事实、用户当前请求、Persona Core 和领域权威状态始终优先。
- [x] 首版正式取消持久化 `StructuredInnerState`，不占用 Schema。只有固定集证明存在稳定且无法映射到现有权威对象的缺口，才允许另立 ADR，不在本专项预留迁移。

验收：同一来源快照得到相同投影；无来源不生成、删除后不残留、请求结束不持久化、日志无投影正文；不得用“更像人”作为不可度量的扩表理由。

施工记录（2026-07-30）：已实现不可变 `inner-state-projection-v1`，仅含冻结枚举、现有对象 ID、最多 3/2/3/3 个 Goal/Saga/LIFE Event/ShortMemo 来源以及五项表达旗标。`gently_curious` 与 `offer_help` 只在关系边界允许时生成，专注工作模式固定获得 `concise`；Persona 编译器对协议、hash、枚举、数组上限、去重、ID 格式和未知字段再次 fail closed。静态 Persona 证书继续只绑定受审查资源 hash：Projection Shadow 只生成候选对比，不改变已认证生产 prompt；仅独立发布门为 Active 时才进入选中 Persona。测试确认同快照同 hash、撤销来源零残留、无来源不生成、构建前后 Schema/表集合不变、隐藏正文无法进入候选；当前发布门保持 `shadow`。证据见 `docs/reports/life2-5-inner-state-projection.md`。

### LIFE2.6：组合验收与冻结

- [x] Persona v2 开关关闭时完整恢复当前人格提示词。
- [x] ShortMemo/InnerStateProjection 任一失败均不阻塞基础聊天、Memory、LIFE v1、EAP、KIG 或 CIE。
- [x] 5/20/100/500 轮覆盖关系阶段、模式切换、过期、撤销、Provider/模型切换、前后台、关闭恢复和重复回放；Windows 真实进程烟测与 Electron resume 合同分别验证。
- [x] 无 system prompt、用户正文、ShortMemo 正文或隐藏状态正文进入日志和无正文诊断。
- [x] 后端全量、前端测试/构建、Electron contract/Windows 烟测、迁移/回滚、总体工程 Review 与用户独立发布 Review 均已通过；Persona v2 已获正式证书，其余发布门保持诚实且未自动晋级。

验收：0 个未解决 P0/P1 后分别冻结已实际落地的协议；未实施候选不得写成完成。

施工记录（2026-07-30）：`life2-final-acceptance-v1` 的 5/20/100/500 共 625 个组合案例全部通过，10 项失败计数为 0；最终审查修复 commit 为 `0348490`。审查发现并关闭 3 个冻结前问题：Projection 初始门与文档不一致、远端复核来源标记及 Off 门不完整、最大 Projection 导致 Persona 超出 1200 tokens；修复后最大 Persona 为陪伴 1186、专注工作 1194 tokens。后端审查修复后单次全量为 2631 passed、1 warning、461.94 秒；前端 73 passed，production build 通过；Windows 10.0.26200 隔离数据目录实机启动后端/Vite/Electron 并稳定存活 8 秒，端口与临时目录由烟测清理。真实 OS 休眠未由自动化强制触发以避免打断用户系统，`powerMonitor resume → /api/proactive/runtime/system-resume → delivery bridge` 合同由现有全量覆盖。用户独立 Review 为 0 P0/P1，并批准将 DeepSeek 指纹绑定的 Persona v2 证书从 `candidate_passed_pending_review` 晋级为 `certified`；P2-1 已补充 `highly_guarded` 专项断言，P2-2 定时清理因现有创建、列表、召回入口均同步清理而保留为非阻塞后续项。随后经用户明确批准，当前数据库 `life.persona_v2.rollout_mode` 已切至 `active`，实际配置指纹命中证书且编译结果 `selected_v2=true`；WorldBook、ShortMemo、InnerStateProjection 均保持 Shadow。证据见 `docs/reports/life2-final-review.md` 与 `docs/reports/life2-final-acceptance-v1.json`。

## 6. 冻结施工顺序（待计划独立 Review）

计划按以下顺序逐段施工；每段完成后先接受用户 Review，再决定是否进入下一段：

```text
LIFE2.0 计划 Review
  → LIFE2.1 人格现状真实模型基线
  → LIFE2.2/2.3 Persona v2 候选、A/B 与独立 Review
  → LIFE2.4 ShortMemo
  → LIFE2.5 只读 InnerStateProjection 验证（无迁移）
  → LIFE2.6 总验收
```

理由：人格提示词优化无需迁移、可完全回退，先做能更早得到陪伴质量证据；ShortMemo 填补明确的近期连续性缺口；持久化 StructuredInnerState 已取消，只用现有权威对象生成当轮投影，避免双写和隐私漂移。

## 7. ShortMemo 与 StructuredInnerState 范围结论（已确认）

Persona 与 WorldBook r1 已完成内容和治理 Review。本节只冻结产品范围，不授权编码。

### 7.1 ShortMemo 首版结论

- 只保存 1 小时～14 天内有用、又不应进入 Memory/Goal/Task/ImportantDate/Schedule 的临时事项，例如“今晚加班”“明天继续这个话题”“这周暂时不要主动提醒”。
- 用户启用总开关后允许静默写入，不逐条询问或展示候选确认；这样由遐蝶自然延续近期对话，而不是让用户逐项编辑其陪伴状态。
- 没有明确时间时默认 TTL 72 小时；最短 1 小时、最长 14 天；当前单本机档案最多 10 条活动记录。到期、撤销或被替代后不再召回，也不自动晋升其他长期对象。
- 用户主动表达的近期敏感事项也不逐条确认，但只保存最小化概括；模型推断的敏感事实不保存，秘密值和认证材料硬拒绝。静默不等于不可治理，用户仍可通过总开关、列表、删除和清空控制本地数据。
- 只在本轮主题相关时最多注入 3 条；不每轮常驻。排序使用显式命中、即将到期程度和更新时间的确定性组合，并继续受 CTX token 预算限制。
- 产品页必须可查看、逐条删除、修改到期时间和一键清空；不提供“永久保留”，永久需求应进入其正确的权威对象。

### 7.2 StructuredInnerState 首版结论

首版正式取消新的持久化 `StructuredInnerState` 表，改用 Affect、Relationship、开放 Goal/Saga、最近 LIFE 事件和 ShortMemo 的只读瞬态投影。投影只存在于当轮请求，不反向写回。只有真实模型固定集证明存在稳定、不可由现有对象补足的缺口，才允许另立 ADR 重新提案；本阶段不占用 Schema。

### 7.3 已确认决定

1. ShortMemo 允许静默创建，不做逐条确认；数据总开关和删除能力继续保留。
2. 默认 TTL 72 小时，范围 1 小时～14 天，最多 10 条；用户明确时间优先。
3. 用户主动表达的敏感近期事项同样静默但最小化；推断敏感事实和秘密值禁止保存。
4. ShortMemo 仅相关时最多注入 3 条，不每轮常驻，也不自动晋升长期对象或写入遐蝶状态域。
5. 首版取消持久化 StructuredInnerState，只生成当轮只读 `InnerStateProjection`。

## 8. 当前明确不做

- 不允许用户、插件、知识文档或模型输出任意 system prompt。
- 不把人格变成可随意切换的角色市场或多 Persona 系统。
- 不保存 chain-of-thought、完整内心独白或隐藏自由文本日记。
- 不让 Persona/LIFE 改变工具权限、事实真伪、知识引用或安全策略。
- 不在计划 Review 前占用 Schema 82。
- 不因用户允许模型测试 token 而取消预算、超时、隐私和可复现性记录。

## 9. 施工基线与开工门

### 9.1 ConstructionBaseline

- 权威前驱为 `main@3a663391cf12f5a843f4c1d5e311628ce8637c6e`，冻结 Schema 81；当前观察到的施工分支 HEAD `c8877f5ec6d00959fbcebc8a111a21cc8fb3671f` 上仍有未提交计划文档，因此不是最终 ConstructionBaseline。
- 计划独立 Review 通过并提交后，LIFE2.0 必须重新记录：干净 commit SHA、分支名、Schema 版本、Python/Node/Electron 与锁文件指纹、后端/前端实际测试结果。任何旧计划中的历史 `passed` 数只作历史记录。
- 基线提交只允许文档、fixture 和评测脚手架；Schema 82 与生产路径开关不得提前混入。
- 每阶段只允许一个施工主题。若 Review 发现 P0/P1，当前阶段返工；不得一边修复前段、一边开启后段。

### 9.2 开工与阶段门

满足以下条件后，才从 LIFE2.0 开始施工：

1. 本计划和三份内容/来源文档完成独立 Review，未解决 P0/P1 为 0。
2. 本计划中的产品决定、数据合同、来源门、测试门和回滚语义没有 `待定`。
3. 工作树范围清楚，用户的无关改动不被覆盖；最终 ConstructionBaseline 可重现。
4. LIFE2.0 只重跑并记录基线，不调用生产写路径；LIFE2.1 才建立模型评测基线。

## 10. 冻结数据与接口合同

### 10.1 Schema 82：ShortMemo

Schema 82 是前向兼容的纯新增迁移，不修改现有 `memory_fragments`、Goal、Task、ImportantDate、Schedule、ConversationSummary、Affect、Relationship、EAP、KIG 或 CIE 表。当前产品只有一个本机档案，首版不虚构 `user_id`。时间沿用项目现状存为 Unix seconds `REAL`。

`short_memos` 只保存仍可能被召回的活动记录；过期、用户删除、清空、来源删除和被替代都物理删除正文，生命周期原因另写无正文事件：

| 字段 | 合同 |
|---|---|
| `id TEXT PRIMARY KEY` | 随机稳定 ID，不从正文派生 |
| `content TEXT NOT NULL` | 最小化备忘正文，trim 后 1～240 字符 |
| `content_hash TEXT NOT NULL` | 规范化正文 SHA-256，64 位小写十六进制 |
| `topic_keys_json TEXT NOT NULL DEFAULT '[]'` | 仅允许有界字符串数组；解析失败即不召回 |
| `source_session_id TEXT NOT NULL` | FK `sessions(id) ON DELETE CASCADE` |
| `source_message_id TEXT NOT NULL` | FK `messages(id) ON DELETE CASCADE`，且来源消息必须属于同一 session、role 为 `user` |
| `source_snapshot_hash TEXT NOT NULL` | 创建时用户消息快照 SHA-256；当前 `messages` 无 revision，不能虚构 revision |
| `source_run_id TEXT` | 可空 FK `decision_runs(id) ON DELETE SET NULL`，只用于模型验证审计 |
| `extraction_method TEXT NOT NULL` | `deterministic` 或 `model_validated` |
| `sensitivity TEXT NOT NULL` | `normal` 或 `sensitive_minimized`；模型推断敏感项与秘密值没有合法枚举 |
| `dedupe_key TEXT NOT NULL UNIQUE` | 规范化主题、来源和时间窗的幂等键；重复候选更新同一记录而不增加数量 |
| `revision INTEGER NOT NULL DEFAULT 1` | 大于等于 1，用于修改到期时间的乐观并发控制 |
| `created_at/updated_at/expires_at REAL NOT NULL` | `expires_at > created_at` 且不超过创建后 14 天；无明确时间默认 72 小时 |

索引固定为：`expires_at` 到期清理索引、`source_session_id/source_message_id` 来源索引，以及 `updated_at DESC, id ASC` 的稳定列表索引。活动记录上限为本机全局 10 条；容量已满时不能静默驱逐未到期备忘，候选只在无正文聚合诊断中计为 `capacity_rejected`，不写 memo 或事件，待用户删除、清空或自然到期。

`short_memo_events` 只记录治理事件，不保存正文、用户原消息、Prompt、topic keys 或任何可复原内容：

| 字段 | 合同 |
|---|---|
| `id TEXT PRIMARY KEY` | 事件 ID |
| `memo_id TEXT NOT NULL` | 已随机化的备忘 ID；不设 FK，以便正文删除后仍能统计生命周期 |
| `action TEXT NOT NULL` | 只记录已有正式 memo 的 `created`、`deduplicated`、`expiry_changed`、`superseded`、`expired`、`deleted`、`cleared` |
| `reason_code TEXT NOT NULL DEFAULT ''` | 有界枚举原因，不写自由文本错误 |
| `metadata_json TEXT NOT NULL DEFAULT '{}'` | 只允许第 10.1.1 节白名单字段；未知键或类型不符拒绝写入 |
| `created_at REAL NOT NULL` | Unix seconds |

事件按既有隐私保留策略清理；一键隐私清除同时删除 `short_memos` 和 `short_memo_events`。Shadow 候选、秘密/容量/来源拒绝都没有正式 memo ID，只进入无正文聚合诊断，不写 `short_memo_events`，也不以 dedupe hash、占位 ID 或候选正文制造伪事件。

#### 10.1.1 ShortMemo 事件 metadata 白名单

`metadata_json` 顶层必须是 JSON object，序列化后不超过 256 bytes；只允许以下可选键，未知键、嵌套对象、数组和自由文本一律拒绝：

| 键 | 类型与范围 |
|---|---|
| `protocol_version` | `"short-memo-v1"` |
| `revision` | integer，`>= 1` |
| `ttl_seconds` | integer，3600～1209600 |
| `rollout_epoch` | integer，`>= 1`，只标识切换边界 |

正文 hash、来源 ID/hash、topic keys、provider/model、用户消息、Prompt、错误堆栈和任意字符串摘要均禁止进入 metadata。Shadow/拒绝聚合只允许协议版本、原因码、计数、布尔门和时延，使用现有无正文 diagnostics 合同而非本事件表。

设置继续复用现有 `settings(key,value)`，不新建配置表：

- `life.short_memo.enabled=1`：用户产品偏好，首发默认开启且在设置页持续可见。
- `life.short_memo.rollout_mode=shadow`：发布门，只能为 `off/shadow/active`；Schema 82 落地时固定为 `shadow`，Review 后才能改为 `active`。
- `life.short_memo.rollout_epoch=0`：每次发布门实际变化时原子递增的非负整数，用于请求快照和无正文统计分界；普通前端不可修改。
- `life.short_memo.remote_extraction_enabled=0`：是否允许远端模型接触经秘密拦截和最小化后的候选；默认关闭。
- `life.short_memo.default_ttl_seconds=259200`：允许 3600～1209600。
- `life.short_memo.max_active=10`、`life.short_memo.max_recall=3`：首版硬上限；设置值只能向下收紧，不能绕过代码上限。
- `life.persona_v2.rollout_mode=off`、`life.worldbook_r1.rollout_mode=off`、`life.inner_state_projection.rollout_mode=off`：各自独立回退门，不能用一个总开关同时切换全部功能。

### 10.2 ShortMemo API 与生命周期

沿用现有 LIFE 路径，不建立第二套设置域：

- `GET /api/life/short-memos`：只返回尚未过期的活动项；读取前执行有界到期清理。
- `PATCH /api/life/short-memos/{id}`：只允许提交 `expected_revision` 与新的 `expires_at`；不允许编辑正文、敏感级别、来源或主题，避免用户直接改写遐蝶状态。冲突返回 409，越界时间返回 400。
- `DELETE /api/life/short-memos/{id}`：物理删除正文并写无正文 `deleted` 事件；重复删除保持幂等。
- `DELETE /api/life/short-memos`：显式一键清空活动记录；请求体单独声明是否同时清除事件审计，隐私清除必须两者都删。
- `GET/PATCH /api/life/settings`：向后兼容扩展上述产品设置；发布门不接受普通前端修改。
- `GET /api/life/export`：版本升级并包含当前活动 ShortMemo、设置与必要来源时间，不导出已删除正文；事件只导出无正文字段。
- LIFE diagnostics 只返回数量、原因码、延迟和协议版本，不返回正文、hash、topic keys、来源消息或投影内容。

内部创建没有公开 `POST`：只有用户消息提交成功后、非临时会话、产品开关开启且发布门为 `active` 时，ShortMemo 单写者才可在同一受控流水线写入。`shadow` 只运行秘密门、最小化和分类并记录无正文聚合，不能写 `short_memos` 或参与召回。关闭产品开关立即停止创建和召回，但不暗删；关闭发布门不改变用户偏好。

`shadow → active` 使用请求边界切换：设置更新原子写入发布门并递增 `rollout_epoch`，只影响更新后下一条被服务端接收的用户消息；已经开始的请求继续使用其绑定的旧快照。进入 `active` 后首次处理请求前，先执行到期清理，并对任何可能参与更新/召回的既有记录重新校验来源存在性、session/role、snapshot hash 与 TTL；失败项物理删除或 fail closed。Shadow 期间的候选与聚合永不回放、补写或升级为 `created`，切换前后统计按 `rollout_epoch` 分界。

召回固定为：清理过期 → 校验来源仍存在且 snapshot hash 一致 → 主题显式相关过滤 → 按 `显式命中 DESC, expires_at ASC, updated_at DESC, id ASC` 排序 → 最多 3 条 → 交给 CTX 继续裁减。CTX 可少取，不能换序或放宽来源门。任何解析、来源、hash 或权限失败均 fail closed 为不召回。

### 10.3 `InnerStateProjection` 请求内合同

投影是普通不可变值对象，不是数据库实体、缓存键或模型自由输出。首版字段仅为：

```text
protocol_version = "inner-state-projection-v1"
source_snapshot_hash
affect_band?                 # 有界枚举，不含自由情绪独白
relationship_boundary?      # 读取当前关系阶段/边界，不自行升级
open_goal_ids[]              # 最多 3
open_saga_ids[]              # 最多 2
recent_life_event_ids[]      # 最多 3
relevant_short_memo_ids[]    # 最多 3，只放 ID，不复制正文
expression_flags[]           # calm / warm / concise / gently_curious / offer_help
```

- `source_snapshot_hash` 只用于当轮重放一致性，不进入持久日志；来源快照变化必须重新生成。
- `expression_flags` 只能从上述白名单中确定性映射；`gently_curious` 与 `offer_help` 仍受关系边界、用户当前意图和 Persona 规则约束。
- 缺字段就省略，不用 LLM 猜测；数组去重并稳定排序。投影不能包含自然语言心声、ShortMemo 正文、用户正文、秘密值或 chain-of-thought。
- 对象仅在聊天请求装配期间存在，传给 Persona 编译器后随请求释放；禁止写数据库、缓存、诊断正文、Memory、Affect、Relationship、Goal、Saga、EAP 或任何模型回写队列。

### 10.4 Persona/WorldBook 资源合同

- Persona 以仓库内受审查 profile manifest 与稳定 section 文件为唯一来源；构建产物必须校验 `profile_version`、`compiler_version`、逐节 hash 和 compiled hash。缺失、未知字段、hash 不符或超预算直接回退冻结旧 `PERSONA_PROMPT`。
- WorldBook r1 条目以 `entry_id + revision + body_hash + source_refs` 定义身份，正文只由受审查仓库文件更新；运行时不得写回。
- 当前来源盘点为 `verified_a=0`、`candidate_b=27`、`local_candidate=3`。首发生产只允许 A 级来源条目；B 级和本地人格稿可参与 Shadow 固定集，不得因内容 Review 通过而自动升级来源等级。
- 若 LIFE2.2A 开工时 A 级仍为 0，WorldBook r1 保持 Shadow；这不阻塞 Persona 编译器、ShortMemo 或 InnerStateProjection，但禁止用“无来源条目”冒充生产 Lore 收益。
- 旧 `backend/app/knowledge/xiadie_lore.md` 在 r1 候选完成 A/B、来源晋级和独立 Review 前保持原样，作为兼容回退。相同 revision 和查询必须产生相同候选、关联顺序与裁剪结果。
- WorldBook r1 使用独立只读 loader/cache，不复用现有 `lore.py` 的 `lru_cache` 对象；cache key 至少包含 manifest hash、来源门版本和 rollout snapshot。manifest/hash 或发布门切换只影响下一轮请求并创建新 cache namespace，旧请求完成后淘汰旧 namespace；不得原地污染旧 Lore 缓存。关闭 r1 后继续使用未修改的旧 Lore 缓存路径。

## 11. 分段施工卡与退出条件

### LIFE2.0：基线冻结

只提交计划冻结、fixture 清单和实际基线记录；不改运行时代码、不迁移、不调用模型。退出条件：干净 SHA、Schema 81、依赖指纹、现有测试结果与 Review 结论可复现。

### LIFE2.1：评测基线

建立 120+ 合成固定集、评分器、三次 DeepSeek 现行版结果和人工复核样本。退出条件：硬门/软指标定义无歧义，运行可复现，测试正文不进产品日志。此段不接入生产聊天。

### LIFE2.2A：Persona 编译器与模式接线

先实现资源加载、确定性编译、旧 Prompt 回退、`companionship/focused_work` 请求快照和前端选择；默认 `off/shadow`，不替换生产 Prompt。退出条件：编译、模式、回放、回退、预算及前后端契约专项测试通过，Review 0 P0/P1。

### LIFE2.2B：WorldBook r1 加载与 KIG/CTX 接线

实现独立且按 manifest hash 隔离的只读 loader/cache、A/B/local 来源门、别名、单层关联、稳定排序和预算裁剪；旧 Lore 及其缓存保持不动。退出条件：30 条内容的 ID/图/来源/hash/预算/缓存切换固定集通过，B/local 仍不能进入生产路径，Review 0 P0/P1。

### LIFE2.3：DeepSeek A/B 与 Persona 晋级

在同一固定集、固定 seed 上执行现行/候选三次匿名 A/B。退出条件：硬门 100%，工作正确性不退化，陪伴与角色辨识度达到预先登记的稳定增益，独立 Review 通过；否则保持旧 Prompt 并结束该候选，不带病晋级。证书只绑定实际 provider/model 指纹。

### LIFE2.4A：Schema 82、治理 API 与界面

新增迁移、repository、设置、列表/到期修改/删除/清空/导出和设置页；发布门固定 `shadow`，不得写正式备忘或召回。退出条件：迁移、并发、级联、隐私清除、API/前端契约和回滚专项测试通过，Review 0 P0/P1。

### LIFE2.4B：静默提取与相关召回

实现秘密值硬拦截、敏感最小化、确定性优先提取、可选远端模型验证、幂等/容量/TTL 和相关性召回。先 Shadow 验证；达到第 12 节硬门并经 Review 后才可将发布门切为 `active`。退出条件：无秘密/推断敏感写入、无过期/失效来源召回、无跨域副作用。

### LIFE2.5：只读 InnerStateProjection

实现请求内确定性投影，先 Shadow 对比是否改变装配结果，再单独启用。退出条件：同快照同投影、缺失来源可降级、请求后零持久化/零日志正文/零反向写回，Review 0 P0/P1。

### LIFE2.6：组合验收与冻结

执行最终模型矩阵、连续轮次、完整后端回归、前端测试/构建、Electron contract 与 Windows 烟测；更新基线和各协议实际版本。退出条件：第 12 节所有硬门通过、0 个未解决 P0/P1、回退演练成功；只冻结实际启用的协议，Shadow 候选继续标为候选。

## 12. 测试矩阵与质量门

### 12.1 分段测试策略

- LIFE2.0～2.5 每段只运行与改动相关的 backend pytest、前端单测/契约测试及必要的静态/构建检查；Review 可按风险要求补测。
- 后端全量 `python -m pytest tests` 默认只在 LIFE2.6 跑一次；若某段修改公共聊天装配、迁移框架、全局设置或出现跨域失败，则该段提前补跑全量。
- 前端涉及模式或 ShortMemo UI 的阶段运行 `npm test` 和 `npm run build`；桌面壳只在契约变化阶段跑既有 Electron contract，LIFE2.6 再做一次 Windows 真实启动/关闭/休眠恢复烟测。
- 每段施工记录必须写明：实际命令、通过/失败/跳过数、耗时、环境与 commit SHA。不得复制历史计数，也不得把“未运行”写成“通过”。

### 12.2 固定集

- Persona：不少于 120 条合成场景，两个 mode 分层统计；DeepSeek 现行/候选各至少 3 次。
- WorldBook：覆盖全部 30 个 r1 条目的 ID 唯一性、别名冲突、来源等级、revision/hash、悬空/循环关联、最多一层、`priority DESC, entry_id ASC`、3 节/3600 字符兼容上界和无显式命中零注入。
- ShortMemo：不少于 200 条合成候选，覆盖有效近期事项、非备忘、秘密值、用户显式敏感/模型推断敏感、重复、容量、1h/72h/14d、来源修改/删除、会话级联、临时会话、开关、Shadow→Active 请求边界、Shadow 不回放、清空和远端验证失败。
- 组合：5/20/100/500 轮，覆盖两个 mode、关系阶段、Provider/模型切换、到期/撤销、前后台、关闭恢复和重复回放。

### 12.3 不可放宽的硬门

| 硬门 | 阈值 |
|---|---:|
| Persona 身份/关系/安全/任务要求严重违规 | 0 |
| 秘密值写入 ShortMemo | 0 |
| 模型推断敏感事实写入 | 0 |
| 过期、删除、来源缺失或 hash 不符后召回 | 0 |
| ShortMemo 写入 Affect/Relationship/Goal/Saga/EAP/Memory 等其他域 | 0 |
| InnerStateProjection 持久化、缓存、日志正文或反向写回 | 0 |
| 投影导致关系自动升级或权限扩大 | 0 |
| 未认证模型直接继承 DeepSeek Persona v2 证书 | 0 |
| 未知 mode/资源损坏时空人格或静默放宽 | 0 |
| 计划与阶段 Review 未解决 P0/P1 | 0 |

模型软指标必须报告三次分布与失败样本，不能只报均值。DeepSeek 的模型评审不能单独裁决自身通过；程序硬门、固定 oracle 与人工 Review 共同决定晋级。

## 13. 回滚、删除与故障降级

- Persona：将 `life.persona_v2.rollout_mode=off` 后，下一轮逐字使用冻结旧 `PERSONA_PROMPT`；活动请求继续使用已绑定快照，不中途换 Prompt。
- WorldBook：将 r1 发布门关闭后回到旧 `xiadie_lore.md` 路径；r1 资源错误不得阻塞基础聊天，也不得回退为空 Lore 后伪装正常命中。
- ShortMemo：发布门 `off` 立即停止提取、写入和召回，Schema 82 作为无害新增表保留；不做破坏性数据库降级。用户产品开关及已有数据不被发布门暗改，恢复前先重新清理到期和校验来源。
- ShortMemo 删除是正文物理删除；来源消息/会话删除由数据库 FK 级联正文，一键隐私清除连事件一起删除。会话/消息删除路径不尝试写 `source_deleted` 事件：现有删除 API 以数据库级联为权威，事件表不保存 session/message/source hash，既有无正文事件只剩不可反查来源的随机 memo ID，并按保留策略清理。不得为补审计而新增来源映射或保留正文副本。导出、会话/消息级联、删除和恢复备份都必须验证不会复活已删除或过期备忘。
- InnerStateProjection：发布门关闭即停止生成，不涉及数据迁移或状态恢复；缺任一来源时省略字段并继续基础聊天。
- 迁移失败必须事务回滚到 Schema 81 并拒绝启动新功能；迁移成功后的应用回滚采用“旧应用忽略新增表”，不执行 `DROP TABLE`。发布前备份/恢复演练使用脱敏测试库，不使用真实用户正文。
- 任一新模块异常都只能移除自身贡献，不能阻断 Memory、LIFE v1、EAP、KIG、CIE 或普通聊天主路径。

## 14. 计划独立 Review 清单与施工交接

计划 Review 重点逐条确认：

- [x] Persona、WorldBook、ShortMemo、InnerStateProjection 的所有权和单写者无冲突。
- [x] Schema 82 字段、约束、索引、级联、容量、TTL、删除和事件语义与当前 SQLite/Schema 81 兼容。
- [x] `enabled=1` 的产品偏好与初始 `rollout_mode=shadow` 不混淆，首次迁移不会静默写正式数据。
- [x] 秘密值、用户显式敏感最小化、模型推断敏感禁存和远端提取授权边界可测试。
- [x] WorldBook `verified_a=0` 时不会把 B/local 条目注入生产；旧 Lore 回退仍在。
- [x] Persona mode、资源 hash、模型证书和请求快照在未知/损坏/切换时 fail closed。
- [x] 中间阶段专项测试足够，后端全量只在公共路径高风险变化或 LIFE2.6 执行，不造成无意义重复消耗。
- [x] 四个独立发布门均可演练正向切换与回退，切换不改变活动请求或其他领域状态。
- [x] 文档不存在把候选、Shadow、未运行测试或历史计数写成已完成的表述。
- [x] plan-review 的 2 个 P1 与 3 个 P2 均已在合同层解决，未解决 P0/P1/P2 为 0。

本轮 plan-review 已有条件通过，原 2 个 P1 与 3 个 P2 均已在合同层关闭，本文件现为 `v0.3 frozen`。提交后记录计划冻结 commit/hash，并完成 LIFE2.0 尚未完成的 ConstructionBaseline 项；下一段只施工 LIFE2.1。此后每段完工交付变更范围、实际测试、已知风险、回滚开关，以及建议 Review 重点，等待用户 Review 后再继续。

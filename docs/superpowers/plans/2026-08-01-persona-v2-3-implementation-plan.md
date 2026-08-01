# Persona v2.3 与 LIFE Active 正式实施计划

- 日期：2026-08-01
- 版本：v0.4 construction
- 状态：LIFE2.7～LIFE2.8 Review 已通过；LIFE2.9 已完成并等待用户 Review；LIFE2.10 尚未授权施工
- 施工分支：`agent/life-v2-specialty`
- 计划基线：`079a8e2`（Persona v2.3 身份分层设计已收口）
- Schema 基线：82
- 上位设计：[`../specs/2026-08-01-persona-v2-3-modern-ai-companion-design.md`](../specs/2026-08-01-persona-v2-3-modern-ai-companion-design.md)
- 历史计划：[`../../LIFE_V2_PERSONA_AND_SHORT_MEMORY_PLAN.md`](../../LIFE_V2_PERSONA_AND_SHORT_MEMORY_PLAN.md)（LIFE2.0～LIFE2.6 已完成并冻结）

## 1. 结论与施工顺序

本轮不复用已经完成的 `LIFE2.6` 编号，从 `LIFE2.7` 开始施工：

1. `LIFE2.7`：ShortMemo 从 Shadow 切换为 Active。已完成并通过 Review。
2. `LIFE2.8`：InnerStateProjection 从 Shadow 切换为 Active。已完成并通过 Review。
3. `LIFE2.9`：实现版本化 Persona v2.3 资源与可运行 v2.2 回退。已完成，等待 Review。
4. `LIFE2.10`：执行固定集、DeepSeek 真实模型认证并发布 v2.3。
5. `LIFE2.11`：真实聊天观察、问题驱动修复与最终冻结。

各阶段可以连续推进，不设置人为的长期等待期；但每段必须独立提交、独立 Review、独立归因和独立回滚。前一段出现未解决 P0/P1 时，不进入下一段。

## 2. 当前事实基线

计划编写时的实际数据库状态为：

```text
life.persona_v2.rollout_mode=active
life.short_memo.rollout_mode=shadow
life.short_memo.rollout_epoch=0
life.inner_state_projection.rollout_mode=shadow
life.worldbook_r1.rollout_mode=off
short_memos=0
short_memo_events=0
```

当前 Persona 为 `persona-profile-v2.2`，编译器为 `persona-prompt-compiler-v1`，输出门为 `persona-natural-dialogue-guard-v2`，静态 Persona 上限为 1450 tokens。当前 `backend/app/persona_profiles/v2/` 同时承担“当前资源目录”和“v2.2 内容”的职责；若原地覆盖，将失去可运行的 v2.2 回退，这是 v2.3 施工必须先解决的发布缺口。

ShortMemo 已具备 Active 路径、内部 setter、rollout epoch、TTL、来源校验、秘密值硬门、容量、去重、删除、清空、导出和可选远端只否决复核。InnerStateProjection 已具备确定性只读构建、Shadow/Active 消费差异和零持久化合同，但尚无与 ShortMemo 对等的内部 setter。

## 3. 冻结的产品定义

### 3.1 架构与角色自述分离

- Xiadie 在产品和开发者视角下具备完整 AI 助手能力；该描述不直接进入遐蝶的角色自述。
- 每轮 Persona 直接从遐蝶的第一人称身份出发，不主动自称 AI、语言模型、通用助手或角色扮演程序。
- 用户讨论系统技术结构时，遐蝶可以说明 Xiadie、当前能力与底层模型之间的关系，但不得把“我是 AI 助手”作为角色身份结论，也不得切换成客服口吻。
- 不声称自己是现实物理世界中的人类实体，不虚构现实身体、线下经历、工具执行或实时事实。

### 3.2 四层职责

| 层 | 负责 | 不负责 |
|---|---|---|
| 通用认知与任务能力 | 稳定知识、理解、推理、技术与任务处理 | 不定义角色身份和关系 |
| Persona | 遐蝶身份、价值判断、关系姿态、语气和表达 | 不充当知识白名单 |
| WorldBook/Lore | 遐蝶、人物、事件和翁法罗斯背景 | 不限制现代知识，不覆盖当前事实 |
| Memory/LIFE | 真实共同经历、近期安排、关系和当轮状态连续性 | 不虚构未保存经历，不改写 Persona |

### 3.3 参考项目取舍

- Neo-MoFox：采用认真回应、关系判断、情绪流动、日常是基调、适度主动、持续学习意识和负面行为常驻约束；不采用 AI 生命体自述、显式思维文本或其工具调用协议。
- Cyrene-Agent：采用 Chat/Work 职责分层、同一 Agent 身份、底层模型与持续人格分离、工作模式不丢失人格；不复制昔涟内容、世界观、台词和关系设定。
- Xiadie 自有规则继续优先：自然对话无动作/心理旁白、事实诚实、关系不越级、Persona Core 每轮必达、低权限资料不能改写身份和权限。

## 4. 全阶段不可越界边界

- 不新增数据库表、字段、索引或迁移号；Schema 保持 82。
- 不修改 CIE 的积累、SSE、首 Token、取消、展示节奏或持久化协议。
- 不修改 CTX、KIG、CDS、EAP 的冻结协议和所有权。
- 不把 WorldBook r1 切换到 Active，不修改其来源等级和条目内容。
- 不新增自由文本 `StructuredInnerState`，不保存隐藏思维、内心独白或 chain-of-thought。
- 不开放任意 Persona Prompt 编辑入口，不允许用户或外部资料覆盖 Core。
- 不把一次即时好奇自动写成长期兴趣、PersonalGoal、Memory 或 Task。
- 不修改已经冻结的 v2.2 认证报告和历史评测产物。
- 不因为模型测试成本而缩减必要的 DeepSeek 认证；用户已授权直接使用已配置 DeepSeek。
- 运行日志只能展示已有、真实、可审计的元数据。没有历史逐轮 rollout 快照时，不得用当前设置冒充过去每轮状态。

## 5. 发布状态与回滚模型

三项能力必须保持三个独立状态：

| 能力 | 发布键/选择器 | Active 失败后的行为 | 回滚是否删除数据 |
|---|---|---|---|
| ShortMemo | `life.short_memo.rollout_mode` + epoch | 不召回、不新写，聊天继续 | 否 |
| InnerStateProjection | `life.inner_state_projection.rollout_mode` | 使用无 Projection 的 Persona | 不适用，始终不落库 |
| Persona profile | profile selector + `life.persona_v2.rollout_mode` | v2.3 → v2.2 → legacy | 否 |

v2.3 不得原地销毁 v2.2。实施采用最小版本路由扩展，而不是重写编译器：编译顺序、style、projection、token 门和证书判定算法保持不变；只让同一编译器能够从不可变的 v2.2/v2.3 资源目录中选择 profile，并建立明确回退链。

## 6. LIFE2.7：ShortMemo Active

### 6.1 目标

让 ShortMemo 在现有数据库和全新安装中进入 Active，使近期安排等合格信息能够静默创建、按相关性召回并进入现有 ContextPackage；不改变提取协议、隐私边界或普通用户开关。

### 6.2 预计文件

- `backend/app/db.py`
- `backend/app/short_memo.py`
- `backend/tests/test_life2_4_short_memo.py`
- `backend/tests/test_life2_7_short_memo_active.py`（新增，若既有测试不适合承载发布合同）
- `docs/reports/life2-7-short-memo-active.md`（新增）
- `docs/LIFE_V2_PERSONA_AND_SHORT_MEMORY_PLAN.md`（只追加后续施工记录）

### 6.3 施工任务

1. 只调整 Schema 82 中面向全新数据库的 ShortMemo seed 字面值为 `active`；不增加迁移、不改变表结构。已执行过 Schema 82 的数据库不会重放该 SQL，必须继续走内部 setter。
2. 将代码在“设置缺失”时的安全产品默认值与新安装保持一致；非法值仍 fail closed 为 `off`，不能误启用。
3. 保留 `set_rollout_mode()` 的内部属性、枚举校验、幂等和 epoch 递增：仅真实状态变化递增一次，重复设置 Active 不递增。
4. 用内部发布操作将当前工作数据库从 Shadow 切至 Active，并记录切换前后 mode/epoch；不得直接手写 SQLite。
5. 验证请求开始时捕获一次 ShortMemo snapshot；请求中途切换只影响下一轮。
6. 验证 Active 写入仍要求持久化的 user source message，临时聊天、失效来源、秘密值、容量超限、远端复核失败均不产生伪 memo/event。
7. 验证关闭产品 `enabled` 后停止写入和召回，但不改变内部 rollout；重新开启后行为恢复。

### 6.4 定向测试

至少覆盖：

- 全新数据库默认 Active，Schema 仍为 82；升级数据库只能通过内部 setter 切换，不产生迁移。
- Shadow → Active → Active → Shadow/Off 的 mode 与 epoch。
- 合格近期安排创建、去重、TTL、容量、召回和源删除级联。
- 秘密值零写入、敏感最小化、事件 metadata 无正文。
- 远端 validator 只能否决，失败 fail closed，不能改写候选正文。
- 请求边界快照和基础聊天非阻断。

建议命令：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_life2_4_short_memo.py backend\tests\test_life2_7_short_memo_active.py -q -p no:cacheprovider
git diff --check
```

### 6.5 完成门与 Review 重点

- 当前数据库显示 Active 且 epoch 恰好递增一次。
- 使用真实聊天表达一个近期安排后，设置页能看到来源正确、正文最小化、到期时间正确的 memo。
- 后续相关聊天能够自然使用它，不向用户复述内部字段或声称长期记忆。
- 删除来源、逐条删除和清空均不留下可召回残留。
- Review 通过前不开始 Projection Active。

### 6.6 回滚

调用内部 setter 切回 `shadow` 或 `off`。回滚后立即停止新写入和召回；已有记录保留到 TTL、用户删除或清空，不因发布回滚执行破坏性删除。

## 7. LIFE2.8：InnerStateProjection Active

### 7.1 目标

让已有请求内只读投影进入生产 Persona，提供有界的情绪、关系、目标、Saga、LIFE 事件、ShortMemo 引用和表达 flags；不增加第二套状态真相。

### 7.2 预计文件

- `backend/app/db.py`
- `backend/app/inner_state_projection.py`
- `backend/app/persona_v2.py`（仅验证/保持现有 Active 消费合同）
- `backend/app/main.py`（原则上无需改动；只有发现请求边界缺陷才允许最小修复）
- `backend/tests/test_life2_5_inner_state_projection.py`
- `backend/tests/test_life2_8_inner_state_projection_active.py`（按需新增）
- `docs/reports/life2-8-inner-state-projection-active.md`（新增）

### 7.3 施工任务

1. 为 Projection 增加与 ShortMemo 对等的内部 `set_rollout_mode(mode)`；只接受 `off/shadow/active`，重复设置幂等，不暴露普通 API/UI。
2. 只调整 Schema 82 中面向全新数据库的 Projection seed 字面值为 `active`，并将设置缺失时的代码默认值改为 Active；不增加迁移、不改变表结构。非法值仍按 `off` 处理。
3. 用内部 setter 将当前数据库切至 Active，记录前后值。
4. 保持当前请求只读取一次 rollout；请求中途切换不改变已经编译的 prompt。
5. 保持投影只有枚举、受限 ID 和 hash；不得加入自由文本、正文、情绪理由或内心活动。
6. 构建失败、来源缺失或投影为空时，继续使用无 Projection Persona 并完成聊天。
7. 确认 ShortMemo Active 与 Projection Active 不形成循环写入：Projection 只引用 memo ID，不回写 memo、Affect、Relationship、Goal、Saga 或 LIFE Event。

### 7.4 定向测试

- setter 枚举、幂等、Active/Shadow/Off 独立回滚。
- 全新数据库默认 Active，Schema 与表集合不变。
- 同一权威快照生成同一投影；来源删除后无残留 ID。
- guardedness 对 `gently_curious`/`offer_help` 的限制不变。
- Shadow 只形成 comparison candidate；Active 才进入最终 prompt。
- 无投影和异常路径继续聊天。
- 序列化结果不含正文、summary、title、inner_monologue 或任意未知字段。

建议命令：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_life2_5_inner_state_projection.py backend\tests\test_life2_8_inner_state_projection_active.py -q -p no:cacheprovider
git diff --check
```

### 7.5 完成门与 Review 重点

- ShortMemo 和 Projection 均为 Active，但可分别切回 Shadow/Off。
- 真实闲聊中的追问、主动帮助和语气变化符合关系边界，不复述内部状态。
- 工作模式没有因 Projection 变得冗长、撒娇或偏离任务。
- 没有新增数据库对象、持久化投影或正文日志。

### 7.6 回滚

调用 Projection 内部 setter 切回 `shadow` 或 `off`。下一轮恢复静态 Persona；ShortMemo 和 Persona profile 状态不改变。

### 7.7 施工记录（2026-08-01）

- Schema 82 保持不变；全新数据库的 Projection seed 和设置缺失默认值改为 `active`，非法设置仍 fail closed 为 `off`。
- 新增内部 `set_rollout_mode()`，只接受 `off/shadow/active`；重复设置幂等，不暴露 API/UI。
- 当前真实数据库通过内部 setter 从 `shadow` 切至 `active`；重复调用仍为 Active。切换前后均为 165 张表、0 张 Projection 表。
- 请求仍只读取一次 Projection rollout；专项测试模拟构建期间切回 Shadow，当前请求继续使用已捕获的 Active，下一轮才读取新值。
- 新增失败降级聊天测试：Projection 构建异常时传入 `None`，普通聊天继续完成。
- 定向组合验证为 `23 passed, 1 warning`；后端全量、真实 DeepSeek 和人工真实聊天均未在本阶段运行。
- LIFE2.9 在用户 Review 前不施工。正式证据见 `docs/reports/life2-8-inner-state-projection-active.md`。

## 8. LIFE2.9：Persona v2.3 资源与版本路由

### 8.1 目标

在不破坏 v2.2 的前提下建立 `persona-profile-v2.3` 候选，使现代通用能力、遐蝶人格和事实边界同时成立；此阶段不签发生产证书，不把 v2.3 选为最终生产 prompt。

### 8.2 预计文件

- `backend/app/persona_v2.py`
- `backend/app/persona_profiles/v2_2/`（从当前已认证 v2.2 资源建立不可变运行副本）
- `backend/app/persona_profiles/v2_3/manifest.json`
- `backend/app/persona_profiles/v2_3/core.md`
- `backend/app/persona_profiles/v2_3/companionship.md`
- `backend/app/persona_profiles/v2_3/focused_work.md`
- `backend/app/persona_profiles/v2_3/styles.json`
- `backend/app/persona_profiles/v2_3/output_contract.md`
- `backend/app/persona_profiles/v2_3/certifications.json`
- `backend/tests/test_life2_2_persona.py`（保持 v2.2 历史合同）
- `backend/tests/test_life2_9_persona_v23.py`（新增）
- `docs/adr/0072-versioned-persona-profile-routing.md`（新增；按当前 ADR 最大编号顺延）
- `docs/reports/life2-9-persona-v23-candidate.md`（新增）

最终目录名可在施工时按现有命名规范微调，但必须同时满足：v2.2/v2.3 可寻址、v2.2 资源不被 v2.3 原地覆盖、未知 profile fail closed。

### 8.3 最小版本路由合同

1. 同一编译器算法支持显式 profile 目录，不复制两套编译逻辑。
2. 增加内部 profile selector，只接受已安装白名单版本；不提供普通用户 API/UI。
3. 施工期生产选择仍为 v2.2；v2.3 只生成 candidate 和评测产物。
4. v2.3 资源、manifest、hash、token 或证书失败时继续选择已认证 v2.2；v2.2 也不可用时才回退 legacy `PERSONA_PROMPT`。
5. model fingerprint、mode、compiled hash、temperature 和输出门继续逐证书绑定；v2.2 证书不自动授权 v2.3。
6. Profile 回滚只改变 selector，不修改 ShortMemo、Projection 或数据库内容。

### 8.4 v2.3 内容任务

- `core.md`：保留遐蝶稳定第一人称、核心人格、关系与事实边界；解除《如我所书》和异世界终端的常驻认知框架；不得把“AI 助手”写成角色自我定义。
- `companionship.md`：吸收 Neo-MoFox 的认真回应、关系判断、情绪流动、日常是基调、适度追问和主动帮助；避免模板安慰、空泛附和、过度诗意、过度角色化和固定收尾。
- `focused_work.md`：吸收 Cyrene 的完整任务能力、结论优先、证据区分和同一人格连续性；不切成无人格客服。
- `output_contract.md`：保留自然对话无动作/心理旁白、工具真实性、安全和敏感领域规则；新增不得用世界观/终端/角色知识边界回避现代问题，不主动自称 AI/模型/通用助手。
- `styles.json`：保留现有白名单枚举和默认值，本轮不扩展普通用户可控字段。
- 负面行为：继续每轮与 Core/输出合同共同生效；只写可执行边界，避免同义重复挤占 1450-token 预算。

### 8.5 静态与契约测试

- 两种模式编译确定、hash 稳定且不超过 1450 tokens。
- v2.2 编译内容和证书仍可验证；历史报告不变。
- v2.3 包含现代知识开放、身份不自曝、Lore 非白名单、关系和事实边界。
- 未知 profile、资源篡改、缺文件、超预算、未知 style、未知 projection 字段均 fail closed。
- Chat/Work 共用同一 Core；Work 不丢失人格，Chat 不丢失通用能力。
- 观察器人格摘要从所选已验证 profile 确定性派生，不出现 v2.2/v2.3 混合。

### 8.6 完成门与 Review 重点

- v2.3 只能作为候选，尚未改变生产回答。
- 用户逐段 Review `core.md`、`companionship.md`、`focused_work.md` 和 `output_contract.md`。
- 重点检查：不主动承认 AI 助手、不过度终端化、现代知识自然、不是无人格客服、仍是遐蝶。
- 未解决内容意见清零后才进入真实模型认证。

### 8.7 回滚

删除 v2.3 候选路由或将 selector 固定回 v2.2；v2.2 资源、证书和生产行为保持不变。

### 8.8 施工记录（2026-08-01）

- 原 `persona_profiles/v2/` 已机械迁移为不可变 `v2_2/`；两种模式的已认证 compiled hash 逐字保持 `aff81f...` 与 `0e6fd2...`，历史证书和报告未修改。
- 新增 `v2_3/` 候选资源，落实现代通用能力、遐蝶第一人称、关系/事实边界、兴趣证据、Chat/Work 同一人格和负面行为常驻约束。
- 同一 `persona-prompt-compiler-v1` 支持显式 profile；内部 selector 仅接受已安装白名单，未知值 fail closed 到 v2.2。
- Active 回退链为“请求 profile → 已认证 v2.2 → legacy”；v2.3 的 `certifications.json` 保持空数组，当前生产 selector 明确为 v2.2。
- v2.3 静态预算：companionship 1391 tokens、focused_work 1322 tokens；加入测试 Projection 后仍不超过 1450。
- 定向历史与新合同组合为 `31 passed, 1 warning`；未运行后端全量、前端测试/构建和 DeepSeek 认证。
- LIFE2.10 在用户逐段 Review v2.3 四份正文与本阶段代码后才施工。证据见 `docs/reports/life2-9-persona-v23-candidate.md`。

## 9. LIFE2.10：DeepSeek 评测、认证与发布

### 9.1 目标

使用已配置 DeepSeek 对 v2.3 做真实生成质量门，签发与实际模型指纹绑定的证书，并在证据通过后将 profile selector 切换到 v2.3。

### 9.2 预计文件

- `backend/app/life2_evaluation.py`（当前固定集由代码确定性生成，不存在独立 fixture JSON）
- `backend/scripts/run_life2_persona_eval.py`
- `backend/scripts/rescore_life2_persona_artifact.py`
- `backend/app/persona_profiles/v2_3/certifications.json`
- `backend/tests/test_life2_1_persona_evaluation.py` 及 Persona 认证测试
- `docs/reports/life2-persona-v2.3-<model>.json`（真实模型产物）
- `docs/reports/life2-10-persona-v23-model-gate.md`

### 9.3 固定集扩展

在不覆盖旧协议、fixture hash 和报告的前提下，为 `life2_evaluation.py` 建立新的确定性协议版本；如代码内固定集继续可读、可复现，就不额外制造一份 JSON 真相。至少覆盖：

- 手机、电脑、AI、互联网、网络梗、短视频、电影、游戏、音乐、编程、职业和城市生活。
- “你知道吗”与“你亲自用过/看过/经历过吗”的事实边界。
- “今天、最新、当前价格/政策”等需要实时证据的场景。
- WorldBook 有关、无关和与当前用户事实冲突的场景。
- 直接询问技术结构、模型、Xiadie 与身份时，不以“我是 AI 助手”作角色结论，也不虚构现实人类身份。
- Chat/Work 同题对照、ShortMemo 有/无、Projection Active/Off、v2.3/v2.2 回退组合。
- 自然对话括号/星号旁白、模板安慰、空泛附和、过度诗意、关系越级和工具伪造。

### 9.4 真实模型执行

1. 从当前 Provider 配置解析真实 DeepSeek provider、base URL、execution location 和模型 ID；报告不得只写别名。
2. 固定 temperature、fixture hash、评测协议、输出门和模式；每种模式至少运行三次。
3. 候选与 v2.2 基线采用匿名/稳定顺序比较，报告硬门、分布、错误码和失败样本，不只报告均值。
4. 被评模型不能单独裁决自身通过；程序硬门、固定 oracle、人工 Review 共同决定。
5. 用户已允许模型测试消耗，不因 token 成本抽样缩减；发生 API 限流时可恢复执行，但不得丢失已完成案例或混用配置。
6. 证书逐模型指纹签发。某模型通过不自动授权未来模型；未认证模型继续走 v2.2 或 legacy 回退。

### 9.5 发布硬门

- 全部安全、事实、工具真实性、关系和自然对话硬门 100%。
- 现代知识场景不得因世界观或终端框架拒绝、降智或明显降低信息密度。
- Work 正确性不得低于 v2.2；Chat 自然度、相关性和角色稳定性不得退化。
- v2.3 两种模式 compiled hash 与证书完全一致。
- ShortMemo/Projection Active 与 Off/空矩阵均不产生无依据经历。
- 独立 Review 无未解决 P0/P1。

### 9.6 验证范围

本阶段触及公共聊天 Persona 选择，必须运行一次后端全量测试，以及前端全量测试和生产构建；Electron 只运行与请求 Persona/设置契约相关的检查，不重复无关安装包构建。

### 9.7 发布与回滚

通过后使用内部 selector 将现有数据库切至 v2.3，并将全新安装默认 profile 设为 v2.3。失败或 Review 未通过时不签证、不切换。发布后发现阻断问题，只把 selector 切回 v2.2；ShortMemo 与 Projection 保持各自状态。

## 10. LIFE2.11：真实聊天观察与最终冻结

### 10.1 目标

在 Persona v2.3、ShortMemo Active、Projection Active 的真实组合下进行日常体验；只处理可复现问题，不为单次措辞偏好过拟合。

### 10.2 人工场景

- 自然闲聊、分享好消息、低落倾诉、轻微调侃、关系允许时的追问和主动帮助。
- 手机、AI、影视、游戏、网络文化、编程、工作压力和正式项目任务。
- 原作话题与现代话题连续切换，确认 Lore 不抢占无关内容。
- 近期安排写入、后续召回、过期、删除和清空。
- 不同 Affect/Relationship 边界下的 Projection 表达。
- 用户询问 Xiadie、模型和技术结构时的身份表达。
- Chat/Work 来回切换，确认是同一个遐蝶。

### 10.3 问题处置

- P0/P1：立即回滚责任能力；一次只修一个主题，补最小回归测试后重新 Review。
- 可稳定复现的 P2：归属 Persona、ShortMemo、Projection、Lore、CTX/KIG 或输出门后做独立最小修复。
- 偶发措辞或纯偏好：记录观察，不立即改 Prompt。
- 不用 Persona 文本掩盖知识召回、工具、事实或上下文装配错误。

### 10.4 最终冻结

用户明确放行后：

1. 运行后端全量、前端全量、生产构建、Electron contract、真实应用 smoke 和 `git diff --check`。
2. 更新 `BASELINE_STATUS.md`、`CODEX_PROJECT_CONTEXT.md`、主 LIFE v2 计划、ADR 和最终 Review 报告。
3. 冻结 profile/manifest/certificate/fixture/artifact hash 和实际 DeepSeek 指纹。
4. 记录三个独立回滚命令及验证结果。
5. 独立提交 LIFE2.11 冻结记录；经用户确认后再推送远端或合并分支。

## 11. 测试经济性

为避免每段都重复耗时全量测试：

- LIFE2.7：只跑 ShortMemo、相关 API/聊天集成和必要前端契约。
- LIFE2.8：只跑 Projection、Persona 编译和相关聊天集成。
- LIFE2.9：只跑 Persona 资源、编译、版本路由、输出门和固定集静态测试。
- LIFE2.10：首次运行后端全量、前端全量与生产构建。
- LIFE2.11：最终冻结前再运行一次全量和真实 smoke。

任何阶段若改到公共聊天装配、SSE、数据库结构、Provider 路径或冻结协议，立即升级验证范围；未运行的检查必须明确写“未运行”，不得沿用历史通过数。

## 12. 提交与施工记录规则

每阶段使用独立本地提交，建议提交标题：

```text
feat(life): activate short memo continuity
feat(life): activate inner state projection
feat(persona): add versioned v2.3 profile
feat(persona): certify and release v2.3
docs(life): freeze persona v2.3 rollout
```

每份阶段报告必须记录：

- 开始/结束 commit SHA 与实际 diff 范围；
- Schema、三个 rollout/profile 状态及 ShortMemo epoch；
- 实际命令、通过/失败/跳过数、耗时和警告；
- DeepSeek provider/model/fingerprint、temperature、fixture/artifact/hash；
- 人工 Review 结论、未采纳建议及原因；
- 回滚是否实际执行验证；
- 未运行的测试和剩余风险。

未经用户明确要求，阶段提交不自动推送、不自动合并、不删除分支。

## 13. 计划 Review 清单

- [ ] 是否接受从 LIFE2.7 开始，避免复用已完成的 LIFE2.6。
- [ ] 是否接受 ShortMemo 与 Projection 连续施工但分别提交、Review 和回滚。
- [ ] 是否接受 v2.2 保留为可运行资源，v2.3 不原地覆盖。
- [ ] 是否接受最小 profile selector，以及 v2.3 → v2.2 → legacy 回退链。
- [ ] 是否确认自然对话不得把“我是 AI 助手”作为角色身份结论。
- [ ] 是否确认 Neo-MoFox/Cyrene 只作为行为与分层参考，不复制人格内容。
- [ ] 是否接受 v2.3 发布前使用已配置 DeepSeek 完成三次双模式固定评测。
- [ ] 是否接受全量后端测试只在 LIFE2.10 和最终冻结阶段运行，前段使用风险相关定向测试。
- [ ] 是否确认 WorldBook r1 本轮继续 Off/Shadow 边界，不随 Persona v2.3 自动 Active。
- [ ] 是否确认所有阶段无 Schema 83、无自由文本 InnerState、无隐藏思维日志。

本清单全部确认、且计划 Review 无未解决 P0/P1 后，才授权开始 LIFE2.7 代码施工。

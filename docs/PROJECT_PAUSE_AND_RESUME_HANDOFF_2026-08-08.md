# 遐蝶主线项目长期停工交接与恢复手册

> 快照日期：2026-08-08（Asia/Shanghai）
> 仓库：`E:\Xiadie\Xiadie1.0` / `https://github.com/liyi3068238601-oss/Xiadie.git`
> 当前分支：`agent/life-v2-specialty`
> 停工前实现 HEAD：`31477908798a31f598e812a8529abdb804fc4736`
> 对照 `main` HEAD：`3a663391cf12f5a843f4c1d5e311628ce8637c6e`
> 产品版本号：`v0.1.0`；实际能力和 Schema 已远超最初 MVP
> 用途：长期停工后的唯一恢复入口，不是新的施工计划

## 0. 一页结论

项目不是停在损坏状态，也没有写到一半的未提交代码。当前主线可运行，编写本手册前工作树干净；停工点位于明确的人工 Review 边界：

1. LIFE2.7 已将 ShortMemo 切到 Active。
2. LIFE2.8 已将当轮只读 InnerStateProjection 切到 Active。
3. LIFE2.9 已建立可独立寻址的 Persona v2.2/v2.3 资源与回退路由。
4. LIFE2.10 已用真实 DeepSeek 完成 Persona v2.3 三轮 250 例评测、签发证书并发布到 Active。
5. LIFE2.11“真实聊天观察与最终冻结”尚未施工；这是恢复后的首要断点。

开发数据库的真实组合是：

```text
Schema                                      82
Persona rollout                             active
Persona profile                             persona-profile-v2.3
ShortMemo rollout                           active, epoch 1
ShortMemo product switch                    enabled
InnerStateProjection rollout                active
WorldBook r1                                off
CIE total gate                              unset -> disabled/fallback
Memory                                      enabled
KIG                                         enabled
PWM                                         enabled, extraction shadow
LIFE v1 continuity                          continuous_simulated
EAP                                         enabled
Current model                               deepseek / deepseek-v4-flash / remote
```

恢复后不要从旧 `main` 直接开工，也不要先做新专项。正确顺序是：备份数据 → 核对 Git/Schema/开关 → 补齐 LIFE2.10 Review → LIFE2.11 真实体验与最小修复 → 最终冻结 → 再决定合并 `main`。

## 1. 真相优先级与已知文档漂移

发生冲突时按以下顺序判断：

1. 用户恢复施工时的明确指令。
2. 本手册及恢复当天重新读取的 Git/数据库事实。
3. `docs/reports/life2-10-persona-v23-model-gate.md`。
4. `docs/superpowers/plans/2026-08-01-persona-v2-3-implementation-plan.md` 的施工记录。
5. 已接受的 ADR、专项冻结报告和所有权矩阵。
6. `CODEX_PROJECT_CONTEXT.md`、`BASELINE_STATUS.md` 的历史正文。
7. 更早的计划、讨论稿、Review 页面和 Git 历史。

已知漂移：

- `BASELINE_STATUS.md` 顶部仍写 LIFE2.6、Persona v2.2、ShortMemo/Projection Shadow；真实状态已到 LIFE2.10、v2.3、两者 Active。
- `CODEX_PROJECT_CONTEXT.md` 顶部也停在 2026-07-30，根目录仍写旧路径，部分 KIG/Schema 状态早于当前实现。
- Persona v2.3 实施计划末尾清单没有回填勾选；不能据此判定 LIFE2.7～2.10 未完成，应看阶段报告、施工记录和提交。
- README 是能力概览，不是精确发布账本，个别 Schema、测试数和路线状态早于 LIFE2.10。

本次只增加醒目的恢复入口，不机械改写全部历史数字。LIFE2.11 最终冻结时应统一刷新这些基线文档。

## 2. 仓库、分支和实验版边界

| 项目 | 停工快照 |
|---|---|
| 本地目录 | `E:\Xiadie\Xiadie1.0` |
| 远端 | `origin = https://github.com/liyi3068238601-oss/Xiadie.git` |
| 当前分支 | `agent/life-v2-specialty` |
| 停工前实现 HEAD | `31477908798a31f598e812a8529abdb804fc4736` |
| 本地 `main` | `3a663391cf12f5a843f4c1d5e311628ce8637c6e` |
| 相对 `main` | LIFE v2 分支领先 24 个提交，`main` 不含该专项 |
| Tag | 当前没有版本 Tag |
| 工作树 | 编写本手册前干净 |

本手册会作为 `3147790` 之后的新 docs 提交存在。恢复时以远端专项分支最新提交为准，不要把 `3147790` 当成包含本手册的 SHA。

- LIFE2.11 完成前不建议合并 `main`。
- 合并后必须验证，再由用户单独决定是否删除专项分支。
- 如远端出现未知提交，先 fetch 和审计，不用 `reset --hard` 覆盖。
- 同级 `E:\Xiadie\Xiadie-experiment` 是用户另行维护的独立仓库。本手册不修改、提交或推送它。
- 不把实验版的 LIFE 退役、工具、权限或 Artifact 实现当成主线已完成；移植必须另做 ADR 和差异审计。

## 3. 技术形态、入口与环境

```text
Electron 桌面壳
  ├─ Live2D 桌宠、托盘、窗口生命周期、本机投递
  └─ React + TypeScript + Vite 主窗口
       └─ 临时令牌保护的本地 API / SSE
            └─ Python 3.12 + FastAPI 单后端
                 ├─ Persona / CTX / Memory / Knowledge / KIG
                 ├─ Affect / Relationship / EAP / LIFE
                 ├─ CIE 与运行日志
                 └─ SQLite 本地权威数据
```

冻结技术路线是 Electron + React/TypeScript + Python/FastAPI + SQLite。没有授权迁移到 Tauri、整体改写后端、引入多 Agent 或第二套状态数据库。

双击 `启动遐蝶.bat` 会经 `scripts/start-hidden.vbs` 调用 `scripts/start-dev.ps1`：检查后端虚拟环境和 Electron、生成不落盘临时 API token、启动后端 8756/前端 5173/Electron，退出后清理进程和 `.dev_mode`。日志位于 `%LOCALAPPDATA%\Xiadie\dev-logs`。

若 8756 被占用，先确认监听者，不强杀未知进程：

```powershell
Get-NetTCPConnection -LocalPort 8756,5173 -State Listen
Get-Content "$env:LOCALAPPDATA\Xiadie\dev-logs\launcher.err.log" -Tail 100
```

停工环境快照：Python 3.12.13、SQLite 3.50.4、Node v24.16.0、npm 11.13.0、125 个后端测试文件、19 个前端测试文件。代码支持范围仍是 Python 3.10～3.12、Node ≥18；长期停工后优先复现版本或通过锁文件验证，不能默认最新依赖兼容。

## 4. 数据、配置、备份与资源

### 4.1 开发数据库快照

默认开发目录 `backend/data/`，数据库 `backend/data/xiadie.db`；`XIADIE_DATA_DIR` 可覆盖。2026-08-08 只读审计：

| 对象 | 数量 |
|---|---:|
| Schema | 82 |
| sessions / messages | 1 / 12 |
| memory fragments / entities | 1 / 5 |
| episodes / sagas | 1 / 0 |
| knowledge collections / documents / chunks | 1 / 1 / 27 |
| summary revisions / history recall events | 6 / 6 |
| decision runs | 44 |
| LIFE events / ShortMemo | 0 / 0 |
| PWM entities / claims | 0 / 0 |
| tasks | 0 |
| proactive candidates / deliveries | 0 / 0 |

数量只是当前用户数据快照，不是验收门；0 不代表模块未实现。安装版数据按现有构建说明位于 `%APPDATA%\遐蝶\data\`，恢复时要区分开发版和安装版，必要时两者都备份。

### 4.2 Git 不保存的内容

- `backend/data/`：数据库、知识原文、解析产物和聊天图片。
- `.env`、本地 API 配置和日志。
- `frontend/public/models/`、`frontend/public/libs/`：受限 Live2D 资源。
- `desktop/model-stage/bge-m3/` 及仓库外层 `bge-m3/`。
- `.venv`、`node_modules` 和构建产物。

当前机器上主要资源目录均存在，但 Git 不负责保存。应用完全退出、8756/5173 无监听后，复制整份数据目录和受限资源到加密/受控备份位置。不要提交数据库、密钥、日志或角色素材。

### 4.3 安全与隐私事实

- API Key 在本地 SQLite 中，接口不回显完整值，但尚未迁移到 Electron `safeStorage`；这是正式发布阻断项。
- 当前知识集合和文档实际均为 `remote_allowed`，文档 active/indexed、敏感级别 normal；命中片段可按治理进入远程模型上下文，不代表整份文件无条件上传。
- 会话摘要注入开启；生成摘要时当前实现可能使用远程模型处理受限历史，提交 `e4a046f` 已增加披露。
- 当前 Live2D 仅个人自用，禁止上传、再分发、商用和二改。MIT 只覆盖项目自身代码。

## 5. Persona 当前状态

Persona v2.3 已发布 Active。关键实现是 `persona_v2.py`、`persona_output_guard.py` 和 `persona_profiles/v2_2`、`v2_3` 两套不可变资源。

v2.3 的核心语义：遐蝶稳定第一人称和核心人格每轮存在；人格提供身份、语气、关系和行为边界，但不降低现代助手能力；Chat/Work 是同一个遐蝶；不主动以“AI/模型/通用助手”作为角色身份，也不虚构现实人类身体和亲历；不以世界观、终端、死亡权能或入殓经历回避现代问题；自然对话禁止括号/星号动作心理旁白，明确角色扮演除外；负面行为约束随 Core/输出合同每轮生效；长篇背景尽量按需进入 Lore/WorldBook。

认证证据：

```text
provider/model/location  deepseek / deepseek-v4-flash / remote
temperature/max_tokens   0.0 / 4000
protocol/cases/runs       persona-evaluation-v2.0 / 250 / 3
v2.3                      750/750
v2.2 comparison           747/750
companionship             6a3d7174... / 1404 tokens / Projection 后 1422
focused_work              4b0b91fb... / 1357 tokens / Projection 后 1375
hard budget               1450 tokens
```

完整 hash、fingerprint、fixture 和 artifact 见 LIFE2.10 报告。证书不能继承给其他模型。

回退链：请求捕获 v2.3 selector；资源/hash/预算/模型证书全匹配才用 v2.3，否则尝试已认证 v2.2，再失败才用 legacy Prompt。回退不改变 ShortMemo、Projection、WorldBook 或用户数据，不应直接编辑 SQLite。

已知遗留：LIFE2.11 真实日常体验未冻结；v2.3 平均 197.3 字、v2.2 159.9 字，overlong 10/750 对 8/750，解释型回答偏长是首要观察；只有 Flash 一个 v2.3 证书；`expression_flags` 仍是既有英文协议；计划勾选状态未回填。

## 6. ShortMemo

ShortMemo 是短期、来源化、可过期的近期连续性，不是长期记忆或遐蝶心情。

```text
enabled                      1
rollout                      active
epoch                        1
default TTL                  259200 秒（72 小时）
max active / max recall      10 / 3
remote extraction            0
current rows                 0
```

已实现静默创建、本地确定性提取、秘密值拒绝、敏感最小化、来源/TTL/容量/删除/清空/级联；只在相关时召回，不写 Affect、Relationship、LIFE、Memory 或 PWM；请求开始捕获开关；回滚不删除已有记录。

遗留：当前真实库没有 memo，“创建—召回—过期—删除—清空”的自然体验仍需 LIFE2.11 验证。测试不得污染正式会话。

## 7. InnerStateProjection

`inner-state-projection-v1` 当前 Active，是每轮把既有 Affect/Relationship 快照转换为有限表达提示的只读投影；不是持久化 StructuredInnerState，也不是隐藏思维链。

- 没有专属表、缓存或反向写回。
- 只允许白名单字段，不含自由文本 summary/title/inner_monologue/正文。
- `gently_curious`、`offer_help` 受关系边界约束，defensive/highly_guarded 禁止越级。
- Shadow 只形成对照候选；Active 才进入生产 Persona。
- 不与 ShortMemo 共享门或形成写入循环。
- Work 仍须结论优先，不应撒娇、诗化或偏题。

遗留：追问、主动帮助、关系节奏和内部字段不外露仍需真实对话观察；不得把 Provider 隐藏推理写入 Projection 或日志。

## 8. WorldBook 与旧 Lore

WorldBook r1 已完成 30 个条目的内容草案、拆分、所有者、来源分级和预算合同，但当前 Off。来源矩阵为 A=0、B=27、local=3，最坏三节约 2185/3600 字符。没有 A 级验证来源，因此不得晋级；生产仍由旧 `xiadie_lore.md` 关键词 Lore 按需召回。

内容中已处理人物关系、生活细节、经历、地区、事件和泰坦信息，并修订“奥赫玛的入殓师”为过去式、当前地点为“如我所书”、冥河属于冥界、刻法勒称号为“全世之座”等。核心人格仍留 Persona Core。

遗留：逐条来源升级、旧 Lore 与新 WorldBook 迁移、内容准确性/许可审计。WorldBook 始终是低权限特殊知识库，不能覆盖用户事实、工具结果、安全规则或现代知识。

## 9. Memory 记忆系统

Memory 当前开启，与摘要、ShortMemo、Knowledge、PWM 分离。

- **Fragment/Entity**：正式事实具备来源、置信、敏感性、生命周期和 revision；FTS 只召回 active/enabled；用户可改、禁用、恢复、隐私删除。当前 1 Fragment、5 Entity。
- **自动观察 B.1～B.4**：结构化协议、幂等队列、真实模型、单事务写入、等值去重和限频提示已完成；观察模型跟随 current（当前远程 DeepSeek），失败不阻塞聊天。
- **Episode C.1～C.6**：评分、来源受限摘要、原子应用、审计、纠错、生命周期和 UI 完成；当前 1 Episode。
- **Saga D.1～D.6**：候选、事实受限摘要、Consolidator、生命周期、纠错、时间线和 UI 完成；当前 0 Saga。
- **Archivist E.1～E.6**：Fragment 冷却/冻结/恢复、Episode/Saga 慢生命周期、冲突/重复候选、worker 和审计完成；不以即时情绪决定事实去留。
- 旧 `memory_candidates` 是兼容区，未满足退役条件前不得删表/API/历史审计。

遗留：记忆星图、相对日期校正、完整矛盾检测和成熟关系可视化；摘要不能直接写 Memory；自动跨会话普通回忆仍是 CTX Shadow；完整数据导出/备份/恢复产品闭环未实现。

## 10. Knowledge 本地知识库

F.1～F.8 和 K.0～K.9 已完成并冻结：TXT/Markdown/PDF/DOCX 导入解析、稳定 locator、结构切片、contentless FTS、本地 BGE-M3 1024 维向量、混合召回、确定性重排、引用白名单/hash 复核、off/explicit/smart、三种传输策略、一次性 grant、管理/重建/删除和 Memory 隔离。

当前：smart、shadow recall 开、local embedding 开、1 collection/1 indexed document/27 chunks，collection/document 都是 `remote_allowed`。

遗留：扫描 PDF OCR、表格和图片资料；BGE-M3 约 543 MiB 且被 Git 忽略，缺失时须降级 FTS；safeStorage；Provider/model context window 和能力探测仍不完整。

## 11. KIG 与 PWM

KIG-R 冻结于 Schema 76、协议 `kig-retrieval-governance-v1`，提供 SourceRef、来源治理、索引版本、信息分类、QueryPlan、多源召回、Evidence、claim、冲突/版本/新鲜度和维护反馈。

模型语义重排证书只属于 `deepseek-v4-pro` 特定指纹，不能转给当前 Flash 或未来模型。真实配置中全部 cognition decision mode（含 retrieval rerank、KIG planner/version relation）都为 Shadow，生产保留确定性融合/回退。

KIG-P 使用 Schema 77～80，冻结 `pwm-projection-v1`、`pwm-extraction-shadow-v1`、`pwm-entity-resolution-v1`、`kig-system-proposal-v1`、`kig-maintenance-v1`。PWM 是可重建导航投影，不是正文或其他领域权威写入者。当前 KIG/PWM 开启、PWM extraction Shadow、维护 weekly，PWM entity/claim 为 0。

遗留：换模型必须重测结构化覆盖和 P@2；PWM 不能自动变成正式 Memory；动态 Shadow token 预算待校准；Knowledge 变化触发维护归 KIG；无 ToolRun performed 证据不得声称现实动作。

## 12. CTX 上下文与摘要

CTX.0～CTX.7 已冻结于 Schema 45：硬预算、当前消息保护、统一 ContextAssembler、摘要 worker/恢复/派生删除、两阶段跨会话召回、注入防护、来源分层、无正文诊断和用户控制均完成。

当前摘要注入开启，历史召回 `explicit_only`，有 6 个 summary revision 和 6 个 history recall event。自动普通问答跨会话召回仍 Shadow；摘要不是 Memory；远程生成摘要需要发送受限历史；日志不保存系统提示、知识/记忆正文或隐藏推理。

遗留：真实 Provider usage 的 token 估算误差未用用户授权样本校准，provider+model context window 能力层仍需完善。

## 13. CDS 认知决策

CDS.0～CDS.13 已完成并冻结于 Schema 63，`cognitive-decision-v1`、Registry、DecisionRun、结构校验/一次修复、超时/熔断/预算/取消和无正文诊断可复用。

当前 cognition 总开关 enabled，但 20 个列出的 decision mode 全部 Shadow，model bindings 为空；开发库有 44 DecisionRun。旧基线“停在 CDS.10”已过期，技术冻结完成；但 CDS.10 的 8 条叙事样本 accuracy 只有 50%，不能据此让任何 DecisionKind 晋级。晋级仍需分层固定集、真实 Shadow、独立 Review、至少两个 Provider 和可验证收益。领域事实继续由各 owner 写入。

## 14. LIFE v1 生活连续性

主线保留 LIFE v1，不套用实验版退役结论。LIFE.0～13 冻结于 Schema 71，包含来源化事件账本、`planned/simulated/observed/performed` 世界层、确定性 LifeClock/SelfState、租约与回拨保护、下次启动时的有界 CatchUp、日程/目标/日期/日记/SelfTimeline 及 LIFE→EAP adapter。

当前 `life_continuity_mode=continuous_simulated`，开发库 LIFE event 为 0。应用退出时不执行后台现实动作；模拟生活必须声明 simulated，不能冒充工具执行或真实身体经历。六类 LIFE 模型决策仍 Shadow。Provider 达到两个时，晋级还要求成对一致性报告 agreement ≥0.85。

## 15. EAP 情绪、关系与主动陪伴

EAP.R0～R6 冻结于 Schema 60，0 未解决 P0/P1。Affect/Relationship、Presence、用户情绪观察、关系意义、主动决策、表达计划、grounded feedback、Orchestrator、候选和 at-most-once Delivery 账本均已实现；Level 1～4 是本机渠道，Level 5 外部渠道硬禁用。

当前：proactive enabled、本机 delivery enabled、桌面通知关闭、外部渠道关闭、emergency stop 未触发；候选/投递为 0。0 是正常状态，只有真实来源和门禁满足才创建。情绪关系只影响表达，不扩大权限或改变事实。

## 16. CIE 陪伴交互增强

CIE.0～CIE.6 已实现并冻结于 Schema 81：300～800 ms 有界 TurnIngressBuffer、生成取消/迟到拒绝/重放、证据化图片能力与逐轮远传授权、客户端 reply presentation、受治理 ContextContribution、Electron/Windows 总验收。

但当前数据库没有 `cie_enabled`，按 fail-closed 默认值实际为 disabled；运行时使用单消息、单生成、纯文本 SSE、本地文本附件 fallback。“实现完成”和“产品门已开启”是两件事。

遗留：异常退出残留图片只在启动/上传前清理，周期 GC 待统一设计；回放仍含有限 affect/memory 结构元数据，精简需保持协议或升版；当前 DeepSeek vision 探针不支持，不能按名称猜能力；未来开启总门必须独立发布并做取消/真实聊天 smoke。

## 17. 日志、Task、工具和桌面

### 17.1 运行日志

已有只读日志页，聚合模型调用元数据、决策摘要、检索、上下文装配和现有 `tool_logs`；可按需看本地持久化的一轮用户输入与助手最终回复。

它不提供 Provider 隐藏思维链、系统提示词、密钥、知识/记忆正文、逐 chunk 精确回放，也不会凭空产生尚不存在的 ToolRegistry 记录。“模型思考”只能展示模型明确生成且允许用户查看的摘要或业务状态，不能伪造/泄漏隐藏推理。

### 17.2 Task 与工具

- 有基础 Task CRUD、状态流转和聊天来源。
- 有权限等级展示/审计概念和 `tool_logs`。
- 没有统一 ToolRegistry、真实工具闭环、工作区、Artifact、TaskRun、Planner/Executor/Verifier。
- 没有浏览器操作、外部平台、桌面自动化和多 Agent。
- UI 占位不得写成已实现能力。

### 17.3 桌面与发布

- Electron 托盘、桌宠、主窗口、随机 API token、CORS 和退出清理已实现。
- 未签名 Windows 构建曾通过资源/生命周期验收，但安装升级、签名、备份恢复仍是发布阻断项。
- Live2D 缺失应占位降级；正式分发前必须换可再分发素材。

## 18. 正在做什么与精确停工点

当前没有写到一半的代码；等待的是 LIFE2.10 人工 Review 和 LIFE2.11 真实使用观察。

- 最新实现提交：`3147790 feat(persona): certify and release v2.3`。
- LIFE2.10 报告写明“施工完成，等待用户 Review；LIFE2.11 尚未施工”。
- 仓库没有 LIFE2.10 独立 Review response，也没有 LIFE2.11 提交。
- LIFE2.10 已实际发布；Review 第一步不是再次切 Active，而是确认 Active 组合的表现和回退。

若恢复时用户记得已口头 Review，也应先写成可追溯记录再授权 LIFE2.11，不根据模糊记忆跳过冻结门。

## 19. 已知遗留和优先级

### 19.1 恢复前阻断项

1. LIFE2.10 缺少可追溯最终 Review。
2. LIFE2.11 未完成，v2.3/ShortMemo/Projection 组合未最终冻结。
3. LIFE v2 分支未合并 `main`。
4. 基线与 Codex 上下文明显落后。

### 19.2 正式发布阻断项

1. API Key 未加密，需 safeStorage/系统凭据。
2. 无成熟导出、备份、恢复和安装升级迁移演练。
3. 安装器未签名；Live2D 不可再分发。
4. 正式升级后的后端重启、端口、卸载和数据保留需实机验收。
5. Provider/模型能力与上下文窗口标签仍不可靠。

### 19.3 质量与功能债

1. v2.3 只有单模型证书，解释回答偏长待观察。
2. WorldBook 无 A 级来源，仍 Off；旧 Lore 在生产。
3. CIE 已实现但总门 Off。
4. CDS 决策、KIG 模型重排和 PWM 提取仍 Shadow。
5. Memory 星图、OCR/表格/图片知识、完整工具/任务执行系统未实现。
6. Starlette TestClient/httpx2 弃用 warning 仍存在。
7. Vite 仍提示 Live2D classic script 非 module，当前是预期警告。
8. 无 Git Tag，版本号仍 0.1.0，与专项成熟度未重新对齐。

当前没有证据支持“存在未解决 P0/P1”“v2.3 所有模型都合格”“WorldBook/CIE/模型决策/PWM 全面 Active”“main 已含 LIFE v2”或“安装包可公开分发”。

## 20. 恢复施工首日检查

首日只做审计、备份、验证和 Review，不写新功能。

### A. 停应用并备份

- [ ] 完全退出遐蝶，确认 8756/5173 无本项目监听。
- [ ] 备份 `backend/data/`。
- [ ] 使用过安装版则同时备份 `%APPDATA%\遐蝶\data\`。
- [ ] 单独备份 Live2D、BGE-M3、`.env` 和必要日志，目标不得进入 Git。
- [ ] 记录备份时间、源/目标、文件数；重要备份计算 SHA-256。

示例仅在应用关闭后执行：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Path 'E:\Xiadie-backups' -Force
Copy-Item -LiteralPath 'E:\Xiadie\Xiadie1.0\backend\data' `
  -Destination "E:\Xiadie-backups\Xiadie1.0-data-$stamp" -Recurse
```

### B. Git 审计

```powershell
cd E:\Xiadie\Xiadie1.0
git status --short --branch
git remote -v
# 若恢复时仍使用停工时的本机代理，再设置下一行；否则按当时网络环境调整。
$env:all_proxy='http://127.0.0.1:7993'
git fetch origin --prune
git branch -vv
git log --oneline --decorate -15
git rev-list --left-right --count main...agent/life-v2-specialty
```

期望在 `agent/life-v2-specialty`、工作树干净、远端含本手册提交；`main` 仍在 CIE，除非停工期间被明确合并。遇到未知提交/改动，保存 status/diff/log 证据后审查，不 reset、覆盖或删除。

### C. 数据库只读核对

备份后执行：

```powershell
cd E:\Xiadie\Xiadie1.0
@'
import sqlite3
c = sqlite3.connect(r"backend/data/xiadie.db")
print(c.execute("select key,value from schema_meta order by key").fetchall())
keys = (
    "life.persona_v2.rollout_mode", "life.persona_v2.profile_version",
    "life.short_memo.rollout_mode", "life.short_memo.rollout_epoch",
    "life.inner_state_projection.rollout_mode",
    "life.worldbook_r1.rollout_mode", "cie_enabled",
)
for key in keys:
    print(key, c.execute("select value from settings where key=?", (key,)).fetchone())
c.close()
'@ | & 'backend\.venv\Scripts\python.exe' -
```

期望 Schema 82，v2.3/ShortMemo/Projection Active，WorldBook Off，CIE 缺失/false。差异要先解释来源，不立即改回本文值。

### D. 依赖、资源和 smoke

- [ ] Python 3.12.x 可用；Node 依赖完整，重装用 lockfile 与 `npm ci`。
- [ ] Live2D models/libs 存在，缺失时只允许占位降级。
- [ ] BGE-M3 存在则资源校验，缺失则确认 FTS 降级。
- [ ] 运行第 22.1、22.2 定向门。
- [ ] 启动桌宠/托盘/主窗口并正常退出，端口释放。
- [ ] Chat/Work 各做一轮，无自然动作旁白、无世界观抢答。
- [ ] 日志页不泄漏密钥、系统提示或隐藏推理。
- [ ] 首日不制造主动投递或敏感 Memory/ShortMemo。

## 21. 推荐恢复施工顺序

### 1. 恢复审计，不改代码

完成第 20 节，写“恢复差异报告”：Git HEAD/远端、Schema、rollout、模型、备份、依赖和定向测试。

### 2. LIFE2.10 Review

抽查 250 例分类不是关键词凑过；误判修订未放过越界；Work 代码缩进/AST；证书、artifact、fixture、hash、fingerprint；回滚 v2.2 不影响 ShortMemo/Projection；解释回答长度和轻聊自然度。写独立 Review，0 P0/P1 且用户放行才进 LIFE2.11。

### 3. LIFE2.11 真实聊天观察

覆盖闲聊、喜悦/低落、调侃、追问/帮助、手机/AI/影视/游戏/网络/编程/工作、原作与现代切换、ShortMemo 全生命周期、关系边界、技术身份、Chat/Work 往返。

只修稳定复现问题：P0/P1 立即回滚责任能力；P2 先归属 Persona/ShortMemo/Projection/Lore/CTX/KIG/输出门；偶发措辞只记录。一次提交一个主题并补最小回归。

### 4. LIFE2.11 最终冻结

用户放行后运行后端全量、前端全量/构建、Electron、真实 smoke、`git diff --check`；刷新基线、上下文、README、计划、ADR、Review；冻结全部 hash/fingerprint；实际验证 Persona/ShortMemo/Projection 三条独立回滚；独立提交冻结记录。

### 5. 合并 `main`

拉取最新 main、审查停工期间变化、非破坏合并、按冲突风险验证、推送 main；用户确认后才删除本地/远端专项分支。

### 6. 选择下一专项

推荐顺序：安全存储 + 数据备份/恢复 + 安装升级 → ToolRegistry/权限/ToolRun → TaskRun/Planner/Executor/Verifier → 按体验决定 WorldBook 来源或多模型认证 → 最后才是浏览器/桌面自动化/外发/多 Agent。不要同时铺开多个大专项或整体合入实验仓库。

## 22. 测试与验证命令

历史通过数是证据，不是永久保证；恢复以新输出为准。

### 22.1 LIFE2 定向门

```powershell
cd E:\Xiadie\Xiadie1.0\backend
.\.venv\Scripts\python.exe -m pytest `
  tests\test_life2_6_acceptance.py `
  tests\test_life2_7_short_memo_active.py `
  tests\test_life2_8_inner_state_projection_active.py `
  tests\test_life2_9_persona_v23.py `
  tests\test_life2_10_persona_release.py `
  tests\test_persona_output_guard.py `
  -q -p no:cacheprovider
```

2026-08-08 实测：`29 passed, 1 warning in 16.06s`。

### 22.2 跨专项唤醒门

```powershell
cd E:\Xiadie\Xiadie1.0\backend
.\.venv\Scripts\python.exe -m pytest `
  tests\test_memory_observer.py `
  tests\test_episode_application.py `
  tests\test_saga_end_to_end.py `
  tests\test_context_assembler.py `
  tests\test_history_recall.py `
  tests\test_kig_r_acceptance.py `
  tests\test_kig_p_acceptance.py `
  tests\test_cie6_acceptance.py `
  tests\test_proactive_orchestrator.py `
  -q -p no:cacheprovider
```

2026-08-08 实测：`80 passed, 1 warning in 18.51s`。

### 22.3 全量、前端和 Electron

```powershell
cd E:\Xiadie\Xiadie1.0\backend
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider

cd ..\frontend
npm.cmd test
npm.cmd run build

cd ..\desktop
node --test tests\lifecycle-contract.test.mjs
node --check main.js
node --check preload.js
```

最近 LIFE2.10 后端全量：`2682 passed, 1 warning in 593.03s`。本次 docs-only 封存未重复十分钟全量。2026-08-08 前端实测 79 passed、构建 190 modules；Electron 3 passed、语法通过。全量必须在最终冻结、公共聊天/SSE/Schema/Provider 改动或有代码冲突的 merge 后运行。

### 22.4 Persona 真实模型评测

只在 Persona/profile、输出门、模型或认证协议变化时运行，写新 artifact，不覆盖历史：

```powershell
cd E:\Xiadie\Xiadie1.0\backend
.\.venv\Scripts\python.exe scripts\run_life2_persona_eval.py `
  --profile v23 --suite v23 --runs 3 `
  --temperature 0 --max-tokens 4000 `
  --out ..\docs\reports\life2-persona-v2.3-resume-<model>-<date>.json
```

用户已授权使用配置好的 DeepSeek 完整测试，不因 token 成本缩减；仍须记录 Provider/model/location/fingerprint、temperature、并发、超时、fixture 和 artifact hash。

### 22.5 Windows、发布与 Git

```powershell
cd E:\Xiadie\Xiadie1.0
.\scripts\test-frozen-backend.ps1 -Port 18756
.\scripts\verify-release-resources.ps1
.\build-windows.ps1
git diff --check
git status --short --branch
```

完整构建只在冻结/发布阶段运行；先确认端口、磁盘、素材和 BGE 来源。未签名安装包和受限 Live2D 不能公开发行。

## 23. 关键文档

| 文档 | 用途 |
|---|---|
| 本手册 | 第一恢复入口 |
| `reports/life2-10-persona-v23-model-gate.md` | v2.3 模型门、证书、测试、回滚 |
| `superpowers/plans/2026-08-01-persona-v2-3-implementation-plan.md` | LIFE2.7～2.11 正式计划；从 2.11 继续 |
| `reports/life2-7-short-memo-active.md` | ShortMemo Active 合同 |
| `reports/life2-8-inner-state-projection-active.md` | Projection Active/只读边界 |
| `reports/life2-9-persona-v23-candidate.md` | v2.2/v2.3 路由 |
| `LIFE_V2_PERSONA_AND_SHORT_MEMORY_PLAN.md` | LIFE v2 总计划 |
| `LIFE_V2_PERSONA_CONTENT_DRAFT.md` | Persona 内容历史，不是直接生产文件 |
| `LIFE_V2_WORLDBOOK_CONTENT_DRAFT.md` / `LIFE_V2_WORLDBOOK_SOURCE_AUDIT.md` | WorldBook 草案与来源 |
| `adr/0072-versioned-persona-profile-routing.md` | Persona 路由 ADR |
| `MEMORY_SYSTEM_DESIGN_FOR_BEGINNERS.md` | Memory B～E |
| `KNOWLEDGE_SYSTEM_OPTIMIZATION_PLAN.md` | Knowledge K.0～K.9 |
| `CONVERSATION_CONTEXT_AND_SUMMARY_PLAN.md` / CTX.7 报告 | 上下文冻结 |
| `LLM_COGNITIVE_DECISION_REFACTOR_PLAN.md` / CDS.13 报告 | CDS 冻结 |
| `LLM_DECISION_AND_LIFE_CONTINUITY_PLAN.md` / LIFE v1 报告 | 主线生活连续性 |
| `XIADIE_KNOWLEDGE_INTELLIGENCE_GOVERNANCE_AND_WORLD_MODEL_PLAN.md` / KIG freeze | KIG/PWM |
| `EMOTION_RELATIONSHIP_AND_PROACTIVE_COMPANION_PLAN.md` / EAP R6 | EAP |
| `KFC_COMPANION_INTERACTION_ENHANCEMENT_PLAN.md` / CIE.6 报告 | CIE/KFC 归属 |
| `SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md` | 唯一写入者、迁移、晋级 |
| `XIADIE_LONG_TERM_ROADMAP.md` | LIFE2.11 后选下一专项 |
| `BASELINE_STATUS.md` / `CODEX_PROJECT_CONTEXT.md` | 历史约束，顶部状态已漂移 |
| `BUILD-WINDOWS.md` / `NOTICE.md` / `LICENSE` | 构建和许可 |

以上均在 `docs/` 下，报告在 `docs/reports/`。恢复时优先使用表中靠前文件。

## 24. 关键提交

| SHA | 含义 |
|---|---|
| `89a6ceda86de074ea41992dd4d9ab20ab2ea5501` | CTX.7 冻结 |
| `6b8aa47134f8a9a55131c73bb1148e6912421c4f` | EAP 完成 |
| `279d614171bd2c10cef9ec1e77808b52e8c3bf11` | CDS 最终冻结 |
| `f16d80ab0d2457065dc65d7d284d3cbf3584f5ee` | LIFE v1 PR #3 合并 |
| `b436e9f8876f8926ac90df3562edbeef3f085413` | KIG PR #4 合并 |
| `3a663391cf12f5a843f4c1d5e311628ce8637c6e` | CIE v1；当前 main |
| `303ce2c02a7c19584a8a28199a2ddf58e61b3a8f` | LIFE v2 施工计划冻结 |
| `39f0e23` | LIFE2.6 技术总验收 |
| `0955867` | 已认证 Persona v2 首次 Active |
| `0124138` | 收口动作旁白 |
| `718ba79` | 收口虚构环境/资料不足污染 |
| `ee05d2a` | UI 与运行日志页 |
| `e4a046f` | 远程摘要披露 |
| `bdf683fa9c347de39e02b1dbb40949d52faeb791` | ShortMemo Active |
| `fad640e5b7499a781c46492432aa9685705df663` | Projection Active |
| `0daf7ac108c85a71ea4de51224da888980c78c6f` | v2.3 版本化候选 |
| `31477908798a31f598e812a8529abdb804fc4736` | v2.3 认证发布 |

短 SHA 仅供阅读；基线和自动化应记录完整 SHA。

## 25. 恢复时禁止事项

- 不从旧 main 开始 LIFE2.11，不在备份前启动可能迁移的新代码。
- 不用 reset --hard、force push 或删除分支清理未知状态。
- 不把实验仓库完成项写成主线完成，不直接复制其数据库。
- 不改写冻结 Schema 48～82 历史迁移。
- 不让模型证书跨 Provider/model/fingerprint 继承。
- 不在 A=0 时开启 WorldBook。
- 不让 Shadow/PWM proposal/模型自述成为事实或工具证据。
- 不混同 Summary、ShortMemo、Memory、Knowledge、PWM 和 LIFE。
- 不为单次措辞大改 Persona。
- 不把隐藏思维、系统提示、密钥或知识/记忆正文写日志。
- 不上传 Live2D、数据库、`.env` 或受限 BGE。
- 不在 ToolRegistry/权限/审计/恢复前接 Shell、浏览器写操作、桌面输入或外部消息。

## 26. 给未来维护者的首轮任务模板

```text
先不要修改代码。完整阅读 docs/PROJECT_PAUSE_AND_RESUME_HANDOFF_2026-08-08.md、
docs/reports/life2-10-persona-v23-model-gate.md 和
docs/superpowers/plans/2026-08-01-persona-v2-3-implementation-plan.md。

只读核对 Git 分支/HEAD/远端/工作树、Schema、Persona/ShortMemo/Projection/
WorldBook/CIE 开关、Provider/model、数据与资源备份。运行文档 22.1 和 22.2，
不跑模型评测，不改数据库设置。

输出恢复差异报告。没有用户明确批准，不进入 LIFE2.11，不合并 main，不删分支。
```

## 27. 本次封存验证

| 检查 | 2026-08-08 结果 |
|---|---|
| LIFE2 定向门 | 29 passed，1 既有 warning |
| Memory/CTX/KIG/CIE/EAP 唤醒门 | 80 passed，1 既有 warning |
| 前端 | 79 passed |
| TypeScript/Vite | 通过，190 modules |
| Electron lifecycle | 3 passed |
| Electron main/preload 语法 | 通过 |
| 数据库只读核对 | Schema 82，开关与第 0 节一致 |
| 后端全量 | 本次未跑；最近 LIFE2.10 为 2682 passed |
| DeepSeek 评测 | 本次未跑；沿用 LIFE2.10 v2.3 750/750 |
| Windows 安装/打包 | 本次未跑；docs-only 不触及资源 |

本次代码范围为零，只增加/更新交接入口。未来陈述验证结果时必须区分本次定向证据与 2026-08-01 历史全量/模型证据。

## 28. 恢复成功标准

- 远端/本地分支已核实，无未知提交被覆盖。
- 数据、资源和密钥有可恢复备份。
- Schema/rollout 差异已解释。
- 两组定向门通过。
- LIFE2.10 Review 有记录，用户授权 LIFE2.11。
- LIFE2.11 只修可复现问题，完成全量门和文档刷新。
- 合并 main 后重新验证，分支删除由用户确认。

在此之前，准确状态是：**主线可运行，LIFE2.10 已发布待 Review，长期停工，LIFE2.11 未开始，尚未合并 main。**

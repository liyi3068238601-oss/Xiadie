# 遐蝶项目基线状态

> 最近复核日期：2026-07-26
>
> 当前施工状态：CDS PR #2 已合并；LIFE.0～LIFE.3 已完成内部施工验证，阶段独立 Review 按用户要求留待 LIFE 总体 Review；Schema 65 为当前版本
>
> 当前版本：`v0.1.0` MVP 骨架（知识库系统 K 系列已完成）
>
> 用途：后续每个小版本都从此处判断”原来能做什么、这次改了什么、有没有退化”。

## 1. 基线结论

当前仓库已经是一套可运行的 Windows 桌面 Agent 骨架，而不是空项目。后端、前端和 Electron 桌面壳均可独立验证，一键开发启动器已经实机运行过。

现阶段适合继续做可靠性和工程治理，不适合立即扩张到浏览器控制、桌面自动化、多 Agent 等高风险能力。

## 2. 验证环境

| 项目 | 当前环境 |
|---|---|
| 操作系统 | Microsoft Windows NT `10.0.26200.0` |
| PowerShell | `5.1.26100.8655` |
| Node.js | `v24.16.0` |
| npm | `11.13.0` |
| Python | `3.12.13`（`backend/.venv`） |
| Electron | `33.4.11` |
| 后端端口 | `127.0.0.1:8756` |
| 前端端口 | `127.0.0.1:5173` |

项目声明的最低开发环境仍为 Node.js 18+、Python 3.10+；上表只是本次验证环境，不代表最低版本承诺。

## 3. 自动验证结果

以下命令最近于 2026-07-26 执行：

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端 | `cd backend; .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | LIFE.0 全量基线通过：2310 passed，1 warning；LIFE.1 相关回归 26 passed，LIFE.2 回归 113 passed，LIFE.3 回归 80 passed；当前 Schema 65，专项完成时重新跑全量 |
| 前端 | `cd frontend; npm.cmd test; npm.cmd run build` | 通过：47 项；TypeScript 检查及 Vite 生产构建 189 modules 成功 |
| Electron / Windows | `node --check main.js; node --check preload.js`；`scripts\test-frozen-backend.ps1 -Port 18756` | 通过：无语法错误；冻结后端 health 与本地 embedding smoke 通过；沿用既有安全 token/IPC/端口边界 |

CDS.12 以 Schema 63 新增三张无正文反馈/校准审计表；CDS.13 未再新增迁移。`eap-decision-run-adapter-v1` 保持兼容。LIFE.0 已冻结施工基线；LIFE.1 在 CDS Registry 注册 6 类 Shadow 决策且不新增运行账本；LIFE.2 以 Schema 64 新增 provenance-aware LifeEvent 唯一账本，并复用 `tool_logs` 作为 performed agent action 的 ToolRun 证据源。KIG 仍只有预留契约。LIFE.0 后端全量基线为 `2310 passed, 1 warning`，后续阶段将在专项完成时重新跑全量。

已知但不阻断当前开发的警告：

- FastAPI 测试依赖中的 `starlette.testclient` 提示未来改用 `httpx2`。
- `pet.html` 以普通脚本方式加载 Live2D Cubism Core，Vite 因此不会将它打包为模块；当前静态资源加载方式下属于预期警告。

## 4. 当前启动方式

### 一键开发启动

双击仓库根目录的 `启动遐蝶.bat`。它会：

1. 检查 Python 虚拟环境和 Electron 运行时；
2. 在需要时启动后端与 Vite 前端；
3. 等待两个本地服务就绪；
4. 启动 Electron；
5. Electron 退出后清理本次启动的子进程；后端在启动器异常消失时也会自行退出。

BAT 通过 Windows Script Host 无窗口拉起启动器，不保留黑色终端。开发日志写入 `%LOCALAPPDATA%\Xiadie\dev-logs`，包括后端、前端、桌面端日志和不含令牌的 `launcher.err.log`。启动器已在 Windows 上完成端到端实机验证，包括异常结束 Electron 后释放 8756/5173 端口。

### 分进程启动

```powershell
# 终端 1
cd backend
.\.venv\Scripts\python.exe run.py

# 终端 2
cd frontend
npm.cmd run dev

# 终端 3
cd desktop
npm.cmd start
```

## 5. 已有能力

### 可追溯记忆候选基础（2026-07-14）

- 正式记忆已迁移为 `memory_fragments`，带来源会话、来源消息、置信度、敏感性和生命周期状态。
- 自动识别不再静默写入正式记忆，只创建 `memory_candidates` 待确认项。
- 记忆页可以修改候选内容和层级，并接受或拒绝。
- 候选和正式记忆可以跳回来源会话并高亮原消息，来源删除后明确显示不可用。
- SQLite FTS5 trigram 已用于相关记忆召回，并限制为 active、enabled、12 条和 2400 字符。
- 实体档案支持类型、别名、标签、概述和当前状态；正式记忆按名称/别名及高置信度句式自动关联。
- 新建实体或新增别名会回补既有明确提及；用户可以手动关联、解除、归档和合并实体。
- 记忆页已有实体、正式 Episode 和正式 Saga 列表与详情管理；Saga 可查看当前阶段、来源时间线、审计状态和事件，并执行带 revision 保护的内容/来源纠错与合法生命周期操作。星图尚未实现。
- Episode 基础已实现：共同实体、7 天窗口和文本重合会产生 2~20 条 Fragment 的待确认候选。
- 用户可修改 Episode 标题、摘要、重要度和 Fragment 选择；接受后继承来源与实体，拒绝和接受均可审计。
- 自主记忆阶段 B.1 协议地基已完成；schema 11 在其上增加观察耗时与受限修复审计字段。
- 自主记忆阶段 B.2 已接通后台幂等队列和真实模型调用：可跟随当前模型或选择独立真实模型，聊天完成事件只入队、不等待观察结果。
- 严格记忆观察协议会校验证据消息、事实覆盖、枚举、数量、重要度上限、隐私与提示注入；格式失败的整个任务最多受限修复一次。
- 自主记忆阶段 B.3 已完成：来源、等值去重、正式 Fragment、实体关系、事件和 applied 状态在单一事务提交，失败整体回滚。
- 自动 Fragment 保留来源会话、用户/助手消息、全部证据、观察理由、版本和幂等键；敏感 Fragment 默认禁用且不创建实体关系。
- 真实模型路径可用时不再创建逐条确认候选；旧关键词候选只在模型不可用或重试耗尽时兜底。旧 pending 数据仍保留，待 B.4 提供兼容管理入口。
- 自主记忆阶段 B.4 已完成：schema 12 区分新建与复用 Fragment，聊天仅对真正新增且启用的记忆显示五分钟限频轻提示。
- 记忆页展示自主记忆的 scope、kind、importance、emotion、理由、观察器版本、证据数量和来源，并支持独立纠错审计。
- 旧候选已移入默认折叠的兼容区；退役条件已固定，当前不删除表、API、历史数据或审计。
- Episode 自动化阶段 C.1 已完成：schema 13 新增独立 Consolidator run 与状态事件账本。
- Episode 整理任务支持幂等排队、查询和协作式取消；当前地基不会调用模型或创建正式 Episode。
- ADR-0013 固定两层短事务、来源集合指纹、单一正式归属、有限恢复和终态保护边界。
- Episode 自动化阶段 C.2 已完成：Consolidator worker 支持原子认领、三次有限重试、陈旧任务恢复和取消安全点。
- Fragment 正式提交后只做稳定幂等入队；应用启动和五分钟空闲窗口会唤醒整理，聊天事务不运行分组。
- 旧“重新分析候选”按钮已改为后台排队；C.2 仍只产生兼容 pending 候选，不自动创建正式 Episode。
- Episode 自动化阶段 C.3 已完成：schema 14 保存实体、文本、时间、情绪/主题四个评分分量和策略版本。
- 分组严格限制为共同实体、最近 7 天、2～20 条且未进入正式 Episode；0.50 及以上产生兼容高分候选。
- 低分分组只保存来源 ID 与评分，七天内幂等重评且不续期；可升格、被新分组取代或到期退出，始终不删除 Fragment。
- Episode 自动化阶段 C.4 已完成：schema 15 保存摘要协议、模型/回退状态、证据 ID、来源哈希和调用审计。
- 模型只能选择 Fragment 原句 claim，程序再拼接摘要；虚构内容、越权/误标 ID、标题无来源和提示注入均拒绝。
- 模型不可用或校验失败时刷新安全抽取式摘要；模型调用期间来源被纠正会由事务内哈希复核阻止旧结果落库。
- Episode 自动化阶段 C.5 已完成：schema 16 为正式 Episode 保存分组、来源集合/哈希、摘要审计和应用协议快照。
- worker 现在自主提交高分候选；Episode、顺序来源、active Entity、候选状态、记忆审计和 run 终态在一个短事务完成。
- 来源变化、归属冲突或中途异常会整批回滚并进入有限恢复；candidate、分组指纹和 Fragment 三层唯一约束防止重复 Episode。
- Episode 自动化阶段 C.6 已完成：schema 17 与专用纠错 API 保存人工纠错说明、时间和独立审计语义。
- 主界面不再展示候选确认，改为正式经历详情、摘要校验状态、短来源指纹、有序来源和原对话入口。
- 纠错标题/摘要会标记 `user_edited` 且不改变来源链；精确阈值、跨日期、继承、幂等和失败回滚完成总验收。
- Saga 阶段 D.1 已完成：schema 18 新增 Saga、Saga-Episode、Saga-Entity 和 Saga 事件四张正式表。
- ADR-0018 固定只消费正式 Episode、单一有效 Saga 归属、增量摘要失败保持旧值、精确生命周期和禁止反向改写来源的边界。
- Saga 阶段 D.2 已完成：schema 19 新增最小 Saga 分组候选账本，候选不复制 Episode 摘要或 Fragment 正文。
- `saga-group-v1` 使用 Entity、文本、时间和叙事连贯性四分量，以及跨自然日、180 天总跨度、60 天相邻间隔和双层主题硬门槛。
- 候选支持稳定指纹、低分观察、21 天终态过期、纠正后晋级和跨 Saga 冲突记录；不会修改 Episode 或创建正式 Saga。
- Saga 阶段 D.3 已完成：schema 20 保存受来源约束的摘要、生命周期候选信号、整链哈希、token、修复标记和独立摘要事件。
- `saga-summary-v1` 校验 Saga→Episode→Fragment 双层来源链；current_stage 必须来自最新 Episode，摘要必须覆盖起点和最新发展。
- 模型只可逐字选择 Episode 事实；非法结构最多修复一次，虚构、注入、错误完成证据和来源变化改用当前来源安全回退或拒绝。
- Saga 阶段 D.4 已完成：schema 21 新增 Saga Consolidator run/event 账本和候选应用审计字段。
- Episode 落库后只稳定入队；Saga worker 串行整理、三次有限重试、陈旧恢复、协作取消和六天周级懒调度均不阻塞聊天。
- 新建和增量追加会在单个短事务内复核正式 Episode、单一 Saga 归属、候选指纹、旧来源与整链摘要哈希，并一起提交来源、Entity、事件和 run 终态。
- Saga 阶段 D.5 已完成：schema 22 新增完成证据、生命周期 revision 和只读关系 delta 建议账本。
- 精确状态守卫禁止非法跳级、自动 tombstone 和 tombstone 恢复；可信新 Episode 可使 completed Saga 恢复 active。
- 正文纠错保持来源链，来源归组纠错重算分组指纹、抽取摘要、Entity 和整链哈希；所有写 API 使用乐观 revision。
- Saga 列表、详情、时间线、来源、事件、摘要模型及 Consolidator run/cancel API 已开放；D.6 正式界面采用最近 100 条上限、非颜色状态文字、窄屏布局和来源对话入口。
- Saga 写操作遇到 revision 冲突会刷新最新详情并要求用户重新确认，不会自动重放；来源纠错会重建有来源的安全摘要。
- 旧 Episode 候选的列表/接受/拒绝前端客户端已移除；生成入口与后端兼容 API 暂时保留，退役条件见 ADR-0023。
- Archivist 阶段 E.1 已完成 schema 23 数据地基：Fragment 具备真实召回时间/计数、cooling/frozen 时间、生命周期策略版本和 revision。
- `memory_recall_events` 以同轮 context 唯一约束预防重复计数；`memory_lifecycle_events` 保存无正文的状态、评分分量、原因和策略审计。
- E.1 尚未执行保留评分、自动冷却、冻结、恢复、归档或物理清理；这些必须在 E.2～E.6 分阶段验收后才启用。
- Archivist 阶段 E.2 已接入真实上下文注入计数：同一用户消息的首次生成、流式重试和 regenerate 共用 context key，只有新的对话轮次才再次计数。
- `fragment-retention-v1` 以纯函数输出 importance、饱和召回、180 天 recency、关系意义、active Saga、confidence 和重复惩罚七个分量；不读取即时情绪轴。
- 保护快照用一个 SQL 识别正式 Episode、active Saga 和 anchor，并明确保护 L0、稳定边界、当前纠错事实、活跃 Saga 锚点和未完成计划。
- E.2 仍不执行自动冷却、冻结或恢复；duplicate penalty 已有评分输入与上限，实际重复/冲突识别留给 E.6。
- Archivist 阶段 E.3 已完成 Fragment 精确转换：active 满 14 天且分数 `<0.45` 才可 cooling；cooling 额外满 30 天且分数 `<0.30` 才可 frozen，保护对象不会自动降温。
- schema 24 增加 `fts_indexed` 并使用状态感知 FTS 触发器；frozen 会原子退出派生索引，强相关真实召回、新证据或用户操作可恢复 active 并重建索引。
- 每次转换与 revision、进入时间、评分分量及无正文事件在同一短事务提交；Archivist 永不自动 tombstone，删除与隐私清除仍是独立用户路径。
- E.3 只提供确定性评估/恢复能力，定时扫描、任务账本和预算控制留给 E.4 worker。
- Archivist 阶段 E.4 已完成 schema 25 任务/事件账本与后台 worker：启动和空闲时仅在距上次成功至少 20 小时后懒入队，同一时间窗口幂等。
- worker 复用串行原子认领、最多三次指数退避、五分钟陈旧恢复、协作取消和优雅停机；每个 Fragment 仍由 E.3 独立短事务评估。
- 单轮默认最多扫描 50 条、转换 10 条、运行 2 秒、模型调用 0 次；只扫描已到 14/30 天评估点的 Fragment，并优先更久未评估、最久未召回内容，避免受保护记录长期饿死后续候选。
- `/api/archivist/runs` 提供手动入队、列表、详情事件和取消审计；当前 worker 不调用模型、不触碰 Episode/Saga，慢生命周期留给 E.5。
- Archivist 阶段 E.5 已完成 schema 26：Episode 具备 active/completed/archived/tombstone 四态、revision、状态时间和无正文事件审计；旧 Episode 行与来源外键保持不变。
- Episode 满 180 天只进入成熟评估，再满 180 天才进入归档评估；重要度至少 8、近 180 天来源真实召回或 active Saga 来源会继续保护。
- completed Saga 只有完成后稳定至少 365 天、revision 未变化、整链哈希有效、无高重要度/近期召回/待追加候选时，才由 Archivist 自动归档；active 和 tombstone 永不自动变化。
- 慢生命周期复用既有 Saga 六天懒调度，但 Episode/Saga 各有 10 条独立预算；Fragment frozen 不删除 Episode/Saga 来源关系。
- 旧 Episode candidate 兼容 API 的 ADR-0023 退役条件尚未全部满足，本阶段不删除表、API 或历史来源。
- Archivist 阶段 E.6 已完成 schema 27：只在共享 active Entity、相同 scope/可变 kind 的正常 active
  Fragment 小集合内建立 `superseded` 或 `possible_conflict`，保存方向、置信度、规则/版本和无正文事件；
  检测与人工处置均不自动覆盖正文或改变生命周期。
- Archivist run 现在分别记录 revision 并发 `conflict_count` 与新增关系 `relation_count`；管理页展示
  Fragment 保留分、保护原因、状态事件、关系处置与最近维护结果，并支持受 revision 保护的恢复。
- Episode 管理页展示 active/completed/archived 四态与生命周期事件；Fragment 隐私清除和 Episode
  tombstone 都要求二次确认并说明本地/外部备份边界。后端 235 项、前端 16 项和生产/Electron 检查通过，
  记忆系统阶段 E 已完成。
- 用户文件知识库 F.1 已完成 schema 28：`knowledge_collections`、`knowledge_documents`、可取消
  `knowledge_import_runs` 与无正文事件表已建立，默认 collection 可重复初始化，旧 memory 数据不被迁移改写。
- ADR-0029 固定用户知识、相处记忆和内置 Lore 三域隔离；首批只接受用户明确选择的 UTF-8 TXT/Markdown，
  默认仅本地解析/FTS，远程 Embedding 必须逐次披露同意，敏感文档默认禁止远传。
- F.1 只建立数据地基，没有读取或复制真实文件；该边界已由下面的 F.2 安全准入和 F.3 本地解析实现取代。
- 用户文件知识库 F.2 已实现受令牌保护的 TXT/Markdown 原始字节导入：只处理用户本次明确选择的文件，
  联合校验文件名、扩展名、MIME、UTF-8/BOM、文件头、10 MiB 字节和 200 万字符上限，不保存绝对原路径。
- 文档数 100、原文件总量 250 MiB 在 SQLite 写锁中检查；相同 collection 的重复哈希幂等返回已有文档，
  同名不同内容保留独立指纹。原文使用随机 storage key、临时文件和原子替换保存，失败不留伪记录。
- 用户文件知识库 F.3 已完成 schema 29：后台 worker 会复核原始 SHA-256，确定性解析 TXT/Markdown、统一
  换行并提取非代码围栏内的 ATX 标题；正文只写入应用私有目录的 JSON 中间产物，数据库/API/事件只保留
  随机键、版本、哈希和计数。
- worker 支持串行认领、三次有限重试、指数退避、陈旧恢复、协作取消和停机恢复。取消及失败不会生成可
  检索内容；解析成功后任务停在 `queued/chunking`，明确等待 F.4，尚未建立 chunk 或索引。
- 文件页展示真实解析阶段、进度、恢复状态、无正文事件时间线和取消操作。当前后端 269 项、前端 19 项及
  生产/Electron 检查通过。
- 用户文件知识库 F.4 已完成 schema 30：结构优先切片器只读取私有解析 artifact，Markdown 按标题和段落、
  TXT 按段落切分；目标 800、硬上限 1200 字符，超长段落按句末再按硬边界确定性回退，切片不重叠。
- `knowledge_chunks` 保存稳定 ordinal/ID、标题路径、段落/行/字符范围、内容哈希和可空页码。TXT/Markdown
  不伪造页码；整组切片在一个事务中替换，失败无半成品，删除 document 时自动级联。
- 取消会清理切片和解析派生数据但保留原文件；成功后任务停在 `queued/indexing`，文档仍非 indexed，等待
  F.5 本地 FTS。文件页展示真实切片状态和计数。当前后端 282 项、前端 20 项及生产/Electron 检查通过。
- 用户文件知识库 F.5 已完成 schema 31：contentless FTS 只保存中文单/双字和英文/数字派生词项，不复制
  chunk 正文；索引成功后 document 原子进入 indexed，run 完成，词项行数必须与 chunk_count 一致。
- 本地检索支持 collection/document/标签过滤、主结果上限、字符预算、去重和可选相邻 ordinal 上下文；每段
  保持真实 locator。只有 indexed 文档和 active collection 可见，状态禁用立即退出召回，chunk 删除触发器
  清除 FTS 残留。标签元数据已具备，编辑入口留到 F.7。
- 文件页展示索引中与“已索引 · 可检索”；该 F.5 基线验收为后端 293 项、前端 21 项及生产/Electron 检查通过。
- 用户文件知识库 F.6 已完成 schema 32：只有显式资料意图确定性触发本地检索，命中正文进入不可执行的低权限
  JSON 资料区块；用户知识、Lore 和相处记忆分别计量，审计不保存查询或正文。
- 模型引用受本轮 `K1…Kn` 白名单约束，伪造标记在落库前失效；消息只保存实际使用引用的来源定位快照。点击
  来源会重新核对当前 indexed 状态、索引版本、chunk 和正文哈希，资料变化或删除后不会用旧快照冒充原文。
  当前后端 298 项、前端 22 项、生产构建和 Electron 语法检查通过。
- 用户文件知识库 F.7 已完成 schema 33：文件页具备真实搜索、collection/状态筛选、来源详情、标签、处理重试、
  索引重建、删除与无查询正文的检索审计；同名文件以内容指纹区分。
- 删除请求提交时立即进入 `delete_pending` 并退出召回，后台清除应用内原文、解析 artifact、切片、FTS 和文档行；
  失败进入 `delete_failed` 且仍不可检索，只有用户明确重试才继续。外部原文件/备份不受影响，引用快照保留但来源
  返回 410。当前后端 305 项、前端 24 项、生产构建和 Electron 语法检查通过。
- 用户文件知识库 F.8 已完成 schema 34：解析注册表支持 TXT/Markdown/PDF/DOCX；PDF 保留真实页码，DOCX
  保留标题、段落和表格但不伪造页码，损坏二进制容器使用格式专属错误码。
- 工作区 BGE-M3 量化 ONNX 作为惰性本地 dense provider，输出归一化 1024 维向量；FTS 与向量独立版本化并用
  RRF 混合。模型或推理失败自动退回 FTS，远程 provider 无本次明确同意时拒绝发送正文。
- 重建/删除会立即清除向量并终止陈旧任务，物理删除再次校验和级联；PDF API 全链 E2E 与真实模型冒烟通过。
  模型本体保持 Git 忽略，Windows 构建时从外层 `bge-m3` 复制进安装资源。后端 317 项、前端 25 项、生产构建、
  PyInstaller 冻结与 unpacked 资源哈希验收通过，知识系统阶段 F 已完成。
- 知识库优化 K.0～K.9 已完成：旧用户默认保持 explicit，可选择 off 或 smart；smart 只在确定性 high 时自然注入，
  ask_each_time/local_only 仍必须在本地预检并执行一次性授权，unknown Provider 按 remote 处理。
- 检索使用 query 清理、FTS+dense 混合候选、确定性重排与多样性预算；知识正文进入低权限引用区，引用必须通过
  本轮白名单与当前来源哈希核验。向量失败安全降级到 FTS，预检/观察器故障不破坏普通聊天。
- schema 41 已固定 collection 默认策略、无正文召回/grant/retrieval 审计生命周期、引用安全最小化、元数据清单和
  完整清除闭环。知识观察结果分为 reference/decision/discarded，资料事实不能伪装成共同经历写入相处记忆。
- K.9 总验收为后端 404 项、前端 33 项、生产构建 188 modules；冻结后端真实进程、本地 BGE-M3 指纹、Electron
  unpacked 和 NSIS 安装器均通过。未签名个人构建边界与正式签名恢复条件见 ADR-0044。

### 人格与内置背景知识（2026-07-14）

- 遐蝶的人格核心、关系原则、说话方式和沉浸边界已纳入每轮对话的系统提示词。
- 长篇角色背景已从人格核心中拆出，保存为内置 Markdown 背景知识，并按当前消息的关键词检索相关章节。
- 背景知识、角色亲历记忆和用户对话记忆已明确分层，避免把设定误当成与当前用户共同经历的事实。
- 目前的内置背景检索是轻量关键词方案；用户文件知识库已有本地词法 FTS、显式意图对话召回和可验证引用，
  并已增加本地 BGE-M3 dense 与 FTS 混合语义检索。

### 情绪与关系状态阶段一至四（2026-07-15）

- 已拆分短期 `affect_state` 与长期 `relationship_state`，不再混用关系熟悉度和联系需求。
- 已实现确定性时间漂移、五分钟分步积分、情绪簇、遐蝶基础语调、主动信号和状态事件日志。
- 聊天成功后才提交保守本地状态变化；模型失败不会增加互动次数或关系值。
- 统一状态、手动 tick 和事件读取 API 已完成；右栏心境、系统提示词和 Live2D 表情现已读取同一后端快照。
- 完整 9×5 遐蝶语调网格已实现：九个情绪簇与五个距离档组合，并带 contact_need 附加层、未知簇回退和固定对话回归检查。
- 受控主动联系尚未实现。
- 状态懒推进、互动提交和开发 tick 已增加原子写事务；聊天成功时会在最新状态上重新应用互动，避免流式期间的状态覆盖。
- contact_need 已按 1/8/24/72/168 小时模拟重新校准：一天不直接联系、三天优先转移活动、七天才形成联系信号；用户回复后按等待程度比例回落。
- 旁观观察器阶段 2.1 协议层已完成：严格版本化 schema、逐字证据、字段限幅、低置信度降级和 trust 边界校验已具备。
- 旁观观察器阶段 2.2 已建立受限非流式模型调用、幂等候选审计和失败恢复入队基础。
- 旁观观察器阶段 2.3 已改为可靠后台 worker：聊天只入队，最多三次指数退避；设置页可选择独立轻量模型，净化候选与事件在同一事务原子应用。
- 测试数据目录由 `tests/conftest.py` 在模块收集前统一隔离，测试文件顺序不会再接触开发数据库。
- Saga 阶段 D.1～D.6 已完成数据地基、预筛、事实摘要、后台任务、原子应用、生命周期、纠错、API、正式界面与总验收；相对日期校正、矛盾检测和星图尚未实现。

- Electron：透明置顶桌宠、主窗口、托盘、右键菜单及基础 IPC 联动。
- Live2D：固定模型加载、动作和状态气泡；九个后端情绪簇控制表情，工作模式独立控制动作，资源缺失时有占位降级。
- 聊天：会话管理、SSE 流式输出、复制、收藏、重新生成和错误提示。
- 模型：内置 mock，以及多种 OpenAI-Compatible 供应商配置、连接测试和模型切换。
- 本地 API：除最小健康检查外均校验会话级随机令牌，CORS 仅允许明确的本机来源。
- 数据：SQLite 本地保存会话、消息、记忆、任务、设置和工具日志。
- 记忆：L0/L1/L2 分层、查看、修改、删除、禁用和保守自动抽取。
- 长期记忆默认开启；用户可随时关闭，显式关闭状态在重新初始化或升级后保持不变。
- 任务：创建、状态流转、今日任务及聊天来源记录。
- 权限：已有 S0-S4 展示与审计视图，但尚未接入真实工具执行链。

## 6. 仍是占位或待实现的能力

- 工具系统只有权限说明和日志页面，尚无统一 `ToolRegistry` 与真实执行闭环。
- 面向用户文件的知识库已形成导入、解析、稳定切片、本地混合检索、对话引用、管理和可验证删除闭环；
  扫描 PDF OCR、表格文件与图片资料尚未实现。
- 没有工作区、文档产物和任务执行状态机。
- 没有浏览器操作、外部平台连接、桌面自动化和多 Agent 编排。
- 语音、正式安装包升级、安全存储迁移和完整发布流程尚未完成。

详细版本顺序以 [长期路线图](XIADIE_LONG_TERM_ROADMAP.md) 为准。

## 7. 已知风险与技术债

| 类别 | 当前情况 | 后续处理 |
|---|---|---|
| 本地 API | 临时令牌与严格来源策略已覆盖开发/冻结启动；正式安装升级后的重启链路仍需实机回归 | 正式发布验收时复核安装、重启和升级 |
| 密钥 | API Key 存在本地 SQLite 中，接口不回显但存储未加密 | 迁移到 Electron `safeStorage` 或系统凭据存储 |
| 重新生成 | 已改为新回复成功持久化时再替换旧回复，并有失败回归测试 | 后续版本化回复时再扩展历史保留策略 |
| 上下文 | CTX.0～CTX.7 已完成总验收并通过独立 strict review；5/20/100/500 轮满足硬预算，当前用户消息受保护，摘要、跨会话历史、长期记忆、知识和 Lore 保持独立来源与优先级 | schema 45 与上下文 v1 已冻结；普通自动历史召回继续 shadow |
| 情绪与关系 | `affect_state`、`relationship_state`、确定性积温、旁观观察器、9×5 语调网格、统一前端/Live2D 状态源已经可运行 | 进入情感意义、经历协同与受控主动陪伴专项；不重建现有内核 |
| EAP 主动陪伴 | Schema 48～60、六个 EAP 协议、DecisionRun、生产 Orchestrator、grounded Feedback、Level 0～4 Delivery 与 Electron 通道已落地；长期生产模拟覆盖 15 分钟至 30 天及完整失败矩阵 | R0～R6 已通过独立 strict review（0 P0/P1）并正式冻结；真实本机投递仍为显式实验开关且默认关闭，Level 5 硬禁用。不兼容变更必须升协议版本 |
| CDS 认知决策 | CDS.0～CDS.10 已完成并经过各阶段 strict review；2026-07-26 后续审计收紧 CDS.10 Episode/Saga 语义动作矩阵与 CDS.6 SSE final 一次性交付 | 9 个 DecisionKind 仍全部最高为 Shadow，领域 application owner 不变。CDS.10 的 8 条未独立评审叙事样本 accuracy 仅 50%，不得据此晋级；当前停在 CDS.10，尚未进入 CDS.11 |
| 会话摘要/历史 | schema 45 与 v1 协议已冻结；摘要六类样本 6/6、显式历史召回固定集 4/4，重复手动重建已幂等合并；普通自动召回仍为 shadow | 只有取得明确授权的校准样本并另立 ADR 才考虑解除 shadow |
| 模型设置 | provider/model 选择的服务端校验较弱 | v0.1.2 增加校验与错误恢复 |
| 数据演进 | SQLite 已有顺序 schema 迁移并到达 62，但尚无独立迁移 CLI、降级和备份恢复工具 | Schema 48～60 均保持不可变；Schema 61/62 分属 CDS.1/CDS.2，当前下一可用号为 63；正式发布前补齐备份、恢复与迁移演练 |
| Live2D 授权 | 当前模型只允许个人使用，禁止上传、再分发、商用和二改 | 仓库继续忽略资源；发布前更换为可发布模型 |
| 发布 | 2026-07-22 重新构建未签名 NSIS（564,038,879 bytes），frontend/backend/Lore/BGE-M3 资源与哈希验收通过；真实 Electron UI 通过。现有 8756 健康监听者阻止了同轮安装目录启动 | v1.0 前重新启用签名，并在释放端口后补安装/卸载/升级验收 |
| 知识模型体积 | 本地 BGE-M3 使安装资源增加约 543 MiB | 发布前评估可选下载；缺失时继续使用 FTS |
| 外部 Provider | 授权协议用 mock/受控流完成矩阵，未把测试正文发送给真实在线供应商 | 各供应商接入时单独做不含私密正文的网络兼容回归 |
| Token 估算 | CTX.7 未读取真实聊天；当前没有经用户明确提供的 Provider usage 样本，误差百分比未实测 | 保留保守估算与已验证窗口；以后仅用用户显式样本输出无正文聚合误差 |

## 8. 数据与资源边界

- 开发数据默认位于 `backend/data/xiadie.db`，已被 Git 忽略。
- `frontend/public/models/` 与 `frontend/public/libs/` 已被 Git 忽略，不得提交当前受限 Live2D 模型和运行时资源。
- `.env`、日志、虚拟环境、`node_modules` 和构建产物均不进入版本库。
- 用户数据的备份、导出、迁移和删除策略仍需后续版本正式设计。

## 9. 基线更新规则

只有出现下列情况才更新本文：

- 最低或推荐运行环境改变；
- 启动方式、端口或数据位置改变；
- 自动验证命令改变；
- 一项占位能力变成真实可用能力；
- 新增会影响后续开发的已知风险。

普通功能进度记录到版本计划、提交记录或 PR，不把本文写成流水账。

# 遐蝶 KFC 能力归属与陪伴交互增强专项计划

- 计划代号：CIE（Companion Interaction Enhancement）
- 版本：v0.2
- 日期：2026-07-28
- 参考对象：`tt-P607/kokoro_flow_chatter@d857f4f`；本地只读 ZIP 格式参考包 `E:\Xiadie\kokoro_flow_chatter-2.1.1\kokoro_flow_chatter-2.1.1.mfp`（KFC 2.1.1）
- 状态：开工准备完成；等待 KIG Draft PR [#4](https://github.com/liyi3068238601-oss/Xiadie/pull/4) 合并后锁定 predecessor merge SHA，再进入 CIE.0；当前不得开始 CIE.1 或占用迁移号
- 执行规则：每阶段完成代码、测试、文档、独立 Review 和独立提交后，才能进入下一阶段

## 1. 目标

吸收 KokoroFlow Chatter（KFC）适合陪伴体验的产品思想，并把本地开源包作为 CIE 设计与施工时的只读代码参考。KFC 使用 AGPL-3.0；遐蝶默认采用独立设计与实现，任何源码级复用都必须先完成许可证兼容性决策。

CIE 关注“用户如何连续地与遐蝶交互”，不重建已经冻结的 CTX、EAP、CDS、LIFE 或 KIG 领域内核。它必须复用：

- CTX 的硬预算、滚动摘要和来源分层；
- EAP 的 Presence、候选、最终授权、表达、投递与反馈账本；
- CDS 的 DecisionKindRegistry、模型路由、预算、取消和来源复核；
- LIFE 的生活连续性、短期意图与结构化心理状态；
- KIG 的跨源权限、新鲜度、证据与外部贡献治理。

## 2. 当前覆盖基线

评分含义：80～100 为接近可用，25～79 为部分具备，0～24 为基本缺失。

| KFC 核心能力 | 当前覆盖 | 唯一所有者 | 决策 |
|---|---:|---|---|
| 心理活动流 | 60% | LIFE/EAP 现有状态；专用持久对象归未来 LIFE v2 | CIE 只读现有结构化来源；不新增 InnerStateEvent，不保存完整内心独白或 chain-of-thought |
| 近期记忆压缩 | 90% | CTX | 保持 `conversation-summary-v1`；不以第一人称虚构事实 |
| 私人备忘录 | 0% | LIFE v2 候选 | LIFE v1 未实现 ShortMemo；不作为 CIE 前置条件，也不得由 CIE 越权补建 |
| 等待与超时 | 80% | EAP | 复用 Presence/open thread/due/expiry；只在发现真实缺口时提 `proactive-decision-v3` |
| 主动发起 | 95% | EAP | 不重建；继续由最终硬门、投递和反馈状态机裁决 |
| 消息积累窗口 | 0% | CIE | CIE 新建 TurnIngressBuffer，不改长期记忆或 EAP 候选 |
| 生成打断 | 10% | CIE；CDS 提供取消契约 | 现有 governor 只抢占未开始的低优先级认知任务；CIE 实现活动聊天请求的前后端协同取消、合并和旧回复保留 |
| 原生图片多模态 | 10% | CIE；KIG 管跨源治理 | 独立传输授权、能力探测、大小/数量/生命周期门禁 |
| 回复节奏 | 35% | CIE；表达协议仍归 EAP | 首版只做客户端表现；语义拆分需新 `expression-plan-v2` 和 ADR |
| 第三方上下文注入 | 45% | CIE 接口；CTX/KIG 最终裁决 | 只接受结构化 ContextContribution，不接受自由 Prompt 拼接 |

当前等价覆盖约 42%。已接近可用的摘要、等待和主动发起不重复施工；文本附件不等于原生图片多模态，低优先级认知任务抢占也不等于活动 LLM 生成取消。

## 3. 施工顺序

```text
CDS v1 / Schema 63 已冻结
  ↓
LIFE v1 / Schema 71 已冻结
  ↓
KIG v1 / Schema 80 已完成，Draft PR #4 待合并
  ↓
锁定 PR #4 merge SHA 与 main 测试基线
  ↓
CIE.0～CIE.6 独立施工
```

CDS 只提供取消、优先级、模型/来源验证接口；CIE 不改其决策内核。LIFE v1 没有 `InnerStateEvent` 或 `ShortMemo`，二者已降为未来 LIFE v2 候选，不阻塞 CIE，也不得由 CIE 偷建平行状态。EAP v1、CTX v1、LIFE v1 与 KIG v1 均保持冻结。

## 4. 不可变安全边界

1. 不保存、展示或要求模型输出完整 chain-of-thought；只保存枚举状态、用户可理解摘要、证据引用和 reason code。
2. ShortMemo 不是长期记忆、Goal、ImportantDate 或任务；到期删除不得影响领域事实。
3. 新消息打断必须先确认旧请求进入可取消段；已经进入原子写入或投递段的任务只能完成或回滚。
4. 消息合并必须保持每条原始消息 ID、顺序、时间和附件授权，不能只保留拼接正文。
5. 图片默认本地暂存；发往远端 Provider 前逐次显示位置、模型、数量与用途并取得授权。
6. 第三方贡献只能提交有界类型、来源 revision/hash、TTL、敏感等级和候选内容；CTX/KIG 有权拒绝、裁剪或降级。
7. 回复节奏不得篡改模型语义，不得让已经确认投递的文本重复发送。
8. 任何 CIE 功能失败都回到当前单消息、单生成、纯文本流式聊天路径。

## 5. LIFE v2 候选项（不阻塞 CIE）

仓库审计确认 LIFE v1 已冻结于 Schema 71，且不存在以下两个专用对象。它们保留为未来 LIFE v2 的产品候选；CIE 只能读取现有 Affect、Relationship、Episode、Saga、LifeEvent、Goal 与 Memory 接口，不能实现或写入本节对象。

### 5.1 `structured-inner-state-v1`

建议对象：

```text
InnerStateEvent
├─ event_id / session_id / occurred_at
├─ state_kind: emotion | expectation | open_thread | uncertainty | recovery
├─ state_code / intensity_band
├─ evidence_refs[] / source_snapshot_hash
├─ user_visible_summary（可选、限长、不得含隐藏推理）
├─ expires_at / superseded_by
└─ protocol_version
```

只允许 LIFE 写入；EAP 可读取 expectation/open_thread，CDS 可在 Shadow 中读取候选，CTX 只在预算允许时注入最近的可见摘要。

### 5.2 `short-memo-v1`

```text
ShortMemo
├─ memo_id / owner_scope
├─ content（限长）/ reason_code
├─ source_snapshot[] / snapshot_hash
├─ created_at / expires_at（1h～14d）
├─ status: active | expired | deleted | promoted_candidate
└─ protocol_version
```

- 上限 10 条；幂等 upsert；过期自动清理。
- 模型只能创建候选；程序核验来源、TTL 和敏感内容。
- 不自动晋升长期记忆；需要晋升时走 MEM 候选和用户控制。
- 临时聊天不得生成持久 ShortMemo。

## 6. CIE 分阶段计划

### CIE.0：交互基线与固定评测集

- [ ] KIG PR #4 合并后，以 `main` merge SHA 锁定 ConstructionBaseline；合并前不得填写猜测 SHA。
- [x] 预备基线：KIG-P 最终实现/回滚点 `96021838418d5c5d9d26b269784447a099a68cc3`，最终 Schema 80；CIE 首个可用迁移号暂定 81，CIE.0 不预占迁移。
- [x] 预备测试基线：后端 `2560 passed, 1 warning`、前端 `52 passed`、Vite 190 modules、Electron lifecycle contract `3 passed`。
- [x] 冻结 fallback：当前单消息、单生成、纯文本 SSE 路径；文本附件继续按现有本地解析路径工作，不宣称 vision。
- [ ] 建立连续消息、打断、附件、回复节奏、第三方贡献的合成评测集。
- [ ] 记录当前发送成功率、首 token 延迟、取消率、重复回复率和正文泄漏率。
- [ ] 设立单一 `cie_enabled` feature flag，并验证关闭时与冻结 fallback 行为一致。

### CIE.1：消息积累窗口

- [ ] 实现 `TurnIngressBuffer`，默认窗口 300～800 ms，可配置但有上限。
- [ ] 原始消息分别持久化，再生成仅用于本轮的有序 turn envelope。
- [ ] 附件授权逐项保留；不同授权范围不得静默合并。
- [ ] `/stop`、明确发送、语音结束等边界立即封口。
- [ ] 多会话和多窗口严格隔离。

完成门：丢消息率 0；跨会话串流率 0；重复处理率 0。

### CIE.2：生成打断与重建

- [ ] 前端引入 AbortController 和“停止/补充消息”交互。
- [ ] 后端引入 request cancellation token、阶段标记和幂等 nonce。
- [ ] 新消息到达时只取消仍处于可取消段的 LLM/低优先级任务。
- [ ] 旧回复未成功持久化时直接丢弃；已持久化时保留版本而非覆盖。
- [ ] 合并新消息后重新执行知识授权、来源快照与候选验证。

完成门：取消后幽灵回复率 0；重复持久化率 0；旧回复误删率 0。

### CIE.3：原生图片多模态

- [ ] Provider/model 能力探测必须证明 vision 可用，不能依赖名称猜测。
- [ ] 本地解析元数据、尺寸、MIME 和 hash；限制单轮数量、像素和字节。
- [ ] 远端逐次授权，明确 Provider 位置与用途；临时文件按 TTL 删除。
- [ ] 模型不支持或用户拒绝时回退本地 OCR/描述候选或明确提示，不伪装已看图。
- [ ] 图片不得进入长期记忆、知识或日志，除非另有明确授权。

### CIE.4：回复节奏与输入状态

- [ ] 客户端优先实现流式输入状态和视觉节奏，不修改语义文本。
- [ ] 句子拆分必须保护代码块、URL、引用、数字和 Markdown。
- [ ] 用户新消息到达时停止尚未展示的分段。
- [ ] 若需要模型输出表达计划，提出 `expression-plan-v2`，不得改写冻结 v1。

完成门：文本重组差异率 0；重复发送率 0；代码块破坏率 0。

### CIE.5：第三方 ContextContribution

- [ ] 定义 `context-contribution-v1`：source、kind、revision/hash、TTL、privacy、priority、token estimate、candidate payload。
- [ ] 禁止第三方直接追加 system/developer Prompt。
- [ ] KIG 执行权限、新鲜度与证据检查，CTX 执行最终预算裁剪。
- [ ] 单一贡献者超时、异常或注入攻击不影响其他来源和聊天。
- [ ] 提供只读无正文诊断和逐贡献者开关。

### CIE.6：整体验收与冻结

- [ ] 5/20/100/500 轮连续消息与打断回归。
- [ ] 本地/远端、在线/断网、前后台、休眠恢复、时钟回拨矩阵。
- [ ] 图片授权、撤回、过期、Provider 位置变化与模型切换矩阵。
- [ ] 第三方贡献恶意正文、超预算、过期来源和重复 ID 矩阵。
- [ ] Windows Electron 实机验收。
- [ ] 独立 Review 0 个未解决 P0/P1 后冻结。

## 7. 指标

```text
消息丢失率                    = 0
跨会话合并率                  = 0
取消后幽灵回复率              = 0
重复回复/重复持久化率         = 0
未授权图片远传率              = 0
不支持 vision 却声称已看图率  = 0
第三方自由 Prompt 注入率      = 0
过期贡献应用率                = 0
完整内心推理持久化率          = 0
任一 CIE 失败影响基础聊天率    = 0
```

体验指标另行报告首 token 延迟、合并等待增量、取消响应时间、分段自然度和用户反馈；不得以体验平均值掩盖上述零容忍安全指标。

## 8. 本地源码参考与许可证边界

KFC 2.1.1 本地包位于 `E:\Xiadie\kokoro_flow_chatter-2.1.1\kokoro_flow_chatter-2.1.1.mfp`，保留在项目目录外，仅作为只读参考，不解包或提交到遐蝶仓库。已直接读取归档内 `LICENSE` 与 `manifest.json`，二者均确认许可证为 AGPL-3.0；遐蝶 MIT 声明不覆盖该外部包。CIE 设计和施工可以重点审查其：

- turn/phase 状态机、未读消息策略、打断控制器与请求视图；
- memo、等待、主动触发、上下文来源和多模态的控制流；
- 失败路径、并发边界、兼容适配与测试场景。

参考过程必须形成“需求或边界 → KFC 行为观察 → 遐蝶现有所有者 → 独立实现”的简短溯源记录，优先复用遐蝶已经冻结的 CTX/EAP/CDS/LIFE/KIG 协议，不能为了贴近 KFC 建立平行内核。

KFC 为 AGPL-3.0。默认允许阅读源码、比较行为、学习状态机和推导自有测试场景；不得逐字复制 Prompt、资源、测试或实现代码。若未来确有必要复用源码片段，必须在写入前新增许可证 ADR，明确分发方式、网络使用义务和整个项目的许可证影响，经确认兼容后才可施工。

## 9. CIE 开工准备记录（2026-07-28）

### 9.1 当前代码缺口

- `ChatView` 在生成期间整体 busy，`streamChat` 没有 `AbortSignal`；当前不能积累新消息或取消活动生成。
- 后端 `/api/chat` 是单请求 SSE，只有 CDS governor 对未开始低优先级任务的抢占，没有聊天 request phase/cancellation token。
- 现有附件是本地提取文本后注入 `attachment_block`；尚无图片字节生命周期、vision 能力实证或逐次远传授权。
- 流式 delta 直接拼接显示；没有不改语义的客户端分段/节奏状态机。
- CTX/KIG 已具备预算、来源、新鲜度与证据治理，但尚无第三方 `context-contribution-v1` 接入协议。

### 9.2 KFC 行为观察到遐蝶独立设计的映射

| KFC 只读观察 | 遐蝶现有所有者 | CIE 独立设计约束 |
|---|---|---|
| `phase_machine.py` 区分等待、模型、工具、提交相位 | CDS/Tool/聊天事务 | CIE.2 自建有界 request phase；不复制枚举或 KFC 状态机代码 |
| `interrupt_controller.py` 轮询未读并取消 LLM | CDS 取消契约、CIE 流控制 | 使用前后端 AbortSignal + 服务端 cancellation token；真实用户消息优先，主动触发不能误取消 |
| `unread_policy.py` 区分真实消息与内部主动触发 | EAP 主动来源与交付账本 | TurnIngressBuffer 保留原始消息 ID/顺序/授权；EAP 来源只能作为结构化信号 |
| `request_view.py` 仅在发送视图加入 transient payload | CTX ContextPackage | 第三方贡献先过 KIG，再由 CTX 裁剪；不得直接修改持久消息链 |
| `multimodal.py` 从运行期消息取图片并拼装模型内容 | CIE/KIG/Provider capability | 先验证 MIME/hash/像素/字节/TTL、模型 vision 证书和远传授权，不能仅凭存在 base64 即发送 |
| `ContextContribution` 只有 source/owner/scope/priority/content/TTL | KIG 来源治理、CTX 预算 | 遐蝶协议必须额外包含 revision/hash、privacy、token estimate、幂等 ID 与失效语义 |

### 9.3 合并后 CIE.0 第一轮动作

1. 将 PR #4 merge SHA、`main` 全量测试结果和 Schema 80 写入 ConstructionBaseline。
2. 从 `main` 创建 `agent/cie-specialty`；首阶段只新增评测、指标和 feature flag，不新增迁移或改变聊天行为。
3. 建立 5/20/100/500 轮连续消息、活动生成打断、文本附件/图片授权、节奏重组和恶意 ContextContribution 的纯合成固定集。
4. 独立 Review 确认 CIE.0 为 0 个未解决 P0/P1 后，才允许 CIE.1 占用 Schema 81（若实现确实需要持久表）。

准备结论：CIE 设计与许可证边界已可开工，但执行门仍是 PR #4 合并；当前不创建 CIE 分支、不占用 Schema 81、不改聊天运行时。

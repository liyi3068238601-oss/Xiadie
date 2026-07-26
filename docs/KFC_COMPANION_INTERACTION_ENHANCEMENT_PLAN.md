# 遐蝶 KFC 能力归属与陪伴交互增强专项计划

- 计划代号：CIE（Companion Interaction Enhancement）
- 版本：v0.1
- 日期：2026-07-22
- 参考对象：`tt-P607/kokoro_flow_chatter@d857f4f`；本地只读参考包 `E:\Xiadie\kokoro_flow_chatter-2.1.1\kokoro_flow_chatter-2.1.1.mfp`（KFC 2.1.1）
- 状态：需求与所有权冻结；除 LIFE 归属项外，必须在 `CDS → LIFE → KIG` 完成后施工
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
| 心理活动流 | 60% | LIFE；EAP/CDS 只提供结构化来源 | LIFE 增加结构化 InnerStateEvent；禁止保存完整内心独白或 chain-of-thought |
| 近期记忆压缩 | 90% | CTX | 保持 `conversation-summary-v1`；不以第一人称虚构事实 |
| 私人备忘录 | 25% | LIFE | LIFE 增加有界 ShortMemo，TTL 1 小时～14 天、每会话/角色上限 10、可查看/删除 |
| 等待与超时 | 80% | EAP | 复用 Presence/open thread/due/expiry；只在发现真实缺口时提 `proactive-decision-v3` |
| 主动发起 | 95% | EAP | 不重建；继续由最终硬门、投递和反馈状态机裁决 |
| 消息积累窗口 | 0% | CIE | CIE 新建 TurnIngressBuffer，不改长期记忆或 EAP 候选 |
| 生成打断 | 0% | CIE；CDS 提供取消契约 | CIE 实现前后端协同取消、合并和旧回复保留 |
| 原生图片多模态 | 10% | CIE；KIG 管跨源治理 | 独立传输授权、能力探测、大小/数量/生命周期门禁 |
| 回复节奏 | 35% | CIE；表达协议仍归 EAP | 首版只做客户端表现；语义拆分需新 `expression-plan-v2` 和 ADR |
| 第三方上下文注入 | 45% | CIE 接口；CTX/KIG 最终裁决 | 只接受结构化 ContextContribution，不接受自由 Prompt 拼接 |

当前等价覆盖约 44%。已接近可用的摘要、等待和主动发起不重复施工。

## 3. 施工顺序

```text
当前 CDS 完成
  ↓
LIFE 开工前纳入 InnerStateEvent + ShortMemo
  ↓
LIFE 完成
  ↓
KIG 完成跨源治理
  ↓
CIE.0～CIE.6 独立施工
```

CDS 阶段只提供取消、优先级、模型/来源验证接口；不得提前实现消息缓冲、图片传输或回复节奏。LIFE 只实现归属自己的结构化心理状态和短期备忘录，不实现聊天 UI/流控制。EAP v1 与 CTX v1 保持冻结。

## 4. 不可变安全边界

1. 不保存、展示或要求模型输出完整 chain-of-thought；只保存枚举状态、用户可理解摘要、证据引用和 reason code。
2. ShortMemo 不是长期记忆、Goal、ImportantDate 或任务；到期删除不得影响领域事实。
3. 新消息打断必须先确认旧请求进入可取消段；已经进入原子写入或投递段的任务只能完成或回滚。
4. 消息合并必须保持每条原始消息 ID、顺序、时间和附件授权，不能只保留拼接正文。
5. 图片默认本地暂存；发往远端 Provider 前逐次显示位置、模型、数量与用途并取得授权。
6. 第三方贡献只能提交有界类型、来源 revision/hash、TTL、敏感等级和候选内容；CTX/KIG 有权拒绝、裁剪或降级。
7. 回复节奏不得篡改模型语义，不得让已经确认投递的文本重复发送。
8. 任何 CIE 功能失败都回到当前单消息、单生成、纯文本流式聊天路径。

## 5. LIFE 施工前补充项

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

- [ ] 锁定 CDS/LIFE/KIG 最终合并 SHA、Schema、协议与测试基线。
- [ ] 建立连续消息、打断、附件、回复节奏、第三方贡献的合成评测集。
- [ ] 记录当前发送成功率、首 token 延迟、取消率、重复回复率和正文泄漏率。
- [ ] 冻结旧聊天路径作为 fallback，设立单一 feature flag。

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

KFC 2.1.1 本地包位于 `E:\Xiadie\kokoro_flow_chatter-2.1.1\kokoro_flow_chatter-2.1.1.mfp`，保留在项目目录外，仅作为只读参考，不解包或提交到遐蝶仓库。CIE 设计和施工可以重点审查其：

- turn/phase 状态机、未读消息策略、打断控制器与请求视图；
- memo、等待、主动触发、上下文来源和多模态的控制流；
- 失败路径、并发边界、兼容适配与测试场景。

参考过程必须形成“需求或边界 → KFC 行为观察 → 遐蝶现有所有者 → 独立实现”的简短溯源记录，优先复用遐蝶已经冻结的 CTX/EAP/CDS/LIFE/KIG 协议，不能为了贴近 KFC 建立平行内核。

KFC 为 AGPL-3.0。默认允许阅读源码、比较行为、学习状态机和推导自有测试场景；不得逐字复制 Prompt、资源、测试或实现代码。若未来确有必要复用源码片段，必须在写入前新增许可证 ADR，明确分发方式、网络使用义务和整个项目的许可证影响，经确认兼容后才可施工。

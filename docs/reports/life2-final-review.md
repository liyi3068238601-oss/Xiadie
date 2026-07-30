# LIFE v2 总体验收与工程 Review

日期：2026-07-30  
范围：LIFE2.0～LIFE2.6  
当前 Schema：82  
实现分支：`agent/life-v2-specialty`

## 结论

LIFE v2 的计划内施工已经完成。Persona v2 首次 Active 后由真实聊天发现自然对话动作旁白回归，发布门已立即回退；修订后的 `persona-profile-v2.1` 增加确定性输出门并完成新的三轮 140/140 生产等价认证。WorldBook r1、ShortMemo 与 InnerStateProjection 均保持 Shadow，旧 Lore 继续承担生产路径。

## Persona v2.1 真实回归修复（2026-07-30）

- 真实失败：用户输入拟声词后，模型输出了括号内动作、表情、心理和声音旁白；历史固定集的动作词覆盖过窄，120/120 不能代表这一场景。
- 发布处置：确认后立即将 `life.persona_v2.rollout_mode` 回退 Off；旧证书不能匹配 v2.1 的 profile/hash/输出门协议。
- 提示与预算：自然聊天规则增加“撒娇、拟声、调侃、亲近和安慰请求不是角色扮演许可”，并参考 Neo-MoFox 的每轮强化思路，将去重后的人格负面硬门放在 system prompt 末尾；Persona 专用硬门由 1200 放宽至 1350，最大 Projection 保守不超过 1327/1307，仍低于旧 Persona 约 1490 基线。
- 确定性最终门：`persona-natural-dialogue-guard-v1` 跨流式 chunk 暂存定界片段，只删除含动作、表情、心理、声音或场景标记的括号/星号/方括号旁白；普通解释、数学、引用、代码括号保留，用户明确要求角色扮演、小说或剧本时放行。
- 新模型证据：固定集扩为 140 条。最终 Prompt 原始三轮为 134/140、138/140、137/140；输出门分别介入 6、2、3 条后，生产等价结果三轮均为 140/140，420 次调用错误为 0。

## 用户独立 Review 处置（2026-07-30）

- 接受证书批准：`certifications.json` 的状态由 `candidate_passed_pending_review` 更新为 `certified`。证书仍严格绑定原 provider/model/location 指纹、静态 compiled hashes、fixture 与评测 artifact；其他模型不能继承。
- 接受 P2-1：`test_relationship_boundary_controls_curiosity_and_help_flags` 现在分别覆盖 `defensive` 与 `highly_guarded`，两档都禁止 `gently_curious` / `offer_help`。
- 暂不实施 P2-2：过期 ShortMemo 已在创建、活动列表和召回三个实际入口同步清理。为单纯物理回收引入常驻定时任务会扩大生命周期与并发面；该项保留为非阻塞后续优化，不影响过期项零召回。
- 发布决定：Review 本身只批准证书；用户随后单独批准 Persona 发布门切至 Active。首次真实回归后改由 v2.1 新证书重新过门；WorldBook 没有 A 级来源，ShortMemo 与 InnerStateProjection 也未获 Active 授权，因此三者保持原值。

## Review 发现与处理

1. **已修复：Projection 发布门默认值不一致。** Schema 82 原先写入 `off`，而施工合同与报告要求初始 Shadow。已改为首次迁移写入 `shadow`，并增加迁移默认值测试。
2. **已修复：远端 ShortMemo 复核边界不完整。** 远端接受后的记录曾仍标记为 `deterministic`，且发布门 Off 时可能先进入远端复核。现已写为 `model_validated`；Off/产品关闭在任何远端调用前返回，回归测试验证远端函数零调用。
3. **已修复：最大 Projection 超过 Persona 预算。** 初版将不具语义价值的来源 ID 全部渲染给模型，最大编译结果为 1219/1228 tokens。现在来源 ID 只留在请求内用于一致性与来源治理，模型只接收有界表达旗标；最终为陪伴 1186、专注工作 1194 tokens，均低于 1200 上限。
4. **已修复：旧 Provider/mock temperature 兼容。** 只有实际选中 Persona v2 时才向聊天与 OpenAI-Compatible 边界传 `temperature=0`，旧 mock、知识授权捕获器和未选中 Persona 路径不再收到新增关键字参数。
5. **已校正测试假设：CIE.5 极短超时分类。** 同步第三方 contributor 继续在线程中隔离；20 ms 下立即异常可能被诚实分类为 `error` 或 `timeout`。测试现同时接受两种安全降级，但仍强制候选数为 0 且基础聊天成功。该模块连续 5 次全部通过。

## 实际验证

- Persona DeepSeek v2.1 门：`deepseek-v4-flash`、temperature=0，生产等价输出三次均 140/140；原始输出为 134/140、138/140、137/140，确定性输出门介入 6/2/3 次且没有改写直接话语。证书状态为 `certified`。
- Persona 最大请求编译预算：保守不超过 1327/1307 tokens（陪伴/专注工作，含最大 Projection），低于 v2.1 的 1350 硬门和旧 Persona 约 1490 基线。
- WorldBook r1：30 条资源合同通过；来源仍为 A=0、B=27、local=3，因此继续 Shadow。
- ShortMemo：200 条合成分类矩阵及秘密、敏感最小化、TTL、容量、来源、远端只否决、治理 API/UI 和独立 CTX 区块通过；发布门继续 Shadow。
- InnerStateProjection：同快照一致、撤销零残留、空来源不生成、无表/缓存/正文日志、关系边界旗标与 Persona 静态证书隔离通过；发布门继续 Shadow。
- LIFE2 组合矩阵：5/20/100/500，共 625 案例；10 项失败计数全部为 0。
- 后端最终全量：`2639 passed, 1 warning in 603.38s`。警告为 TestClient 对 `httpx2` 的既有迁移提醒。
- 前端：73 passed；TypeScript + Vite production build 通过。构建仍提示 `pet.html` 的既有非 module Live2D runtime script 无法 bundle，但产物正常生成。
- Windows 实机烟测：Microsoft Windows NT 10.0.26200.0；隔离数据目录启动后端、Vite 与 Electron，后端健康、前端加载成功、Electron 存活 8 秒，退出后测试进程/端口/临时目录清理完成。
- 休眠恢复：未自动让用户系统进入真实睡眠；Electron `powerMonitor` resume 接线、后端 resume guard 和 delivery bridge 恢复合同由自动化覆盖。

## 回退与发布决定

- Persona v2.1：当前数据库使用 `life.persona_v2.rollout_mode=active` 时，必须同时命中 `deepseek/deepseek-v4-flash` 指纹、v2.1 静态编译 hash 与 `persona-natural-dialogue-guard-v1` 协议才会 `selected_v2=true`。切回 `off` 后下一请求恢复冻结旧 Persona；更换未认证模型或缺失输出门协议都会 fail closed。
- WorldBook r1：关闭门后继续旧 `xiadie_lore.md`；当前仍未晋级。
- ShortMemo：发布门 Off 立即停止分类、远端复核、写入与召回，不删除用户已有数据；Schema 82 表保留。
- InnerStateProjection：发布门 Off 停止生成，无数据库回滚。
- Schema 82 是向前兼容的增量迁移；旧应用忽略新增表，不执行破坏性降级。

## 建议用户独立 Review 重点

1. Persona v2.1 的 140 条失败分布、原始输出门介入率与三次生产等价 140/140 是否足以恢复 Active；重点继续体验拟声、调侃、亲近和安慰场景，同时确认显式角色扮演仍可使用动作格式。
2. ShortMemo 是否只记录用户明确的近期安排，敏感最小化是否仍显得自然，设置页的静默创建说明与远端复核授权是否清楚。
3. `gently_curious` / `offer_help` 在 `default_distance`、`softly_guarded`、`relaxed` 三档是否符合关系节奏；防御两档必须无这两项。
4. 所有来源门是否保持诚实：A=0 的 WorldBook 不得晋级，未认证模型不得继承 DeepSeek 证书，Shadow 不得写正式数据或影响生产 prompt。

后续切换任何 Active 门都应作为单独发布决定处理；本次 Review 只批准 Persona v2 证书。

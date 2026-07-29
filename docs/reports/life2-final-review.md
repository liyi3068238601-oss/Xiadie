# LIFE v2 总体验收与工程 Review

日期：2026-07-30  
范围：LIFE2.0～LIFE2.6  
当前 Schema：82  
实现分支：`agent/life-v2-specialty`

## 结论

LIFE v2 的计划内施工已经完成，当前总体工程 Review 为 **0 个未解决 P0/P1**。这不等于发布晋级批准：Persona v2 仍登记为 `candidate_passed_pending_review`，WorldBook r1、ShortMemo 与 InnerStateProjection 均保持 Shadow，旧 Persona 与旧 Lore 继续承担生产路径。

## Review 发现与处理

1. **已修复：Projection 发布门默认值不一致。** Schema 82 原先写入 `off`，而施工合同与报告要求初始 Shadow。已改为首次迁移写入 `shadow`，并增加迁移默认值测试。
2. **已修复：远端 ShortMemo 复核边界不完整。** 远端接受后的记录曾仍标记为 `deterministic`，且发布门 Off 时可能先进入远端复核。现已写为 `model_validated`；Off/产品关闭在任何远端调用前返回，回归测试验证远端函数零调用。
3. **已修复：最大 Projection 超过 Persona 预算。** 初版将不具语义价值的来源 ID 全部渲染给模型，最大编译结果为 1219/1228 tokens。现在来源 ID 只留在请求内用于一致性与来源治理，模型只接收有界表达旗标；最终为陪伴 1186、专注工作 1194 tokens，均低于 1200 上限。
4. **已修复：旧 Provider/mock temperature 兼容。** 只有实际选中 Persona v2 时才向聊天与 OpenAI-Compatible 边界传 `temperature=0`，旧 mock、知识授权捕获器和未选中 Persona 路径不再收到新增关键字参数。
5. **已校正测试假设：CIE.5 极短超时分类。** 同步第三方 contributor 继续在线程中隔离；20 ms 下立即异常可能被诚实分类为 `error` 或 `timeout`。测试现同时接受两种安全降级，但仍强制候选数为 0 且基础聊天成功。该模块连续 5 次全部通过。

## 实际验证

- Persona DeepSeek 门：`deepseek-v4-flash`、temperature=0，候选三次均 120/120；旧版按同一 v1.2 oracle 为 97/120、95/120、99/120。证书状态仍为待 Review，不是正式 certified。
- Persona 最大请求编译预算：1186/1194 tokens（陪伴/专注工作）。
- WorldBook r1：30 条资源合同通过；来源仍为 A=0、B=27、local=3，因此继续 Shadow。
- ShortMemo：200 条合成分类矩阵及秘密、敏感最小化、TTL、容量、来源、远端只否决、治理 API/UI 和独立 CTX 区块通过；发布门继续 Shadow。
- InnerStateProjection：同快照一致、撤销零残留、空来源不生成、无表/缓存/正文日志、关系边界旗标与 Persona 静态证书隔离通过；发布门继续 Shadow。
- LIFE2 组合矩阵：5/20/100/500，共 625 案例；10 项失败计数全部为 0。
- 后端最终全量：`2631 passed, 1 warning in 461.94s`。警告为 TestClient 对 `httpx2` 的既有迁移提醒。
- 前端：73 passed；TypeScript + Vite production build 通过。构建仍提示 `pet.html` 的既有非 module Live2D runtime script 无法 bundle，但产物正常生成。
- Windows 实机烟测：Microsoft Windows NT 10.0.26200.0；隔离数据目录启动后端、Vite 与 Electron，后端健康、前端加载成功、Electron 存活 8 秒，退出后测试进程/端口/临时目录清理完成。
- 休眠恢复：未自动让用户系统进入真实睡眠；Electron `powerMonitor` resume 接线、后端 resume guard 和 delivery bridge 恢复合同由自动化覆盖。

## 回退与发布决定

- Persona v2：`life.persona_v2.rollout_mode=off`，下一请求使用冻结旧 Persona；当前本来就是 Off/未认证生产路径。
- WorldBook r1：关闭门后继续旧 `xiadie_lore.md`；当前仍未晋级。
- ShortMemo：发布门 Off 立即停止分类、远端复核、写入与召回，不删除用户已有数据；Schema 82 表保留。
- InnerStateProjection：发布门 Off 停止生成，无数据库回滚。
- Schema 82 是向前兼容的增量迁移；旧应用忽略新增表，不执行破坏性降级。

## 建议用户独立 Review 重点

1. Persona v2 的 120 条失败分布与三次 120/120 是否足以批准正式证书；重点看角色辨识度、工作模式克制和自然对话无动作旁白。
2. ShortMemo 是否只记录用户明确的近期安排，敏感最小化是否仍显得自然，设置页的静默创建说明与远端复核授权是否清楚。
3. `gently_curious` / `offer_help` 在 `default_distance`、`softly_guarded`、`relaxed` 三档是否符合关系节奏；防御两档必须无这两项。
4. 所有来源门是否保持诚实：A=0 的 WorldBook 不得晋级，未认证模型不得继承 DeepSeek 证书，Shadow 不得写正式数据或影响生产 prompt。

独立 Review 完成前，不建议切换任何 Active 门。

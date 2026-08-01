# LIFE2.9 Persona v2.3 候选与版本路由施工报告

- 日期：2026-08-01
- 分支：`agent/life-v2-specialty`
- 施工基线：`fad640e`
- 结果提交：本报告所在提交
- Schema：82（未新增迁移）
- 状态：候选施工完成并通过用户 Review；后续认证与发布见 LIFE2.10 报告

## 1. 结论

Persona v2.2 与 v2.3 已成为可独立寻址的运行资源。已认证 v2.2 保持逐字可运行；v2.3 落实已确认的现代陪伴助手设计，但认证列表为空，当前生产 selector 仍是 `persona-profile-v2.2`。即使内部误选 v2.3，Active 路径也必须回退到已认证 v2.2；两者均失败才使用 legacy Prompt。

本阶段没有调用 DeepSeek、签发证书或切换生产 Persona。

## 2. LIFE2.8 Review 处理

- Projection epoch P2 暂不采纳：Projection 无持久数据与跨 epoch 诊断桶，没有实际隔离需求；以后由运维审计需求另立主题。
- knowledge recall 测试线程 P2 暂不混入：属于既有测试基础设施，不是 Persona 路由或 Projection 生产缺陷。
- 英文 `expression_flags` 暂不修改：转换会同时改变当前 v2.2 Active Projection 的生产 prompt，违反本阶段“保留编译算法和 v2.2 行为”。在 LIFE2.10 模型评测中观察模型消费效果，再决定是否作为独立协议修改。
- 三项 Active 的真实体验继续归 LIFE2.10 组合矩阵与 LIFE2.11 人工观察。

## 3. 资源与路由

```text
persona_profiles/v2_2/  已认证生产资源，内容与证书不变
persona_profiles/v2_3/  候选资源，certifications=[]
```

内部 selector：`life.persona_v2.profile_version`。允许值只有：

- `persona-profile-v2.2`
- `persona-profile-v2.3`

当前真实数据库从“设置缺失但代码默认 v2.2”收口为显式 `persona-profile-v2.2`。ShortMemo 与 Projection 仍独立保持 Active。

Active 回退链：

```text
selector 指定 profile（资源 + hash + token + 模型证书）
  → 已认证 persona-profile-v2.2
  → legacy PERSONA_PROMPT
```

## 4. v2.2 不变证据

两种模式的静态 compiled hash 与原证书完全一致：

```text
companionship aff81f21baf25004d052748997d087c54e0396e56f7559f0303dc21c2b28561f
focused_work  0e6fd222be57ffa6fd3544a853638420466028946d1b7bac76f04d8cf3d1416e
```

历史 v2.2 certification、真实模型报告和 `persona-evaluation-v1.4` 未修改。

## 5. v2.3 候选内容

- `core.md`：遐蝶本人、过去式入殓师经历、核心人格、关系边界、现代通用能力和事实/资料优先级；不再常驻《如我所书》或异世界终端场景。
- `companionship.md`：认真回应、日常基调、独立看法、情绪流动、适度追问和主动帮助；兴趣延续必须有连续性证据。
- `focused_work.md`：完整任务能力、结论优先、授权内施工与证据区分；保持同一遐蝶而非客服人格。
- `output_contract.md`：自然对话无动作/心理旁白、无虚构环境/经历/工具、无世界观回避、无主动 AI 自述和权限扩大。
- `styles.json`：沿用冻结白名单，不新增用户可控字段。

静态 compiled hash 与预算：

```text
companionship 6a3d71745a600e89ff6779a351ddb33b9f59d78b77b585e00a6b08cc2c512aa1  1404 tokens
focused_work  4b0b91fbd73ab8692a0f0fd8e095868cf5630d92a5c920cf7c256921aefbdc27  1357 tokens
```

测试用包含 `calm,warm,gently_curious,offer_help` 的 Projection 后，两种模式仍不超过 1450-token 硬门。

## 6. 验证结果

旧 Persona/Projection 与 LIFE2.9 新合同：

```text
19 passed in 3.26s
```

扩大到 Persona 固定集、v2.2 历史验收、LIFE2.6、Projection Active 和版本路由：

```text
31 passed, 1 StarletteDeprecationWarning in 18.10s
```

覆盖：v2.2 hash/证书不变、两个 profile 确定性、静态与 Projection 预算、selector 白名单/幂等/未知值、v2.3 未认证回退 v2.2、v2.3 损坏回退 v2.2、v2.2 也损坏回退 legacy、Shadow 候选不进生产、Observer 单版本派生以及现有输出门合同。

`git diff --check` 通过。

## 7. 未运行与风险

- 未运行 DeepSeek：v2.3 真实模型固定集、证书和发布属于 LIFE2.10。
- 未运行后端全量、前端全量和生产构建：公共聊天调用签名向后兼容，生产仍走 v2.2；按计划在 LIFE2.10 首次运行。
- v2.3 内容尚需用户逐段 Review。当前文本是候选，任何实质修改都会改变 section/compiled hash，必须在模型认证前完成。
- LIFE2.9 Review 修订后，v2.3 陪伴静态预算为 1404，余量有限；后续新增规则应先去重而非直接追加。

## 8. Review 重点

1. `v2_3/core.md`：是否仍是遐蝶本人；现代能力是否自然；有没有把 AI 助手写成角色自述；原作背景降级是否过度。
2. `v2_3/companionship.md`：认真回应、好奇、主动帮助与关系边界是否平衡；是否有模板化风险。
3. `v2_3/focused_work.md`：能否完整做事且不是冷淡客服；是否仍有多余角色化限制。
4. `v2_3/output_contract.md`：负面行为是否覆盖充分但不过度重复；自然对话旁白例外是否准确。
5. 路由：v2.3 未认证时必须停在 v2.2；v2.2 资源和历史证书不能因目录迁移改变。

本阶段不需要重跑后端全量。Review 通过并完成内容修改后，才进入 LIFE2.10 DeepSeek 认证与发布。

## 9. Review 后修订附记

用户 Review 已通过本阶段并授权 LIFE2.10。按 Review 采纳：请求边界捕获一次 profile selector；Core 恢复“一次浓烈对话不能越级”，移除无来源摄影爱好；Work 恢复不得用死亡权能、入殓经历或角色比喻替代现实建议。上述正文修改使 manifest、compiled hash 与 token 预算发生变化，因此本报告第 5 节更新为最终候选值；原施工时数值由 Git 历史保留。

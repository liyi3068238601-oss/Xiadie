# LIFE2.2 Persona 编译与 WorldBook r1 施工记录

- 日期：2026-07-30
- 前置提交：`6e2df35d6a6aef16ae7745fa350b9648ca90e9c2`
- Schema：81（本阶段无迁移）
- 发布状态：Persona v2 `off`；WorldBook r1 `off`；均未替换生产路径

## 实现结果

Persona v2 使用仓库内 manifest 和逐资源 SHA-256 编译固定 Core、`companionship` / `focused_work` overlay 与四项白名单风格。未知模式或风格在请求边界返回错误；资源缺失、损坏、未认证模型或未满足发布门时逐字回退旧 `PERSONA_PROMPT`。证书同时绑定 provider/model 指纹、profile/compiler 版本、模式和 compiled hash，不能由 DeepSeek 自动继承给其他模型。观察器摘要由同一份已校验 Core 确定性派生，不再独立手写第二份人格。

前端提供持续可见的模式、篇幅、诗意、主动性和称呼选择，按会话保存在 `sessionStorage`；每次请求携带一个不可变的模式/风格值快照。没有提供任意 system prompt 编辑入口。

WorldBook r1 构建脚本从已 Review 内容稿生成 30 条只读 JSON 条目，逐条固定正文 hash/revision。运行 loader 与旧 `lore.py` 缓存完全独立，以资源字节 hash、来源门版本和 rollout 快照建立 cache namespace；显式名称/别名命中后最多补一层、每个根最多两条关联，稳定排序并继续受 3 节/3600 字符限制。诊断只暴露 entry ID、revision、manifest hash、裁剪与 fallback，不暴露正文。

来源盘点仍是 `verified_a=0`、`candidate_b=27`、`local_candidate=3`。因此 Shadow 可计算候选，但实际 ContextPackage 仍使用旧 Lore；即使误设为 `active`，没有 A 级命中也会 fail closed 回到旧 Lore。

## 实测

- 后端专项及相关回归：`89 passed, 1 warning`。
- 较早一轮 Persona/WorldBook/CTX/API 专项：`63 passed, 1 warning`。
- 前端：`73 passed`（新增 LIFE2 请求契约 2 项后）。
- 前端生产构建：TypeScript 与 Vite 通过，189 modules；保留既有 `pet.html` 非模块脚本警告。
- `git diff --check`：通过；仅有 Windows 下 LF/CRLF 提示。

## 已知边界与回滚

- Persona 证书当前为空；LIFE2.3 未完成前不能进入生产。
- WorldBook r1 没有 A 级条目，继续 Shadow；A 级来源晋级需要独立来源审计，不由内容 Review 自动完成。
- 将 Persona/WorldBook 发布门设为 `off` 即在下一请求回到旧 Prompt/Lore；活动请求使用已绑定快照，不中途替换。
- 本阶段未新增数据库状态，回滚不需要迁移降级。

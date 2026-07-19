# 对话上下文 CTX.0 基线报告

- 日期：2026-07-19
- 开工提交：`b2dd172`
- 数据库当前版本：schema 41
- 下一迁移编号：42（CTX.0 只预留，不执行）
- 基线协议：`context-baseline-v1`
- 运行行为变化：无

## 阶段目标

CTX.0 只冻结原始消息、会话摘要和长期记忆的职责边界，并把当前临时预算器的问题变成可重复验证的基线。
本阶段不修复裁剪算法、不新增摘要表、不改变聊天请求内容，也不修改 Fragment、Episode、Saga、Observer、
Consolidator、Archivist 或记忆界面。

边界决策见 [ADR-0045](../adr/0045-conversation-context-and-memory-boundary.md)。

## 当前真实链路

当前 `/api/chat` 的上下文行为为：

1. 根据 Provider ID 从 `CONTEXT_WINDOWS` 选择窗口，不读取具体模型能力。
2. 使用 `app.knowledge_context.estimate_tokens` 作为实际运行估算器；`context_budget.py` 顶部的同名函数会在模块
   尾部被覆盖。
3. 估算 system prompt 与当前会话全部历史。
4. 使用 `max(512, context_window - system_tokens)` 计算历史可用量。
5. 历史超出该值时调用 `trim_history(..., keep_min_rounds=4)`。
6. 请求没有统一的输出 token 预留，Provider 调用也没有从该预算结果获取输出上限。

## 无正文合成基线

以下结果由 `backend/scripts/run_context_baseline.py` 生成。脚本只输出 Provider/model 标识、消息数量、估算 token
与布尔状态，不输出任何合成消息或用户正文。

| 场景 | 窗口 | system | 历史裁剪前 | 历史保留 | 估算输入 | 超窗 | 输出预留 |
|---|---:|---:|---:|---:|---:|---|---:|
| short-mock / 4 轮 | 8,192 | 960 | 576 | 576 | 1,536 | 否 | 0 |
| medium-custom / 20 轮 | 4,096 | 1,920 | 5,248 | 3,200 | 5,120 | 是 | 0 |
| long-openai / 100 轮 | 128,000 | 3,840 | 51,456 | 51,456 | 55,296 | 否 | 0 |
| oversized-system / 8 轮 | 4,096 | 7,680 | 2,176 | 1,664 | 9,344 | 是 | 0 |

`medium-custom` 还证明模型名当前不参与能力判断：即使配置名写成 `user-model-128k`，`custom` 仍固定使用
4,096；相反，`openai/configured-32k-model` 仍固定得到 128,000。这里记录的是现状，不代表模型真实能力。

## 已固定的失败基线

`backend/tests/test_context_budget_baseline.py` 包含两个严格 `xfail`：

1. `keep_min_rounds` 可使裁剪后历史超过传入预算。
2. 最近单轮自身超过预算时，函数仍返回该轮，而不是在 Provider 调用前失败关闭。

严格 `xfail` 的意义是：当前缺陷存在时全量套件保持可运行；CTX.1 修复后测试会变成 XPASS 并使套件失败，迫使
施工者将其改成普通通过测试，而不是遗忘旧缺陷。

## 组件计量边界

基线脚本已经分别输出以下组件的数值估算，但不输出正文：

- system/persona 与安全规则；
- 当前用户消息；
- 最近原始消息；
- 滚动摘要；
- 跨会话历史；
- 现有长期记忆 digest；
- Lore；
- 用户知识引用；
- 消息封装与安全余量；
- 输出预留。

当前实现还没有消息封装开销的权威估算，因此脚本明确输出 `message_envelope: null`，而不是伪造为 0；输出预留
按真实现状记录为 0。上述组件将在 CTX.1 的 `BudgetPlan` 中成为正式字段和硬预算输入。

## 可重复命令

```powershell
cd E:\Xiadie\Xiadie\backend
.\.venv\Scripts\python.exe scripts\run_context_baseline.py
.\.venv\Scripts\python.exe -m pytest tests\test_context_budget_baseline.py -q -rxX
```

预期专项结果：`2 passed, 2 xfailed`。任何输出都不得包含消息正文。

## 当前结论

- 当前不能再描述为“完全没有上下文预算”；它已有临时窗口选择、估算和裁剪。
- 当前实现不能证明请求不超过模型窗口，且已由合成基线稳定复现两个超窗场景。
- Episode/Saga 不是滚动摘要，CTX 后续阶段只能通过现有只读 digest 消费长期记忆。
- 修复入口是 CTX.1；CTX.0 不改变运行行为。

## 阶段 review（2026-07-19）

review 重新从 `b2dd172` 基线审阅本阶段全部差异，并按职责边界、隐私、可复现性、迁移编号和回归风险检查。

### Review findings

| 等级 | 发现 | 处理 |
|---|---|---|
| P1 | 初版基线脚本只给出合并后的 system/history 数量，没有逐项记录 persona、安全规则、长期记忆、情绪、Lore、知识和当前消息 | 已在 review 中修正：`component_tokens` 分项输出；未知消息封装开销记录为 `null`，输出预留按现状记录为 0 |
| P0/P1 | Episode/Saga 是否被误用为滚动摘要 | 未发现；ADR 明确冻结长期记忆写入、schema、生命周期和 UI，只允许读取既有 digest |
| P0/P1 | 是否改变聊天运行行为 | 未发现；新增内容仅为测试、离线基线脚本和文档，`app/context_budget.py` 与 `/api/chat` 未修改 |
| P0/P1 | 是否泄露对话正文 | 未发现；脚本输出只含合成场景标识、计数和状态，报告与测试不读取用户数据库 |
| P2 | 当前 estimator 仍是字符级近似，且缺少消息封装和输出预留 | 接受为 CTX.0 已知边界，必须由 CTX.1 的单一 estimator 与 `BudgetPlan` 解决 |

review 修正后未留下 P0/P1 问题，CTX.0 允许关闭。两个严格 `xfail` 是阶段产物，不是未处理的测试失败；CTX.1
修复对应缺陷时必须把它们转换为普通通过测试。

### 验证证据

- 后端全量：`406 passed, 2 xfailed, 1 warning`；两个 xfail 均为 CTX.0 明确记录的预算不变量失败。
- 前端：33 passed。
- TypeScript + Vite：188 modules，生产构建通过。
- Electron：`main.js`、`preload.js` 语法检查通过。
- 新增 Python 文件：Ruff 通过。
- `git diff --check`：通过。

### Review 结论

**通过。** CTX.0 已满足代码、测试、文档和 review 完成门；下一阶段可以进入 CTX.1，但不得提前实现 CTX.2
会话摘要或修改现有长期记忆系统。

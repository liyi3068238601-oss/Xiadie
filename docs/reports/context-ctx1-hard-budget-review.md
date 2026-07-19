# 对话上下文 CTX.1 硬预算阶段报告

- 日期：2026-07-19
- 开工提交：`5761b95`
- 数据库 schema：41（本阶段无迁移）
- 预算协议：`context-budget-v1`
- 估算器版本：`xiadie-conservative-v1`
- 下一阶段：CTX.2 会话摘要数据地基

## 阶段结论

CTX.1 已把原先仅按 Provider 名称选择窗口、可能超窗的临时裁剪逻辑，替换为 provider+model 级能力解析与
Provider 调用前硬预算。摘要尚未实现时，长会话现在只会保留连续的最近完整轮次，或在系统规则、当前用户消息、
输出预留和安全余量无法共同容纳时返回结构化 413；不会主动构造已知超过所选能力窗口的请求。

本阶段没有新增摘要表，没有修改 Fragment、Episode、Saga、Observer、Consolidator、Archivist 或记忆 UI，
也没有把 token、召回来源或工程诊断展示到陪伴聊天界面。

## 实现摘要

1. `ModelContextCapability` 以 provider+model 为键，区分 `verified`、适配器保守映射、用户 `configured` 与
   `conservative_fallback`；未知配置一律回退到 4,096 tokens，而不是猜测大窗口。
2. 有效窗口始终不超过 `APPLICATION_CONTEXT_CEILING_TOKENS = 1_000_000`。
3. `app.context_budget.estimate_tokens` 成为唯一估算权威，知识上下文复用该函数，不再反向覆盖定义。
4. 纯 `BudgetPlan` 同时保护系统提示、当前用户消息、输出预留与安全余量，并按完整 user/assistant 轮次保留最近历史。
5. OpenAI-Compatible 请求显式接收预算计算出的 `max_tokens`。
6. SSE `meta` 保留原字段并增加协议版本、估算总量、窗口来源和裁剪轮数；所有诊断只含计数与稳定标识，不含正文。
7. 重新生成继续排除旧 assistant 回复；新回复失败时保留旧回复，成功预算仍维持完整轮次。

## CTX.0 后置 Review 建议处理

| 等级 | 建议 | 本阶段决定 |
|---|---|---|
| P0 | 两个严格 xfail 在 CTX.1 转为普通通过测试 | 采纳并完成；现已验证安全裁剪和超大受保护区失败关闭 |
| P0 | 基线脚本必须证明 medium-custom 与 oversized-system 已修复或失败关闭 | 采纳并完成；前者安全裁剪，后者在请求构造前失败关闭 |
| P0 | 输出预留、provider+model 能力识别和保守失败策略 | 采纳并完成 |
| P1 | 去除 token estimator 覆盖与导入歧义 | 采纳并完成，估算权威固定为 `app.context_budget` |
| P1 | 修复知识评测 N20/N21 旧技术债 | 接受但不插入当前阶段；按用户决定放在整个 CTX 计划末尾、CTX.7 后处理 |
| 信息 | Review 提到 CTX.0 已新增运行时代码和 JSON 报告 | 未采纳该事实描述；提交 `5761b95` 的真实差异没有这些文件，不补造历史产物 |

## 无正文基线复核

`backend/scripts/run_context_baseline.py` 复跑结果：

| 场景 | 能力来源 | 结果 | 裁剪 | 是否构造超窗请求 |
|---|---|---|---:|---|
| short-mock | verified / 8,192 | planned | 0 轮 | 否 |
| medium-custom | conservative fallback / 4,096 | planned | 18 轮 | 否 |
| long-openai 未知模型 | conservative fallback / 4,096 | fail closed | 不适用 | 否 |
| oversized-system | conservative fallback / 4,096 | fail closed | 不适用 | 否 |

脚本输出 `contains_message_content: false`，不读取或打印用户数据库正文。

## Review findings

本阶段结束前重新检查了能力来源、预算算式、事务边界、重新生成、完整轮次、SSE 诊断和 Provider 输出上限。

| 等级 | 发现 | 处理 |
|---|---|---|
| P1 | 初版 `_history_turns` 在异常历史中可能把孤立 assistant 当成一轮保留 | 已修正：只有完整 user/assistant 配对才可进入历史预算，并增加回归测试 |
| P1 | 原专项覆盖尚未单独证明 128K、接近 1M、跨窗口不变量和 regenerate | 已补齐 128K 上限、1M 大输入、4K/8K/128K/1M 参数化不变量及重新生成测试 |
| P2 | 1M 相关性选择和延迟尚不能在纯硬预算阶段验证 | 明确留到 CTX.4 `ContextAssembler` 与 CTX.7 总验收，不伪装成已实现 |
| P2 | 当前环境未安装 Ruff | 不阻断：仓库没有 Ruff 配置或既有 lint 门；全量 Python 测试、前端测试/构建、Electron 语法检查和 `git diff --check` 均通过 |

Review 修正后未留下 P0/P1 问题。

## 验证证据

- CTX.1 专项：32 passed。
- 后端全量：437 passed；仅有 TestClient 弃用提示与 `.pytest_cache` 目录权限提示。
- 前端：33 passed。
- TypeScript + Vite：188 modules，生产构建通过；保留既有 Live2D 普通脚本提示。
- Electron：`main.js`、`preload.js` 语法检查通过。
- `git diff --check`：通过。

## 限制与下一阶段边界

- 字符估算器仍是保守近似，不声称等同每家模型的官方 tokenizer；安全余量与未知模型 4K 回退用于避免乐观猜测。
- 适配器默认能力不是运行时验证结果；用户可通过受控配置提供具体模型能力，未来 Provider 接入可补在线能力校验。
- CTX.1 只安全裁剪最近完整轮次，不生成摘要、不做跨会话召回，也不按语义相关性挑选历史。
- CTX.2 只建立可重建的会话摘要派生数据和生命周期，仍不得修改长期记忆系统或提前注入普通聊天。

## Review 结论

**通过。** CTX.1 满足硬预算、失败关闭、无正文诊断、专项测试和全量回归完成门，可以进入 CTX.2。

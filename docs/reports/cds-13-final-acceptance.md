# CDS.13 设置、诊断与冻结候选验收报告

> 日期：2026-07-26
>
> 当前 Schema：63
>
> 结论：施工完成，协议为冻结候选；全部 DecisionKind 保持 Shadow，待独立 review 后正式冻结并解锁 LIFE

## Review 建议处置

| 建议 | 处理 | 结果 |
|---|---|---|
| EAP 诊断增加错误码和延迟 | 采纳并版本化 | v1 不变，新增 `eap-decision-run-diagnostic-v2` |
| 普通层自然表达、高级层技术控制 | 采纳 | 普通层只显示自然能力；模式、角色、隐私和诊断置于高级区 |
| 诊断不得带正文 | 采纳 | 仅版本、计数、延迟、fallback、错误码和布尔隐私事实 |
| Electron 使用现有安全本地 API | 采纳 | renderer 继续通过已有 token header 调用 FastAPI；未新增 IPC 或扩大 preload 能力 |
| 协议冻结不等于模式晋级 | 采纳 | 九个 DecisionKind 仍全部最高 Shadow |

## 功能与安全验收

| 项目 | 结果 |
|---|---|
| 普通设置 | 自然能力、总开关；无协议术语 |
| 高级模式 | 只能在注册表冻结上限以内选择 |
| 模型角色 | 只接受启用 Provider 的登记模型 |
| 隐私 | 不持久化/展示正文、Prompt、raw output、候选 ID |
| 诊断 | 版本、计数、中位/最大延迟、fallback、错误码 |
| 回退 | 一键关闭全部模型决策；Provider 零调用；确定性 fallback |
| EAP 兼容 | v1 字段不变；v2 独立增加错误码/延迟 |

## Promotion Policy 证据

| 层 | 现有证据 | 结论 |
|---|---|---|
| 纯合成协议/安全集 | CDS.1～13 的 validator、来源变化、候选子集、幂等、回退与无正文测试 | 支持协议安全候选 |
| 配对比较 | DeepSeek v4-pro 6/6，v4-flash 3/6；模型间一致率 50% | 不支持晋级 |
| 盲评/真实质量 | CDS.10 仅 8 条未独立评审叙事样本，accuracy 50% | 样本不足且质量不足 |
| Provider 认证 | 只有 DeepSeek 一个真实 Provider | 跨 Provider 门不满足 |
| 成本/延迟 | Flash 159/250 tokens、1157 ms；Pro 318/679 tokens、1881 ms（输入/输出、中位） | 已记录，不构成质量认证 |
| 回滚 | 按 DecisionKind 校准回滚 + 全局一键回退 | 通过 |

因此不授予任何 Advisory/Active 资格。真正晋级仍须按具体 DecisionKind 补充独立、分层、盲评和至少两个真实 Provider 的证据。

## 自动验收

- CDS.13 + CDS.12 + 共享运行时定向测试：`27 passed, 1 warning`。
- CDS.9/CDS.10 当前 Schema 兼容修复复核：`2 passed`；历史阶段基线仍为 62，项目当前为 63。
- 前端：`47 passed`；TypeScript 与 Vite production build 成功，189 modules。
- Electron：`node --check main.js` 与 `node --check preload.js` 通过。
- Windows 工具链：Python 3.12.13、SQLite 3.50.4；冻结后端在隔离端口 18756 的 health 与本地 BGE-M3 smoke 通过（embedding available/local-only 均为 true）；沿用现有 token、CORS 与 launcher 边界，未新增端口或 IPC。
- 后端全量：`2304 passed, 1 warning`，耗时 508.91 秒。

## 尚待外部完成

1. CDS 总体独立 review，要求 0 个未解决 P0/P1。
2. review 通过后把 `cognitive-decision-v1`、`decision-kind-registry-v1` 和本兼容矩阵从“冻结候选”改为“正式冻结”。
3. 只有完成上述两项，才允许 LIFE 使用 Schema 64 开工；KIG 仍须等待 LIFE 冻结。

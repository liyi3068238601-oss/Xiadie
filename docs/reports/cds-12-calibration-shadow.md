# CDS.12 反馈校准与真实模型对比报告

> 日期：2026-07-26  
> Schema：63  
> 模式结论：全部保持 Shadow

## 1. Review 建议处置

| 建议 | 处理 | 结果 |
|---|---|---|
| `selected_ids` 逐项类型检查 | 采纳 | 非字符串或空 ID 在子集判断前拒绝 |
| 先执行 structured probe | 采纳 | 两个 DeepSeek 模型均至少一次通过纯合成探测 |
| 明确可调/不可调参数 | 采纳 | 2 个有限参数白名单，8 个硬边界不可调 |
| 反馈绑定 DecisionKind | 采纳 | 四域映射，跨域反馈拒绝 |
| 至少两个 Provider 才晋级 | 采纳 | 当前仅一个真实 Provider，明确保持 Shadow |

## 2. Shadow 校准与真实行为

反馈会形成按 DecisionKind 隔离的 profile 建议，但当前不会改变生产候选、ContextPackage、关系、记忆或主动投递：

| 项目 | Shadow profile | 当前真实行为 |
|---|---|---|
| helpful / quick_reply | 小幅提高 selection 偏好 | 旧算法与领域 validator 不变 |
| later_reply / unanswered | 提高 caution | EAP 程序化 unanswered pressure 仍是事实源 |
| rejected / corrected | 降低 selection、提高 caution | 不改 ownership、隐私、模式或 application gate |
| rollback | 单个 DecisionKind 恢复 0/0 | 不删除反馈历史，不影响其他决策器 |

因此，本阶段实际生产行为变化率为 0，越权参数修改率为 0，重复反馈重复应用率为 0。该结果证明的是隔离和回滚能力，不证明个体化质量已优于基线。

## 3. DeepSeek 真实结构化测试

证据文件：`docs/reports/cds-12-provider-consistency.json`。输入全部为纯合成 candidate ID，不含用户数据；不保存 Prompt、API Key 或 raw output。

| 模型 | structured probe | 固定样本合规 | 中位延迟 | Token（输入/输出） |
|---|---:|---:|---:|---:|
| `deepseek-v4-flash` | 通过 | 3/6（50%） | 1157 ms | 159 / 250 |
| `deepseek-v4-pro` | 通过 | 6/6（100%） | 1881 ms | 318 / 679 |

两模型配对一致率 50%。Flash 的失败分类为 `json_repair_failed` 2 次、`LLMError` 1 次。当前只有 DeepSeek 一个真实 Provider，跨 Provider 门未满足；不授予 `decision_verified`，不晋级 Advisory/Active。

## 4. 本地验证

- CDS.12 + CDS.11 + 共享协议/运行时首轮：`44 passed, 1 warning`。
- 24 路相同 feedback 并发只有一次创建/应用。
- 五类主动响应信号分别产生独立且限幅的 profile 变化。
- API 诊断不返回 delta JSON、正文、Prompt 或 raw output。

## 5. 未做事项

- 未将 profile 接入任何生产决策输出。
- 未把一个 Provider 的两个模型冒充为跨 Provider 证据。
- 未因 structured probe 通过而授予 decision-level 认证。
- 未改变现有九个 Shadow DecisionKind 的模式上限。

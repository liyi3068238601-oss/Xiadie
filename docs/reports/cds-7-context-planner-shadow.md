# CDS.7 ContextPlanner Shadow 评测

- 样本：80 个纯合成场景；不含用户数据，不调用真实 Provider。
- Fixture SHA-256：`8a957ae964e80a8e1edc04a742de87d880ad0ff5081f894e09d60d8d72c3dcbf`
- Proposal 精确匹配：100.00%
- Proposal 与 CTX v1 实际注入顺序存在差异：100.00%

## 完成门

| 指标 | 结果 |
|---|---:|
| 当前问题、最近完整轮次与输出预算保护 | 100.00% |
| 输出预算保护 | 100.00% |
| 真实 assemble 保护区验证 | 100.00% |
| 计划与实际注入差异记录 | 100.00% |

## 边界

- `context-priority-proposal-v1` 只表达语义优先级，不输出最终 token 数。
- 实际注入继续调用冻结 CTX v1 固定比例分配器；本评测不向 ContextAssembler 传入 proposal。
- 固定比例 fallback 保留，ContextPackage v1、生产聊天装配与输出预算均未修改。

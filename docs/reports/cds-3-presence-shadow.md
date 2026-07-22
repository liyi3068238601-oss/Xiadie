# CDS.3 PresenceAndThreadObserver Shadow 兼容评测

- 样本：660 轮纯合成输入；不含用户数据，不调用真实 Provider。
- Fixture SHA-256：`812d42b4c3b428a373a31dab720a8627234bb760c1df4a43d1347b0eadb85cca`
- Shadow 精确匹配：100.00%
- 冻结 EAP v2 fallback 精确匹配：24.55%
- 有效 message ID 绑定：100.00%

## 完成门

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| ‘晚安’误判预计返回率 | 0.00% | 0 |
| ‘去测试一下’开放话题识别率 | 100.00% | ≥95% |
| 未知沉默被写为拒绝率 | 0.00% | 0 |

## 边界

- 结果仅为 Shadow proposal；EAP Conversation Presence v2 仍是唯一写者。
- 差异不回写冻结 v2；如需应用，必须提出新协议及迁移影响。
- 报告只保存 case ID、分组、枚举预测与聚合指标，不保存模型输出。

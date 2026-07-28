# CIE.1 TurnIngressBuffer 验收

- 协议：`turn-ingress-buffer-v1` / `turn-envelope-v1`。
- 窗口：默认 500 ms，硬范围 300～800 ms；单 envelope 上限 20 条。
- Schema：80；CIE.1 不需要 Schema 81。
- 固定矩阵：[5, 20, 100, 500]，共 625 条纯合成消息。

## 零容忍指标

- 消息丢失率：0.00%。
- 重复处理率：0.00%。
- 跨会话/窗口串流率：0.00%。
- 顺序破坏率：0.00%。
- 附件授权归属丢失率：0.00%。

## 实现边界

- 前端 `TurnIngressBuffer` 只在 `cie_enabled=1` 时使用；缺失、关闭或设置读取失败均走旧单消息路径。
- 原始消息在后端分别写入现有 `messages`，附件分别绑定原消息；有序 envelope 仅用于本轮检索和生成，不持久化平行正文。
- `/stop`、Ctrl/Cmd+Enter、语音结束协议位及 20 条硬上限立即封口；普通输入在最后一条后 500 ms 封口。
- 当前附件范围只有 `local_text_only`；未知或混合授权范围由严格 Schema 拒绝，不静默合并。

## 回滚

关闭 `cie_enabled` 即回到冻结 fallback；删除 CIE.1 前后端协议、缓冲器和测试即可，无数据库迁移或用户数据转换。

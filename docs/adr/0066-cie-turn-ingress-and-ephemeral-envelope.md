# ADR-0066：TurnIngressBuffer 与临时 turn envelope

- 状态：Accepted for CIE.1
- 日期：2026-07-28

## 决策

CIE.1 在渲染进程内按 `session_id + window_id` 建立有界 `TurnIngressBuffer`：默认等待 500 ms，配置硬限制为 300～800 ms，单次最多 20 条。窗口封口后，一次请求携带每条原始消息的客户端 ID、正文、附件 ID、授权范围、入队时间和边界原因。

服务端不信任客户端拼接正文，而是验证唯一 ID、单窗口、附件唯一归属和 `local_text_only` 授权后重新构建 `turn-envelope-v1`。原始消息分别写入现有 `messages` 表，附件分别绑定对应原消息；envelope 只用于本轮检索、上下文和生成，不作为第二份正文持久化。

## Schema 决策

CIE.1 不占用 Schema 81。现有 `messages` 与 `message_attachments` 已能表达权威原始数据；短窗口及 envelope 都是进程内瞬态控制面。若 CIE.2 为跨进程取消、幂等 nonce 或崩溃恢复证明必须持久化请求状态，再独立评审 Schema 81，不能在本阶段预建空表。

## 封口与隔离

- 普通消息在最后一条后 500 ms 封口。
- `/stop`、Ctrl/Cmd+Enter、`voice_end` 协议位及 20 条上限立即封口。
- 会话切换会封口旧 scope；不同会话和不同窗口永不共享队列。
- 当前只接受 `local_text_only`，因此不同远传授权范围无法被静默合并。

## 回滚

`cie_enabled` 默认关闭。关闭或设置读取失败时前端继续调用原 `/api/chat` 单消息路径，后端拒绝批量 ingress；无需迁移或数据回滚。

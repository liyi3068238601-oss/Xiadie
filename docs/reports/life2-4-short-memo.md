# LIFE2.4 ShortMemo 施工与验证报告

日期：2026-07-30  
协议：`short-memo-v1`  
Schema：82  
发布状态：`shadow`（未晋级 Active）

## 已完成范围

- Schema 82 仅新增 `short_memos`、`short_memo_events` 与 ShortMemo 设置键；历史迁移未修改。
- 单写者只接受已成功入库、非临时会话中的用户原消息。请求入口固定捕获发布快照，避免同一请求混用 Shadow/Active。
- 本地确定性门要求明确近期时间与用户意图；秘密值硬拒绝，用户主动敏感事项只保留最小化概括。
- 可选远端复核默认关闭。开启后仅发送本地处理后的有界候选，模型只能返回严格 `accept` 布尔值；拒绝、异常或畸形结果均不写入，模型不能生成或改写正文。
- TTL 为 1 小时至 14 天，默认 72 小时；活动记录最多 10 条，相关召回最多 3 条。重复、容量、过期、来源删除或来源正文变化均 fail closed。
- ShortMemo 以独立低权限 `source_type: short_memo` 区块进入 CTX，不合并长期 Memory，不写 Affect、Relationship、Goal、Saga、EAP 或其他领域。
- API/UI 支持产品总开关、远端复核授权、默认 TTL、列表、改期、单删、隐私清空、导出；诊断只含计数、原因码和发布元数据，无正文。

## 验证结果

- ShortMemo 专项与 Context/LIFE API：26 passed。
- 公共聊天、Persona、WorldBook 与 LIFE 验收相关回归：60 passed。
- 前端：73 passed；TypeScript/Vite production build 通过。
- 200 条合成分类矩阵覆盖：50 条有效普通安排、30 条用户显式敏感安排、50 条秘密值、70 条非备忘文本。
- 秘密值写入、Shadow 正式写入、过期/失效来源召回、重复增长、容量伪事件、诊断正文泄漏、远端模型改写正文：均为 0。

## 当前保守结论

LIFE2.4 的实现与专项门已完成，但发布门继续保持 `shadow`。这意味着当前仅产生请求内无正文聚合诊断，不落正式 ShortMemo、也不参与召回。是否切到 Active 留到 LIFE2 全部阶段完成后的整体 Review；无需在进入 LIFE2.5 前提前开放。

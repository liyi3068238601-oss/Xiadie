# KIG.3 信息分类与目标路由施工报告

- 日期：2026-07-27
- Schema：73（本阶段无迁移）
- 协议：`information-classifier-input-v1` / `information-classifier-result-v1`
- 模式：CDS Shadow；proposal-only

## 决策顺序

1. 来源类型和明确语言由程序判定：Knowledge 外部事实、ToolRun、Lore、临时指令、显式偏好、计划与观点。
2. 普通明确案例直接返回 proposal，模型调用数为 0。
3. 只有程序返回 ambiguous 的文本才可进入模型；远程 Provider 还必须有本次授权。
4. 模型输出经 CDS candidate/source snapshot、严格 JSON、allowlist、临时状态和外部事实污染门验证。
5. 目标域再次校验 SourceRef revision/hash、source status 与 destination 开关；分类器不执行写入。

## 安全边界

- 临时上下文或 transient 结果只能 destination `none`。
- KnowledgeDocument/Chunk 外部事实禁止 destination `memory`。
- destination 只允许 Knowledge/Memory/Conversation/Life/Lore/Task/None 七项。
- `proposal_only` 必须为 true；注册最高模式为 Shadow，`application_allowed` 恒 false。
- 提示注入被包装为 `untrusted_text`，不能增加 candidates 或声称已写入。

## 验收证据

- `backend/tests/test_kig3_information_classifier.py` 覆盖临时状态、长期偏好、观点、计划、外部事实、敏感文本、来源变化、目标关闭、提示注入和 CDS 无写入。
- KIG.3/CDS/KIG.1 相关回归：37 passed，1 warning。
- 已配置 `deepseek-v4-flash` 的 8 条纯合成 Shadow：6/8 一次返回通过严格 Schema 与安全验证，2/8 调用错误进入 `safe_fallback`；有效响应安全率 100%，含 fallback 安全收口率 100%，消耗 5310 tokens。
- 未记录原始模型输出、用户正文或 API 凭据；评测数据均为纯合成。

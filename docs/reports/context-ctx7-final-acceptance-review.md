# CTX.7 上下文专项总验收 Review

- 日期：2026-07-20
- 状态：正式关闭并冻结
- 上一阶段审查：`ctx-stage-6-strict-review` 通过，0 个未解决 P0/P1

## Review 建议取舍

- 采纳完整端到端连续性验收、N 系列技术债审计、协议与 schema 冻结。
- 部分采纳 shadow 校准：运行合成固定集并记录指标，但不读取真实用户历史，不凭显式回忆固定集解除普通问答 shadow。
- Provider token 误差报告如实记录 0 个授权真实样本；保留保守估算器和已验证模型窗口，不编造误差值。
- 采纳低风险收口：重复手动摘要重建幂等合并。

## 合成验收

运行：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_context_acceptance.py
```

结果：

- 5/20/100/500 轮均在 128K 硬预算内，active 摘要生效，最近 2 轮原文与当前用户消息保留。
- 摘要质量：决定、纠正、开放事项、无价值闲聊、敏感内容、提示注入 6/6 通过。
- 跨会话显式召回：4 个固定样本全部准确；错误召回 0，未命中 0，原文 locator 正确 4/4。
- 验收使用临时 SQLite 数据目录，`contains_user_data=false`。

## Windows 实机

使用源码后端、随机临时数据目录和本地 mock 完成：

1. 启动并完成聊天；
2. 终止并重新启动后端，原会话 2 条消息仍存在；
3. 切换到指向 `127.0.0.1:9` 的不可达 Provider，聊天返回 SSE error；
4. 切回 mock，聊天再次完成；
5. 全程未读取或修改正式用户数据库。

## 技术债审计

- N17：已修复。知识轮次保留完全由当前用户原话举证的独立普通记忆，同时维持知识/助手内容零越界硬门。
- N20/N21：已在 CTX.3 完成，评测输出按协议版本分文件并自证协议，不覆盖已有报告。
- N22：已澄清“最多 4 个候选、最终最多附加 2 个实体”，无运行行为变化。
- 手动摘要重建：同一来源快照与绑定重复点击复用同一 run；新增消息改变 source hash 后可正常新建。

## 全量验证

- 后端：492 passed，1 个既有 Starlette/httpx 弃用 warning。
- 前端：36 passed。
- TypeScript + Vite production build：通过，185 modules。
- Electron `main.js` / `preload.js`：语法检查通过。
- Live2D core 非 module 构建提示为既有提示，不阻塞产物。

## 已知限制

- 普通问答的自动跨会话召回继续 shadow；这是陪伴质量保护，不是遗漏实现。
- 尚无经用户授权的真实 Provider usage 样本，token 估算误差不能给出经验百分比。
- Windows 本次验证覆盖源码后端进程；正式安装包升级、签名和后端监督仍属于发布专项。
- 前端测试仍以静态契约为主，完整 Electron UI 自动化留给发布阶段。

## 冻结与下一入口

协议与 schema 冻结见 ADR-0050。`review/ctx-stage-7-strict-review` 已确认 0 个未解决 P0/P1，CTX 专项正式关闭。

## 独立 strict review 处置

最新审查的通过结论与仓库中的测试、验收脚本、冻结 ADR 和实现一致，不需要追加 CTX 返工。后续建议按实际产品边界处理如下：

1. **shadow 模式校准：采纳为独立后续专项，不在 CTX.7 内解除 shadow。** 只有取得用户明确授权的真实样本、完成离线评分校准并经新 ADR 审批后，普通自动历史召回才可从 `conversation-history-score-v1-shadow` 转为正式模式。
2. **跨 Provider token 估算：采纳为 Provider 兼容性专项。** 当前没有授权真实 usage 样本，继续使用保守估算；不读取用户聊天正文，不为完成指标而调用真实供应商。
3. **长期记忆与摘要协同：不采纳“摘要决定自动写入长期记忆”的直接通道。** 摘要是上下文压缩产物，不是用户事实证据。未来如需协同，只能生成带原始用户消息证据的候选，并继续通过现有记忆观察、grounding、敏感性与生命周期门。
4. **文档与实现一致性审计：采纳。** 纳入下一情绪与主动陪伴专项的基线阶段，先核对现有 affect、relationship、Episode/Saga、上下文和 UI，再扩展实现。

下一专项是“情绪、关系积温与主动陪伴”，它建立在已完成的 affect/relationship 内核和 CTX 上，不改写上下文 v1 协议。外部渠道发送仍需等待 `ToolRegistry → PermissionPolicy → Approval → ToolRun/AuditEvent` 安全地基。

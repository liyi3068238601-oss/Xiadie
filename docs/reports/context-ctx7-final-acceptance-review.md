# CTX.7 上下文专项总验收 Review

- 日期：2026-07-20
- 状态：实现与内部总验收完成，等待独立 strict review
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

协议与 schema 冻结见 ADR-0050。独立总审查必须确认 0 个未解决 P0/P1 后，CTX 专项才能正式关闭。下一产品阶段应回到受控 Agent 地基，而不是继续增加普通聊天技术展示：`ToolRegistry → PermissionPolicy → Approval → ToolRun/AuditEvent`。

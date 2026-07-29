# CIE.6 整体验收与正式冻结

- 协议：`cie-final-acceptance-v1`
- Schema：81（未占用 82）
- ADR：ADR-0071
- 机器报告：`docs/reports/cie-6-final-acceptance.json`
- Windows 烟测：`docs/reports/cie-6-electron-smoke.json`
- 状态：已通过独立 Review 并正式冻结（0 P0 / 0 P1）

## 验收矩阵

1. **连续消息与打断：** 5/20/100/500 轮共 625 条；顺序、附件范围和会话/窗口隔离保持，generation 取消、persistence 拒绝迟到取消、重放和旧回复保护通过。
2. **运行环境：** 本地/远端 Provider 治理、在线/断网降级、前台/托盘后台、休眠恢复 guard 与时钟回拨保守模式通过。为避免破坏用户环境，断网、休眠和时钟回拨使用确定性模拟与 Electron contract，没有真实切断网络或修改系统时间。
3. **图片：** 本地、远端逐次授权、拒绝、发送前删除、TTL 过期、Provider 位置版本变化、模型变化和不支持模型矩阵通过；原始字节成功发送后销毁。
4. **ContextContribution：** 默认关闭、恶意正文、NFKC/零宽混淆、超预算、过期、陈旧证据、重复 ID 和 contributor 超时矩阵通过。
5. **Windows Electron：** 当前源码在隔离数据目录实际启动后端、Vite 和 Electron；后端健康、前端加载、Electron 连续存活 8 秒。结束后 8756/5173 释放，临时目录和 dev 标志清理。

## 零容忍指标

机器报告中的 10 项最终指标全部为 0：消息丢失、跨会话合并、幽灵回复、重复回复/持久化、未授权图片远传、不支持 vision 假声明、第三方自由 Prompt、过期贡献、完整内心推理持久化，以及任一 CIE 失败影响基础聊天。

## 最终自动验证

- 后端全量：`2597 passed, 1 warning`，耗时 7 分 55 秒；唯一警告是既有 Starlette TestClient/httpx2 弃用提示。
- 前端：`71 passed`；TypeScript/Vite 生产构建通过，192 modules。
- Electron：3 项生命周期 contract 通过；`main.js`、`preload.js` 语法通过。
- Windows 当前源码 Electron 烟测：通过。
- 发布资源：frontend、冻结后端、Lore、BGE-M3 源/暂存/打包文件及 SHA-256 全部通过。
- `git diff --check`：通过。

## 最终独立 Review 结果

独立 Review 于 2026-07-29 通过：0 个 P0、0 个 P1、2 个可延后 P2；专项测试 7/7、零容忍指标 10/10、验收矩阵 36/36 均通过。总门 fallback、组合 exactly-once、目标变化 fail-closed、无正文诊断/回放和 Electron 生命周期五个重点均通过。

两个 P2 不在冻结前修改运行时：`cleanup_expired()` 的周期调度需与长期运行维护机制一并设计；回放中的 `affect_observation`/`memory_observation` 是限时结构化元数据，精简会改变现有重放契约。两项均登记为后续兼容维护候选。

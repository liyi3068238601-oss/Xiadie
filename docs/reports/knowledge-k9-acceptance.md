# 知识系统 K.9 总验收报告

- 日期：2026-07-19
- 开工提交：`95855fc`
- 数据库：schema 41
- 结论：通过

## Review 处理

开工前读取 `review/knowledge-stage-k8-review/knowledge-stage-k8-review-feedback.html` 一次。该 review 仍基于
`799b187`、schema 39 和 374 项测试，落后于开工提交。N15 文案、K.8 专项测试、查询净化、协议版本、观察器
隔离和 `knowledge_discarded` 计数均已在当前代码实现；成功清理日志也已存在，因此没有重复加入第二套日志。

## 验收矩阵

| 边界 | 结果 | 主要自动化证据 |
|---|---|---|
| off / explicit / smart | 通过 | `test_knowledge_context.py`、`test_knowledge_grants.py`：off 不检索、explicit 显式召回、smart high 注入与 ask 授权 |
| local / remote / unknown × 策略 | 通过 | `test_provider_location_and_document_policy_matrix`；unknown 按 remote，sensitive/local_only 不可授权远传 |
| 同意 / 拒绝 / 过期 / 重放 | 通过 | grant 专项覆盖 allow-once、deny+skip、TTL、串行与并发重放、在线失败后不可复用 |
| 策略 / 模式 / 模型变化 | 通过 | revision、recall mode 和新增 model switch 用例均在写消息前撤销授权 |
| 注入 / 来源变化 | 通过 | 知识正文只进入低权限 quoted JSON；新增签发后 source change 用例失败关闭 |
| 删除 / 重建 / 向量失败 / FTS 降级 | 通过 | management 与 embedding 专项覆盖立即退出召回、向量清除、失败重试及 FTS fallback |
| 引用真实性与生命周期 | 通过 | 引用白名单、当前哈希核验、审计最小化、清库与 citation 生命周期专项 |
| 知识 / 记忆隔离 | 通过 | `test_knowledge_memory_isolation.py` 覆盖四类伪造、用户决定证据和 discarded 审计；观察器崩溃不影响回答/引用 |

## 构建与运行证据

- 后端全量：404 passed。
- 前端：33 passed。
- 生产构建：Vite 188 modules，TypeScript 通过。
- 冻结后端：PyInstaller 6.21.0 / Python 3.12，实际隐藏进程健康检查通过；BGE-M3 `available=true`、`local_only=true`。
- Electron：`main.js`、`preload.js` 语法通过；`electron-builder --dir` 通过。
- 安装器：`electron-builder --win` 通过，生成未签名 NSIS `遐蝶-Setup-0.1.0.exe` 及 blockmap。
- 资源：源目录、stage、unpacked 三份 ONNX 均为
  `0826f8c1ab9edf1801db86c61919d4d108e8bfc0b809ec823ad366882ff0b77d`；前端、冻结后端和内置 Lore 均存在。

可重复命令：

```powershell
cd E:\Xiadie\Xiadie\backend
.\.venv\Scripts\python.exe -m pytest -q

cd E:\Xiadie\Xiadie\frontend
npm.cmd test -- --run
npm.cmd run build

cd E:\Xiadie\Xiadie
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-frozen-backend.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-release-resources.ps1
```

## 已知边界

- 验收使用本地 mock/受控流验证远程传输协议，没有把测试知识正文发送给真实外部 Provider；具体供应商网络兼容性属于模型配置回归。
- 当前安装器未代码签名，可能出现 SmartScreen；正式发布需按 ADR-0044 重新启用签名并做安装/升级验收。
- BGE-M3 增加约 543 MiB 安装体积；模型缺失或推理失败时仍以 FTS 安全降级。
- Live2D 当前素材仅限个人使用，不能因本次安装器成功而对外分发。

## 完工后 review 处置（2026-07-19）

完工后收到 `knowledge-stage-k9-sec1-review-feedback.html`。它仍以 374 项后端、32 项前端测试为基线，早于
本报告的 404/33 基线，并把临时 SEC.1～SEC.3 代码视为既定施工阶段。逐项处置如下：

| 建议 | 决定 | 原因与后续边界 |
|---|---|---|
| SEC.1 增加 SecretStore 专项测试 | 调整后采纳，进入下一份计划 | 当前实现尚未成为真实密钥边界：业务调用、连接测试和模型发现仍读取 `providers.api_key`，SQLite store 只是第二份未加密副本，部分写入错误还会被静默吞掉。不能先用测试固化错误过渡态；新计划应先定义迁移状态机、失败恢复和唯一读取入口，再补接口、迁移与端到端测试。 |
| 清除旧明文 key 推迟到 safeStorage | 调整后采纳，列为下一计划核心 | 不能无限期保留双份明文。应设计 dual-read/受控迁移/验证/清除/回滚顺序，并把 Electron safeStorage、开发环境替代实现和旧库升级放在同一阶段验收。 |
| 等 STR/TOOL 完成后另写 v0.7 总设计 | 不采纳该依赖关系 | 密钥与上下文属于当前可靠性风险，无需等待 ToolRegistry 或大规模结构治理。按用户决定，在 K 系列关闭后另写一份聚焦的新计划。 |
| Live2D 授权替换 | 推迟 | 已在基线和 ADR-0044 记录；它阻塞公开发布，但不阻塞知识系统本地闭环。 |
| ProviderCapability.context_window 与上下文预算 | 调整后采纳，进入下一份计划 | `context_budget.py` 已有临时代码，但仍按 Provider ID 硬编码、重复定义 estimator，且 `max(512, …)` 与最小保留轮次不能证明总输入不越界。新计划需按 provider+model 能力、输出预留、系统/知识/记忆预算和严格不变量重新设计，并保留 `context_trimmed` 可观测性。 |
| K.8 遗留项统一推迟 | 不采纳 | K.8 专项、UI 一致性、`clean_query` 和检索协议版本已经在 K.8/K.9 验收，review 的描述已过时。 |

本次处置不修改 SEC 临时代码，也不把它们计为完成；K.9 结论保持关闭。下一阶段必须先创建新的施工计划，再决定
是修复、迁移还是替换这些半成品。

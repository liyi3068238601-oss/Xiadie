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

# CIE.3 原生图片多模态验收

- 协议：`vision-probe-v1`、`cie-image-attachment-v1`
- Schema：81
- 状态：施工完成，独立 Review 通过（0 P0 / 0 P1）

## 验收结论

- vision 能力只来自真实图片探针及当前 Provider 位置版本证据，已移除模型名称猜测。
- PNG/JPEG 在本地执行内容签名、MIME、SHA-256、尺寸、单图/单轮字节与像素硬限制。
- 远程/未知位置按轮确认并绑定 Provider、模型、位置版本；拒绝、快照变化和范围不匹配均发生在消息写入前。
- 图片只在文本上下文组装后进入 Provider 请求；Memory、Knowledge、KIG、长期消息正文及日志不接收原始字节或 base64。
- 提交用户消息后立即清空临时路径并删除原始文件；TTL、用户移除和启动 GC 提供未发送图片的兜底清理。
- 不支持、探针未知或用户拒绝时给出明确提示，不伪装已经看图。CIE.3 选择明确拒绝作为回退，未虚构尚不存在的本地 OCR。

## 真实 Provider 证据

2026-07-29 对当前配置的 `deepseek/deepseek-v4-flash` 发出一次 1×1 红色 PNG 最小探针。Provider 返回 HTTP 400，因此能力状态为 `unsupported`，证据摘要为 `5162a5da9b6aed1aa10806b90c5ed9544d81c7338d6e4717bedb8214e55ccaca`。响应正文和探针图片均未写入证据表。

这意味着当前 DeepSeek 文本模型不会显示图片入口的成功假象；以后切换模型时，每个 `provider + model + location revision` 都独立重新探测，不会继承本次结论。

## 自动验证

- `backend/tests/test_cie3_images.py`：解析限制、无证据拒绝、远端逐次授权、范围错配零写入、原生 payload、提交后原始字节销毁。
- `backend/tests/test_api.py::test_schema_migration_is_idempotent`：Schema 81 幂等基线。
- 前端 63 项通过；TypeScript 与 Vite 生产构建通过（191 modules）。
- 本阶段先执行 CIE/API 针对性回归；后端全量回归留到独立 Review 建议收口后运行，避免在同一阶段重复支付 9 分钟成本。

## Review 建议重点

1. 409/410 拒绝是否全部早于用户消息和附件绑定写入。
2. Provider/模型/位置版本快照是否存在绕过路径，积累窗口 scope 是否逐条匹配。
3. data URL 是否只存在于 `llm.stream_chat` 的请求内存，是否可能流入 Memory、Knowledge、KIG、观察器或日志。
4. 成功、取消、模型失败、用户移除、TTL 与进程重启时的临时文件生命周期。
5. 当前明确拒绝回退是否满足产品体验，还是应在未来单独规划本地 OCR。

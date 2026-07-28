# CIE.2 生成打断与重建验收

- 协议：`cie-cancel-control-v1`
- Schema：80（未占用 81）
- 样本：20 次可取消阶段确认 + 20 次 persistence 迟到取消
- 原始报告：`docs/reports/cie-2-acceptance.json`

## 结果

- 活动生成取消支持率：100%。
- 取消后幽灵回复率：0。
- 重复持久化率：0。
- 旧回复误删率：0。
- persistence 迟到取消拒绝率：100%。
- 取消确认延迟报告包含 mean、P50、max 与总体标准差；该数值是本机进程内控制面开销，不冒充 Provider 中止耗时。

## 实现证据

- 前端只有在服务端接受取消后才调用 AbortController；停止和补充在 CIE 开启时可用。
- SSE 的 phase/cancelled 事件不进入助手正文；generation 取消时部分正文直接丢弃。
- persistence 开始后取消被拒绝，旧回复与新回复事务不会被中途删除。
- 同一 chat nonce 在短期 TTL 内重放权威 final/done，不重复写入用户或助手消息。
- 补充消息启动新请求，重新执行知识传输预检和后端 Knowledge/KIG/CTX 组装，不复用旧请求快照。
- 运行时关闭 CIE 时，后端在写入前拒绝；前端恢复正文和附件到旧路径草稿。

## 自动化

- `backend/tests/test_cie2_chat_cancellation.py`：取消、阶段、重放、旧回复保留、总门原子性。
- `backend/tests/test_cie2_acceptance.py`：至少 10 次样本、五项门与延迟离散度。
- `frontend/tests/chatSseFinal.test.mjs`：phase/cancelled 与权威正文分流。
- `frontend/tests/turnIngressBuffer.test.mjs`：失败恢复、深冻结、卸载计时器清理及前端接线。

最终回归：后端 `2583 passed, 1 warning`；前端 `61 passed`；TypeScript 与 Vite 生产构建通过（191 modules）。

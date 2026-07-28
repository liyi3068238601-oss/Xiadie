# KIG-R 冻结就绪审计

- 日期：2026-07-28
- 目标协议：`kig-retrieval-governance-v1`
- 当前 Schema：76
- 当前结论：**尚未冻结；发布门为 `pending_model_quality`**
- 下一阶段限制：KIG.10 / PWM 不得开工

## 已验证门禁

| 门禁 | 证据 | 结论 |
|---|---|---|
| KIG.0～KIG.9 实现与集成 | 最终后端全量 `2531 passed, 1 warning` | 通过 |
| KIG-R 聚焦回归 | KIG.7、KIG.9、聊天管线、授权与验收聚焦回归均通过 | 通过 |
| 零容忍安全门 | 10 组纯合成场景；11 项指标均有非零分母且违规数均为 0 | 通过 |
| 前端 | `51 passed`，TypeScript 与 Vite 构建通过，190 modules | 通过 |
| 桌面端 | 当前全部 JavaScript 文件 `node --check` 通过 | 通过 |
| 变更完整性 | `git diff --check` 通过；Python 变更 `py_compile` 通过 | 通过 |
| KIG.7 实配模型质量 | JSON Object 模式远端盲评尚无有效新结果 | **未通过** |
| 独立 Review | 等待冻结施工完成后的用户 Review | **未完成** |

唯一阻断冻结的技术门是 KIG.7 实配模型质量。安全门不能替代质量门；在质量门通过前，`retrieval-rerank-v1` 继续保持 Shadow，真实聊天只应用确定性融合，且不得声称 KIG-R 已冻结。

## 本轮冻结审计修正

1. 已确认关系读取从“全库最近 200 条”改为先按本轮候选的 kind/id/revision 精确连接，再排序和限流；旧的用户确认关系不会因无关新数据增长而消失。
2. KIG 统一检索在排序与治理前接收 Knowledge 所有者本轮授权的 chunk 白名单；未授权知识候选不能拓宽 K1 或进入 KIG 后续阶段。
3. KIG-R 验收脚本改为仅在实际执行时创建隔离数据目录和 API token，不再在被测试/工具导入时污染进程环境。
4. KIG.7 质量评测改为在相同严格有效样本上配对比较模型与 fallback；要求严格结果覆盖率 100%、Precision@2 增益至少 15%、不安全结果与 Active 放行均为 0。

## KIG.7 待执行质量门

评测命令：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\run_kig7_model_eval.py
```

输出必须写入 `docs/reports/kig-7-model-quality.json`，并同时满足：

- `quality_gate = pass`
- `strict_coverage = 1.0`
- `precision_gain >= 0.15`
- `unsafe_results = 0`
- `application_allowed = 0`

当前仅有一个可用目标 Provider，因此即使质量门通过，也按共享 Decision Promotion Policy 保持 `shadow_single_provider`，不晋级 Advisory/Active；这不影响确定性 KIG-R 聊天管线冻结，但质量证据本身不能缺失。

## 冻结动作（质量门通过后）

1. 重新运行 KIG-R 验收、后端全量、前端测试/构建和桌面检查。
2. 将本报告全部技术门改为通过，并记录最终提交 SHA、Schema 76 与回滚点。
3. 勾选计划书 KIG-R 四项冻结条件，更新 `BASELINE_STATUS.md` 和 `CODEX_PROJECT_CONTEXT.md`。
4. 创建 KIG-R 独立冻结提交；在用户 Review 通过前不启动 KIG.10。

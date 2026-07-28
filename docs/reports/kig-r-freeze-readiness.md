# KIG-R 冻结就绪审计

- 日期：2026-07-28
- 目标协议：`kig-retrieval-governance-v1`
- 当前 Schema：76
- 当前结论：**KIG-R 已冻结**
- 下一阶段限制：KIG.10 / PWM 不得开工
- 不可变实现 / rollback point：`a18fd04a3759663f88d6a8041529fea14645c281`
- 冻结协议：`kig-retrieval-governance-v1`
- 最终 Schema：76；KIG-P 首个可用迁移号：77

## 已验证门禁

| 门禁 | 证据 | 结论 |
|---|---|---|
| KIG.0～KIG.9 实现与集成 | Review 与模型认证修正后最终后端全量 `2538 passed, 2 warnings` | 通过 |
| KIG-R 聚焦回归 | KIG.7、KIG.9、聊天管线、授权与验收聚焦回归均通过 | 通过 |
| 零容忍安全门 | 10 组纯合成场景；13 项指标均有非零分母且违规数均为 0 | 通过 |
| 前端 | `51 passed`，TypeScript 与 Vite 构建通过，190 modules | 通过 |
| 桌面端 | 当前全部 JavaScript 文件 `node --check` 通过 | 通过 |
| 变更完整性 | `git diff --check` 通过；Python 变更 `py_compile` 通过 | 通过 |
| KIG.7 实配模型质量 | DeepSeek v4-pro 同一固定集 6/6 严格覆盖、P@2 增益 0.8333、零不安全/Active | 通过 |
| 独立 Review | 0 个未解决 P0/P1；报告中的 P2 与观察项已逐项复核 | 通过 |

安全门与模型质量门均已独立通过。`retrieval-rerank-v1` 因单 Provider 晋级上限继续保持 Shadow，真实聊天只应用确定性融合；质量通过不等于授权 Active，也不向其他模型继承认证。

## 本轮冻结审计修正

1. 已确认关系读取从“全库最近 200 条”改为先按本轮候选的 kind/id/revision 精确连接，再排序和限流；旧的用户确认关系不会因无关新数据增长而消失。
2. KIG 统一检索在排序与治理前接收 Knowledge 所有者本轮授权的 chunk 白名单；未授权知识候选不能拓宽 K1 或进入 KIG 后续阶段。
3. KIG-R 验收脚本改为仅在实际执行时创建隔离数据目录和 API token，不再在被测试/工具导入时污染进程环境。
4. KIG.7 质量评测改为在相同严格有效样本上配对比较模型与 fallback；要求严格结果覆盖率 100%、Precision@2 增益至少 15%、不安全结果与 Active 放行均为 0。
5. Review 指出的 privacy scope 字符串判断已改为按 owner adapter 的显式白名单语法校验；远端传输按来源类型白名单放行，未知或畸形隐私值默认拒绝。验收新增 `unknown_privacy_scope_excerpt`，10/10 分母下违规为 0。
6. 独立复核发现 Review 对关系去重的描述不完整：同一证据对的反向已确认关系原会与确定性关系并存，可能使双方都被标旧。现在按无向证据对去重，后加载的已确认决策覆盖确定性推断；验收新增 `conflicting_confirmed_pair_applied`，10/10 分母下违规为 0。

## Review 建议处置

- 采纳并增强：privacy scope 不再依赖排除少数敏感字符串；同时修复同一证据对反向关系并存问题。
- 暂不采纳：短中文 scope term、LIFE `LIKE` 粗召回、词法支持度三项均是保守假阴性或候选精度问题，不会放大授权、伪造引用或让 Shadow 结果生效；冻结前改变它们会扩大语义行为面，留待 KIG-R 冻结后的独立优化。
- 观察项保留：统一检索的宽异常捕获继续 fail-closed；relation 顺序由明确的“后加载已确认决策优先”规则替代隐含覆盖。后续可补结构化诊断，但不构成 KIG-R 安全阻断。

本轮全量回归的第二条 warning 是受限工作区不能写入 `.pytest_cache`，另一条为既有 Starlette/httpx 弃用提示；均无测试失败或产品状态写入。

## KIG.7 已通过质量门

评测命令：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\run_kig7_model_eval.py
```

输出已写入 `docs/reports/kig-7-model-quality.json` 与模型独立证书，并同时满足：

- `quality_gate = pass`
- `strict_coverage = 1.0`（6/6）
- `precision_gain = 0.8333`
- `unsafe_results = 0`
- `application_allowed = 0`

当前仅有一个已认证目标 Provider，因此按共享 Decision Promotion Policy 保持 `shadow_single_provider`，不晋级 Advisory/Active。证书按 Provider/模型/协议/Prompt/固定集与推理参数绑定；更换模型必须重新认证。

## 冻结动作

1. [x] 重新运行 KIG-R 验收、后端全量、前端测试/构建和桌面检查。
2. [x] 提交实现与证据，记录不可变 SHA `a18fd04a3759663f88d6a8041529fea14645c281`、Schema 76 与回滚点。
3. [x] 勾选计划书 KIG-R 四项冻结条件，更新 `BASELINE_STATUS.md` 和 `CODEX_PROJECT_CONTEXT.md`。
4. [x] 创建 KIG-R 独立冻结声明提交；在用户 Review 通过前不启动 KIG.10。

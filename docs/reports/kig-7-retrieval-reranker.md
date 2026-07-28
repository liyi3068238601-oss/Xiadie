# KIG.7 LLM 语义重排施工报告

- 日期：2026-07-27
- Schema：74（本阶段无迁移）
- 协议：`retrieval-rerank-input-v1` / `retrieval-rerank-result-v1`
- 当前模式：CDS Shadow；禁止 Advisory/Active
- 阶段结论：核心实现完成，模型质量门待补

## 已交付

1. 复用 CDS DecisionRun、CandidateEnvelope、快照、structured output、fallback、诊断保留与模式门，没有复制通用重排运行时。
2. 每次最多 30 个 KIG.6 候选、最多选择 12 个；模型必须完整排列输入 ID，并逐项标记 direct、partial、background、conflict、outdated、duplicate 或 irrelevant。
3. ranked IDs 必须是输入候选的精确排列；selected IDs 只能来自输入、保持排序、不得重复、不得选择 excluded/outdated/duplicate/irrelevant。
4. 来源在模型调用前与输出验收时复核 revision/hash/status/privacy/locator；确定性 fallback 也实时解析 SourceRef，变化或撤销候选不会因 fallback 被重新选中。远端模型不得接收未获许可的 Knowledge `local_only/ask_each_time` excerpt。
5. 模型失败使用独立 lexical/vector/metadata/recency 信号的确定性融合；Shadow 对比只输出 Jaccard、位置变化和计数，不保存 query 或 excerpt。
6. 共享 `llm.complete_json` 增加 opt-in `response_format=json_object`，默认关闭；仅 KIG.7 显式启用，已有观察器调用不变。

## 自动验收

- `backend/tests/test_kig7_retrieval_reranker.py`：10 项，覆盖七类角色、候选白名单、排序/预算、来源变化、模型失败、单候选旁路、远端/传输授权、Shadow 与 JSON Object opt-in。
- 扩大到 CDS、CTX、Knowledge、KIG.0～KIG.7 与共享 JSON completion 的核心回归：`924 passed, 1 warning`。
- 非输入候选选择通过率 0；来源变化后旧候选选择率 0；Shadow 应用放行率 0。

## 实配模型证据与未通过项

启用 JSON Object 前共执行 18 次纯合成调用：

- `deepseek-v4-flash`：6 次，严格结果 0，安全回退 6。
- `deepseek-v4-pro` 首轮：6 次，严格结果 1，安全回退 5。
- `deepseek-v4-pro` 带 15 秒间隔的人工标签盲评：6 次，严格结果 0，安全回退 6；其中 JSON repair 失败 4、服务错误 2。该固定集故意使旧融合 Precision@2 为 0，但没有严格模型结果，因此不能计算或宣称模型提升。

18 次均为来源越界 0、`application_allowed` 0，证明失败安全，但不证明相关性质量。随后已加入 JSON Object 模式；远端复测被当前 Codex 外部用量额度拒绝，尚无有效证据。

因此 Decision Promotion Policy 的质量门仍为 `[~]`：不得晋级，不得接入真实聊天排序。KIG.8 可在确定性候选基础上施工，但 KIG-R 冻结前必须补跑隔离盲评，并要求严格有效样本覆盖与人工相关性提升同时达标。

KIG-R 冻结审计补强（2026-07-28）：`run_kig7_model_eval.py` 已消除模块导入时的数据目录/API token 副作用，并将质量判定改为同一批严格有效样本上的配对比较。冻结门要求 6/6 严格结果、Precision@2 相对同样本确定性 fallback 提升至少 15%、不安全结果与 Active 放行均为 0；结果写入 `docs/reports/kig-7-model-quality.json`。当前尚未取得 JSON Object 模式远端复测结果，状态不变。

回滚只需移除 KIG DecisionKind/import 与 `json_mode=True` 调用；共享 JSON mode 默认关闭，Schema 与来源数据均无变化。

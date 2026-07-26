# CDS.2 模型运行时施工与 review 处置

> 日期：2026-07-22  
> 基线：`c14555b`（CDS.1）  
> 状态：CDS.2 strict review 已通过（0 P0/P1）；已进入 CDS.3。

## 1. CDS.1 strict review 处置

外部 review 结论为 0 P0、0 P1、3 P2，允许进入 CDS.2。

| 建议 | 处置 | 理由 |
|---|---|---|
| P2-1：tuple 判断依赖类型字符串 | 采纳并立即修正 | 改用 `get_type_hints` + `get_origin(...) is tuple`，兼容 postponed annotations |
| P2-2：底层 outcome writer 可被绕过候选验证 | 采纳并立即收紧 | 改为内部 `_record_validated_decision_outcome`，调用方必须提交与 run 完全一致的已验证 candidate snapshot hash；新增拒绝测试 |
| P2-3：诊断端点缺少细粒度管理员权限 | 延后 CDS.13 | 当前为本地单用户应用，所有 `/api` 已受全局临时 token 保护且诊断严格无正文；项目尚无真实角色体系，此时添加“管理员”会形成伪权限模型 |

## 2. CDS.2 实现

- Schema 62 延续唯一 `decision_runs`，新增逻辑角色、Provider 位置修订和认证级别。
- `cognition_runtime.py` 实现角色路由、binding revision、合成 structured probe、认证门禁、按 decision kind 隔离的熔断和统一 fallback。
- `llm.complete_json` 支持每任务 timeout、temperature、top_p，并返回 Provider usage 与端到端延迟。
- `CognitionBudgetGovernor` 实现滚动/每日 token、本地/远端并发、前台压力、网络、电池和低优先级取消；控制表不存正文。
- `/api/chat` 新用户消息会取消尚未开始的 diary、PWM、offline refinement，已开始任务不被强制中断。
- body-bearing cognition 对 unknown/remote 默认 fail-closed；当前仅 synthetic body-free probe 可运行。

## 3. 安全与行为边界

- 当前生产注册表只有 `protocol_probe`，最高模式 Shadow；没有真实领域 decision kind 获得 Advisory/Active。
- 不保存 Prompt、原始输出、用户正文或候选正文；失败只记录白名单 error code、token、延迟和状态。
- 单一 decision kind 熔断不影响其他 kind；所有基础设施失败回到注册的旧算法 fallback，不改变聊天行为。
- KFC 2.1.1 只作为项目外只读参考；CIE 计划记录了 AGPL 边界，本阶段未复制其代码。

## 4. 验证

- CDS.2 与相关兼容测试：`71 passed`。
- 后端全量：`967 passed, 1 warning`。
- 警告仍为 FastAPI/Starlette 的既有 `httpx2` 迁移提示。

## 5. 停线门

CDS.2 必须经独立 review 确认 0 个未解决 P0/P1，方可进入 CDS.3。review 期间不得增加真实 decision kind、放开远端正文或提升模式。

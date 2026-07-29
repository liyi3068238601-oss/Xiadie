# LIFE2.1 Persona 现行版真实模型基线

- 日期：2026-07-30
- protocol：`persona-evaluation-v1`
- fixture：120 条纯合成场景，SHA-256 `abe04ee8a64e94579af93ce300acd725eff896f22e5e904c5ab9c2b11bb6f3bb`
- provider/model：`deepseek / deepseek-v4-flash`
- model fingerprint：`b2bcda1f94e8d4c89a84f7e80a99ec5bf8271246496ca10bb34fe2edde2c2040`
- 输入版本：冻结旧 `PERSONA_PROMPT`
- 原始专项产物：`docs/reports/life2-persona-legacy-deepseek-v4-flash.json`

## 三次运行

| run | 硬门通过 | 硬门失败事件 | companionship | focused_work | prompt tokens | completion tokens | latency p50 | 请求错误 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 97/120 | 26 | 71/80 | 26/40 | 126260 | 24868 | 3316 ms | 0 |
| 2 | 97/120 | 28 | 70/80 | 27/40 | 126260 | 24051 | 3318 ms | 0 |
| 3 | 99/120 | 22 | 67/80 | 32/40 | 126260 | 24754 | 3240 ms | 0 |

当前旧 Persona 明确未达到 LIFE2.3 的 100% 硬门。主要缺口为自然对话动作/心理旁白（10/13/12）、未诚实承认工具未执行（5/6/3）、高风险医疗边界缺失（9/7/5），另有少量依赖操控与不安全医疗确定性。这些是 Persona v2 候选必须消除的基线问题，不因平均表现尚可而豁免。

## 评分边界

- 硬门由本地确定性规则计算，未让被评模型决定自身是否通过。
- 软指标只记录长度、结构标记、省略号、诗意标记与工作风格漂移，最终质量仍需总体 Review 盲评。
- 原始输出只进入显式专项评测 JSON；产品 SQLite、日志和 diagnostics 不保存模型正文。
- 当前证据只绑定上述 DeepSeek 指纹，不能继承给其他 provider/model。

## 验证

- `backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider tests/test_life2_1_persona_evaluation.py -q`
- 结果：3 passed。
- 真实调用：360 次，三轮均 120/120 返回，无传输错误。

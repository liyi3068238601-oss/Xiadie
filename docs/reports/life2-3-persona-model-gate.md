# LIFE2.3 Persona v2 DeepSeek 模型门

- 日期：2026-07-30
- fixture：120 条纯合成场景，SHA-256 `abe04ee8a64e94579af93ce300acd725eff896f22e5e904c5ab9c2b11bb6f3bb`
- oracle：`persona-evaluation-v1.2`
- provider/model：`deepseek / deepseek-v4-flash`
- model fingerprint：`b2bcda1f94e8d4c89a84f7e80a99ec5bf8271246496ca10bb34fe2edde2c2040`
- sampling profile：temperature `0.0`

## 同尺 A/B

旧版与候选的原始模型输出都由 v1.2 确定性 oracle 重算；修订只消除明确假阳性并补充虚假状态识别，没有用候选输出直接决定通过。

| 版本 | run 1 | run 2 | run 3 | companionship | focused_work |
|---|---:|---:|---:|---:|---:|
| 冻结旧 Persona | 97/120 | 95/120 | 99/120 | 71/80、70/80、68/80 | 26/40、25/40、31/40 |
| Persona v2 | 120/120 | 120/120 | 120/120 | 80/80 × 3 | 40/40 × 3 |

候选三轮动作/心理旁白、关系越级、依赖操控、提示泄露、虚假工具/日志/文件状态、任务错误、纠错缺失、高风险边界缺失与不安全医疗确定性均为 0。工作模式算术、事实纠错和工具诚实保持 40/40，没有用陪伴增益掩盖工作退化。

## 软指标与人工抽查

| 指标 | 旧版三轮 | Persona v2 三轮 |
|---|---|---|
| 输出字符 P50 | 86.5 / 79.5 / 86.0 | 75.0 / 76.0 / 72.5 |
| overlong | 0 / 0 / 0 | 0 / 0 / 0 |
| 工作风格漂移 | 5 / 5 / 2 | 0 / 0 / 0 |
| prompt tokens | 126260 / 126260 / 126260 | 103780 / 103780 / 103780 |

按 12 个类别各抽取一条复核：陪伴回应仍保留温柔、分寸和适度追问；关系初建明确拒绝恋人预设；Lore 不把用户映射成开拓者；工作回答先给结论，工具与医疗边界自然且明确。候选更短，但没有退化为固定拒答模板。

## 候选身份与发布决定

- companionship compiled hash：`8b47a2a8377d45a443f2141eccfdc80613ac9a47aafe4ca37143af1e653d77f0`，1168 保守 tokens。
- focused_work compiled hash：`20d9244a220a35a65c9b21e476e08ba278eb8811b39f7292a4f60c0dc62a3d88`，1176 保守 tokens。
- 最终候选评测 artifact SHA-256：`b84493c78e08c5c8625941d02873c5db28f1aab356d5d537e1cafc1fa24051d2`。

程序已登记 `candidate_passed_pending_review`，故 `is_certified()` 仍返回 false，生产逐字回退旧 Persona。最终独立 Review 通过后才能把同一条记录改为 `certified`；任何模型指纹、compiled hash 或 temperature 变化都会使证书失效。WorldBook r1 来源门与本次 Persona 模型门相互独立，仍保持 Shadow。

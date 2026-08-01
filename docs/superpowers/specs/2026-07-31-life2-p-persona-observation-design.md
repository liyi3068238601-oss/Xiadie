# LIFE2-P Persona v2 真实体验观察期设计

- 日期：2026-07-31
- 状态：设计已获用户批准
- 范围：Persona v2.2 真实体验稳定期
- 当前分支：`agent/life-v2-specialty`
- 当前 Persona：`persona-profile-v2.2`
- 当前输出门：`persona-natural-dialogue-guard-v2`
- 当前发布状态：Persona Active；ShortMemo Shadow；InnerStateProjection Shadow；WorldBook r1 Shadow

## 1. 目标与阶段定位

LIFE2-P 的目标是验证 Persona v2.2 在日常真实聊天中的体验是否可接受，重点观察自然对话、陪伴边界、现实诚实、动作/心理旁白控制、闲聊邀请和工作模式兼容性。

本阶段是观察与问题驱动修复阶段，不是新的 Persona 设计阶段，也不是 LIFE v2 总冻结阶段。Persona v2.2 的已认证配置视为固定观察基线：认证 provider/model 指纹、temperature、静态 Prompt hash、compiled hash、输出门协议和 legacy fallback 语义均不主动调整。

ShortMemo、InnerStateProjection、WorldBook r1 不在本阶段晋级。不得因为 Persona 观察结果良好而自动切换其发布门。

## 2. 范围边界

本阶段允许做的事情是日常使用 Persona v2.2，并在发现明确问题后进行一个责任边界清晰、可独立回退的最小修复循环。

本阶段明确不做以下事情：不切换 ShortMemo 或 InnerStateProjection 的 Active 门；不推进 WorldBook 来源晋级；不新增数据库迁移；不修改冻结的 CTX、KIG、CIE、CDS 协议；不进行无关重构、UI 改版、依赖升级或发布工程收口；不为了形成统计而强制建立新的体验数据采集系统。

当前工作区已有的用户修改和未跟踪文件不属于本阶段自动处理范围。若后续确需修改，必须另行确认其归属并保持文件范围独立。

## 3. 观察方式与问题入口

观察方式采用纯人工日常体验。阶段不设置固定天数、固定轮数或自动评分阈值，也不要求用户填写体验账本。阶段结束条件只有用户明确确认 Persona v2.2 体验可以放行。

当用户发现问题时，保留处理该问题所需的最小、脱敏的复现信息，包括触发场景、实际表现、期望表现和问题严重程度。不得将完整 system prompt、API Key、Authorization Header、秘密值、隐藏推理或不必要的用户正文写入项目日志、报告或测试 fixture。

每个问题先判断责任边界，再决定是否修改：

- Persona 语气、人格连续性、关系分寸和自然表达问题，归 Persona。
- 错误历史或知识召回，归 CTX/KIG/Knowledge；不得通过 Persona 文本掩盖召回错误。
- 虚构天气、光线、时间、地点、周围环境或即时活动，归事实边界、召回链或 Persona 输出门，按实际根因修复。
- 括号、星号、动作、心理旁白和流式展示问题，归输出守卫或客户端展示层，不能用扩大硬过滤范围的方式破坏代码、数学、引用或明确角色扮演。

如果没有稳定复现或影响轻微，优先继续观察，不为单次措辞偏好过拟合提示词。

## 4. 严重程度与处置

涉及隐私泄漏、危险误导、明显越过关系或事实边界、虚假工具/记忆声明、权限扩大或持续不可用的问题，视为阻断性问题。应立即将 `life.persona_v2.rollout_mode` 回退为 `off`，使下一轮恢复冻结旧 `PERSONA_PROMPT`；当前已开始的请求继续使用其已绑定快照，不在请求中途切换。

稳定复现且会明显破坏角色或任务质量的问题，可以进入独立最小修复。修复必须保持认证边界、旧 Persona fallback 和主聊天可用性，并补充针对该回归的最小测试。

轻微、偶发或纯偏好的措辞问题先不改代码，继续观察；只有在重复出现并且影响体验时，才进入修复评估。

任一 Persona 相关修复都不得阻塞基础聊天、Memory、LIFE v1、EAP、KIG 或 CIE。修复无法安全加载、资源 hash 不匹配、协议不匹配或预算超限时，必须 fail closed 回退旧 Persona。

## 5. 修复循环

发生需要处理的问题时，一次只处理一个主题。修复循环包括：先记录脱敏复现与期望；确认责任模块和是否属于本阶段；评估是回退、继续观察还是代码修复；若修复则做最小改动并增加回归测试；运行与改动直接相关的后端测试、Persona 相关测试、必要的前端测试、类型/生产构建或 Electron 检查；执行 `git diff --check`；记录真实命令和结果；再恢复人工体验。

如果修复触及公共聊天装配、冻结协议、数据库、权限、模型认证或输出协议，必须暂停并重新确认范围，不得以 Persona 小修复名义静默扩大阶段。

## 6. 验证与证据

没有代码变化时，不为制造进展而运行测试或修改项目文件；人工观察结果由用户直接判断。

发生代码变化时，至少运行与变更直接相关的测试、新增回归测试、静态/类型检查或生产构建，以及 `git diff --check`。如变更触及公共主链，再按项目规则补跑后端完整测试和必要的前端/Electron 验证。未运行的测试必须明确写为“未运行”，历史报告不能冒充当前工作树结果。

Persona 体验阶段不重新认证模型，不改变已认证配置。若实际体验要求修改 profile、compiled hash、guard 协议、模型绑定或预算门，则应先停止观察期，单独提出新的 Persona 版本和认证设计。

## 7. 退出条件与下一阶段

唯一退出条件是用户明确确认 Persona v2.2 的实际体验可以放行。用户未明确放行前，不开始 ShortMemo Active 的切换或实施。

用户放行后，本阶段停止，不自动开始下一阶段。随后另开一个独立的 ShortMemo Active 设计与发布决定，至少重新审查 Shadow 观察证据、静默创建准确性、秘密值零写入、敏感最小化、TTL/过期、来源校验、删除/清空/导出、远端复核授权、请求边界切换、关闭和回退语义。ShortMemo 运行并经过用户 Review 后，才另行决定 InnerStateProjection 是否从 Shadow 切换到 Active。

InnerStateProjection 的 Active 切换不属于 ShortMemo 发布的自动后置动作，必须保持独立发布门、独立 Review 和独立回滚。

## 8. 回退方式

Persona 回退：将 `life.persona_v2.rollout_mode` 设置为 `off`。下一轮使用冻结旧 `PERSONA_PROMPT`；不删除 Persona v2 资源、证书或数据库数据。

ShortMemo 和 InnerStateProjection 在本阶段保持现有 Shadow，不做任何切换，因此没有本阶段新增的数据回滚或迁移回滚。

如果后续修复无法验证、产生跨域副作用或影响基础聊天，应保留修复前版本并回退 Persona 发布门，不执行破坏性 Git 操作，不覆盖用户已有工作区修改。

## 9. 设计决策摘要

本阶段采用“固定 Persona 基线、纯人工体验、问题触发最小修复、用户明确放行”的顺序。它把 Persona 真实体验与 ShortMemo/Projection 晋级解耦，确保每个 Active 门都能单独验证、单独回退、单独承担 Review 责任。

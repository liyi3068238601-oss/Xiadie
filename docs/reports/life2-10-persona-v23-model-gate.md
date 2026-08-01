# LIFE2.10 Persona v2.3 模型门、认证与发布报告

- 日期：2026-08-01
- 分支：`agent/life-v2-specialty`
- 施工基线：`0daf7ac`
- 结果提交：本报告所在提交
- Schema：82（无迁移）
- 状态：施工完成，等待用户 Review；LIFE2.11 尚未施工

## 1. 结论

Persona v2.3 已通过固定集、真实 DeepSeek 三轮评测、v2.2 同集对照、资源与模型指纹认证，并成为当前数据库和全新安装的默认 profile。生产组合为 Persona v2.3 Active、ShortMemo Active、InnerStateProjection Active；WorldBook r1 仍为 Off。

v2.2 的资源、历史证书和运行时回退路径未删除。v2.3 资源、hash、预算或模型证书失配时，Active 路径仍依次回退 v2.2 和 legacy Prompt。

## 2. LIFE2.9 Review 处理

采纳：

- 请求开始时单独捕获 profile selector，并显式传给本轮 Persona 编译；请求中途切换只影响下一轮。
- `core.md` 恢复“一次浓烈对话不能越级”，移除未经来源支持的摄影爱好，保留小说、诗歌等文学艺术与花朵。
- `focused_work.md` 恢复“不得用死亡权能、入殓经历或角色比喻替代现实建议”。
- 修正两份候选资源的 manifest hash，并重新计算 compiled hash 与 token 预算。

不采纳或延期：

- `styles.json` 本阶段不改；Review 已确认其未变化符合版本路由边界。
- `expression_flags` 暂不翻译或升级协议；当前真实评测未显示其导致阻断问题，修改会同时触及已发布 Projection 合同。
- Review 对具体作品名来源等级的 P2 观察不在本阶段扩大处理；本次已通过移除摄影爱好消除相关歧义，后续 Lore 来源审计另行处理。

## 3. 固定集与评分器

保留既有 `persona-evaluation-v1.4` 的 150 例与 fixture hash，不修改历史报告；新增确定性 `persona-evaluation-v2.0`，总计 250 例。新增 100 例覆盖：

- 现代科技、互联网与无关 Lore 的正常回答；
- 摄影、游戏等审美/知识与亲身经历边界；
- 当前价格与实时证据边界；
- 遐蝶、Xiadie、底层模型的技术结构；
- Chat/Work 任务正确性与高风险角色边界。

评测器对否定、引用和条件句采用语境感知，避免把“不能只和我说话”“不能用死亡权能判断”误判为实施相应行为。现代互联网允许语义等价答案，不要求机械复述单一关键词。Work 代码除 `def` 外新增 Python AST 可解析硬门。

固定集：

```text
protocol       persona-evaluation-v2.0
case_count     250
fixture_sha256 22ce05dee3ee425783f30346645fb160aaeff4216fefa6b49a5f019dad7d8dcd
```

## 4. 真实模型与产物

两组均使用同一配置：

```text
provider_id        deepseek
base_url           https://api.deepseek.com/v1
execution_location remote
model              deepseek-v4-flash
model_fingerprint  b2bcda1f94e8d4c89a84f7e80a99ec5bf8271246496ca10bb34fe2edde2c2040
temperature        0.0
max_tokens         4000
runs               3
```

候选产物：`docs/reports/life2-persona-v2.3-candidate-deepseek-v4-flash.json`

```text
run 1  250/250
run 2  250/250
run 3  250/250
合计   750/750
artifact_sha256 3023a3e3b43d29da73e271c15c23392021f08b6cf0e63a1f8cc543b94f4ac950
```

v2.2 同集基线：`docs/reports/life2-persona-v2.2-v23-suite-deepseek-v4-flash.json`

```text
run 1  248/250（2 次技术身份回答未说明 Xiadie）
run 2  249/250（1 次技术身份回答未说明 Xiadie）
run 3  250/250
合计   747/750
artifact_sha256 568b7c15211a52ca87fa80f10f216203a09ca36a67727b248d2515bcc59c9eeb
```

v2.3 的 Work 硬门为 210/210，没有低于 v2.2。日常闲聊类别没有列表化结构；结构增长集中在 eSIM、蓝牙、技术身份和纠错等解释型问题。v2.3 平均 197.3 字，v2.2 平均 159.9 字；overlong 为 10/750 对 8/750，进入 LIFE2.11 观察而不作为本阶段阻断项。

## 5. 输出门阻断修复

模型门收口时发现旧输出门会删除所有行首空白，使模型生成的 Python 代码块失去缩进。该问题不是 Persona 内容缺陷，但会破坏最终用户可见的 Work 答案，因此在发布前修复：

- 动作/心理旁白清洗继续生效；
- fenced 与 unfenced 代码缩进逐字保留；
- 固定集对 Work Python 代码执行 AST 解析；
- 两份真实模型原始产物使用同一确定性输出门重新评分，没有再次调用模型或手工替换单条答案。

v2.3 最终 750/750；输出门实际改写 35/750，均为动作旁白或既有无依据环境清洗，不再因代码缩进触发。

## 6. 资源、证书与发布状态

最终 v2.3 静态编译证据：

```text
companionship 6a3d71745a600e89ff6779a351ddb33b9f59d78b77b585e00a6b08cc2c512aa1  1404 tokens
focused_work  4b0b91fbd73ab8692a0f0fd8e095868cf5630d92a5c920cf7c256921aefbdc27  1357 tokens
```

测试 Projection 后分别为 1422 和 1375 tokens，均低于 1450 硬门。证书同时绑定模型指纹、profile、编译器、两种 compiled hash、temperature、4000-token 评测上限、输出门、评测协议、fixture 与最终 artifact hash。

实际发布操作：

```text
life.persona_v2.rollout_mode=active
life.persona_v2.profile_version=persona-profile-v2.3
life.short_memo.rollout_mode=active
life.short_memo.rollout_epoch=1
life.inner_state_projection.rollout_mode=active
life.worldbook_r1.rollout_mode=off
schema=82
```

设置缺失时，全新安装默认选择 v2.3；未知 selector 或读取异常仍 fail closed 到 v2.2。当前实际 DeepSeek 对两种模式均显示 `certified=True`、无 fallback。

## 7. 验证

```text
模型评测：v2.3 750/750；v2.2 同集 747/750
Persona/Projection/发布定向：21 passed, 1 warning
输出门与评测器定向：21 passed
后端首次全量：2675 passed, 4 failed, 1 warning
  原因：四个历史 v2.2 测试仍把当前默认 profile 视为 v2.2
修正后相关定向：21 passed, 1 warning
后端第二次全量：2679 passed, 1 warning
输出门最终修复后全量：2682 passed, 1 warning in 593.03s
前端全量：79 passed
前端生产构建：通过；保留既有 Live2D 非 module 警告
Electron 生命周期合同：3 passed
```

最终门以输出门修复后的 2682/2682 为准。首次与第二次记录保留用于说明过期测试修复过程，不作为最终通过数。

## 8. 回滚

只调用内部 selector：

```python
persona_v2.set_profile_version("persona-profile-v2.2")
```

回滚不删除 v2.3 资源、证书、ShortMemo、Projection 或用户数据。验证应检查 `selected_profile_version()` 为 v2.2，并用当前 provider/model 编译两种模式确认命中 v2.2 证书；无需改变另外两项 LIFE rollout。

## 9. Review 重点

1. 复核 250 例新增分类是否真正覆盖现代知识、技术身份、个人经历和高风险角色边界，而非只靠关键词凑通过。
2. 抽查 v2.3 三轮失败修正记录，确认都是否定/引用/语义等价误判，没有放过真实越界。
3. 抽查 Work 代码样本，确认最终 `output` 保留缩进且 AST 可解析。
4. 核对证书与 artifact、fixture、compiled hash、模型指纹完全一致。
5. 实际聊天切换 Chat/Work，确认当前显示 v2.3；同时验证内部 selector 回滚 v2.2 不影响 ShortMemo/Projection。
6. 观察解释型回答是否偏长；日常安慰、轻聊和关系对话是否仍自然。这是 LIFE2.11 的主要体验观察项。

本阶段已完成本地施工，等待 Review。Review 通过前不进入 LIFE2.11，不自动推送、合并或删除分支。

# ADR-0072：Persona Profile 版本路由与可运行回退链

- 状态：Accepted（LIFE2.9 Review 通过，LIFE2.10 已认证发布）
- 日期：2026-08-01
- Schema：82（无迁移）

## 背景

Persona v2.2 已由真实模型证书绑定资源 hash、两种模式的 compiled hash、模型指纹、采样参数和输出门。若在原目录覆盖文件以制作 v2.3，v2.2 将只剩历史报告而无法作为运行时回退。Persona rollout 单独控制是否使用版本化 Persona，不能替代 profile 版本选择。

## 决策

采用一个编译器、多个不可变 profile 目录：

- `persona_profiles/v2_2/` 保存已认证 v2.2 原始资源和证书；
- `persona_profiles/v2_3/` 保存候选资源、manifest 与本版本自己的证书文件；
- `life.persona_v2.profile_version` 是内部 selector，允许值仅为已安装白名单；不开放普通 API/UI；
- 设置缺失时新安装选择已发布 v2.3；未知值或读取异常仍 fail closed 选择 v2.2；
- `compile_candidate(profile_version=...)` 使用同一个 `persona-prompt-compiler-v1`，不复制编译算法；
- Active 请求依次尝试 selector 指定 profile、已认证 v2.2、legacy `PERSONA_PROMPT`；只有资源完整、hash/预算有效且证书匹配实际模型的 profile 才能成为生产 prompt；
- Shadow/Off 只编译请求的候选用于比较，不改变生产 prompt；
- v2.3 已在 LIFE2.10 通过真实模型门并签发独立证书；当前数据库 selector 为 v2.3。

## 后果

v2.3 的内容、评测和发布可以独立失败，不会销毁 v2.2。版本目录会增加少量重复资源，但换来可审计 hash、逐版本证书和确定性回滚。Profile 切换不修改 ShortMemo、Projection、WorldBook、数据库 Schema 或聊天协议。

Observer 摘要允许显式从单一 profile Core 确定性派生，不能混合两个版本。发布默认由 v2.3 Core 派生；显式 v2.2 回滚仍只能使用 v2.2 Core。

## 未采用方案

- 原地覆盖 `v2/`：会破坏可运行 v2.2 回退和证书证据，拒绝。
- 为每个版本复制编译器：容易产生规则、预算与安全验证漂移，拒绝。
- 未认证 v2.3 自动继承 v2.2 证书：compiled hash 与内容不同，违反模型证书绑定，拒绝。
- 用数据库保存任意 Persona 文本：扩大 Prompt 注入面且不利于代码 Review，拒绝。

## 回滚

内部 selector 设为 `persona-profile-v2.2`。若 v2.2 本身也无法验证，现有编译路径自动使用 legacy Prompt；回滚不删除资源、证书或用户数据。

# LIFE v2：遐蝶 WorldBook 来源审计与补充清单

- 版本：v0.1 research backlog
- 日期：2026-07-29
- 状态：只登记来源与缺口，不是 canonical 正文，不授权运行时施工
- 本地素材：`E:\Xiadie\人格.txt`、`E:\Cyrene agent\设定`
- 上位计划：`docs/LIFE_V2_PERSONA_AND_SHORT_MEMORY_PLAN.md`

## 1. 来源等级

| 等级 | 来源 | 用法 |
|---|---|---|
| A | 游戏内角色故事、语音、短信、任务、物品文本；官方角色介绍、PV、动画短片与官方文章 | 可在人工核对版本与上下文后写入 canonical |
| B | 可追溯到游戏条目的结构化资料库、Wiki 转录 | 用于发现条目和交叉核对，不单独作为最终 canonical 依据 |
| C | 攻略、论坛讨论、同人整理、剧情推测 | 仅作为搜索线索，不写入 canonical |

任何联网正文都先进入本清单。只有确认来源、版本、上下文和中文名称后，才允许人工改写为简短 Lore 摘要；不得把网页全文、攻略结论或未经核验的推测直接复制进 WorldBook。

### 1.1 已登记的联网来源

| 来源标识 | 等级 | 版本/核验日期 | 用途与限制 |
|---|---|---|---|
| [`official-golden-heirs-wiki-castorice-3.7`](https://act.mihoyo.com/sr/event/gt-aio/chrysos-heirs/index.html#/) | B | 黄金裔 WIKI `3.7`；2026-07-29 | 米哈游官方活动站的结构化黄金裔档案；用于发现并交叉核对角色故事、逐火事件、人物关系，以及十一组“泰坦正式称号—火种体系”对应。站内“人物解析”含作者署名，仍按 B 级转录处理，不替代游戏内任务原文。刻法勒能核到「负世」体系，但角色档案没有与其余十一位同格式的称号字段。 |

用户于 2026-07-29 确认刻法勒的对应称号为「全世之座」，登记为 `user-titan-title-confirmation-2026-07-29`。该确认允许进入 r1 内容草案，但不能伪装成上述官方接口直接返回的字段；后续仍需用游戏内或官方原始文本补齐 A 级证据。

## 2. 已确认的资料结构

Cyrene 的提取配置把 WorldBook 分成自身条目、人物、故事、世界与 glossary，并为普通条目提供触发词、常驻、优先级、内在价值和连带触发字段。这证明人物关系、人物经历和生活细节适合按需条目化。

本项目只采用稳定 ID、别名、优先级、单层关联和来源 revision/hash。首版不采用常驻大段正文、动态内在价值、模型提及奖励或“用户 = 原作主角”的 glossary 映射。

## 3. 当前材料可能缺失的内容

### 3.1 官方角色档案与角色故事

- 角色故事 Part I～IV 的逐项核验。
- 正式称号、阵营、地点与亲属关系的版本差异。
- 死亡之触、成长阶段、职责和诗歌创作相关的原始上下文。

### 3.2 角色语音与组队语音

- 自我介绍、问候、告别、爱好、烦恼、对生命与死亡的态度。
- 对阿格莱雅、缇宝三人、万敌、白厄、那刻夏、风堇和赛飞儿的既有语音。
- 后续版本新增的海瑟音、刻律德菈、三月七、丹恒·腾荒和昔涟相关语音及组队语音。
- 语音只提炼关系性质与措辞特征；不把长篇台词放入常驻 Prompt。

### 3.3 短信、访客与任务对话

- 可体现日常习惯、幽默、社交笨拙和主动关心方式的短信分支。
- 不同玩家选项导致的回复差异，用于行为评测而非同时写成互相冲突的事实。
- 主线后续版本对人物结局和关系的修订。

### 3.4 官方宣传材料与物品文本

- 遐蝶角色 PV《墓志铭》及相关官方动画短片。
- 官方“走近星穹”遐蝶节目中关于柔软物品、无法随意接触动物等生活细节。
- 角色光锥、晋阶材料、专属物品、成就与加载页文本中真正属于设定的内容；纯玩法数值不进入 WorldBook。

## 4. 已发现的更新风险

- 当前 `人格.txt` 主要覆盖早期主线和 3.2 左右资料，不能视为完整终稿。
- 当前时间线已不能继续写作“本体守候冥界”：用户确认且现有剧情资料交叉支持遐蝶现存在于《如我所书》中。Persona Core 只写当前地点；冥界归宿与职责作为此前经历进入 `events`，不得混写成同时存在的两个当前地点。
- 《如我所书》不是普通纸质居所，而是承载黄金裔生平与记忆延续的剧情载体；Core 不展开其机制，相关结局、传播与翁法罗斯实体化过程进入 `world/events` 并继续核验游戏内终章原文。
- 资料库的版本记录显示后续版本新增多组遐蝶人物语音，当前 `xiadie_lore.md` 尚未逐项覆盖。
- 人名、称号和地点存在简中、繁中、英文与剧情别名，需要独立 glossary；别名归一化不能推导人物同一性之外的关系。
- 社区页面可能混入推测、翻译差异或后期剧情剧透，必须回到 A 级来源核验。

## 5. WorldBook r1 条目清单

```text
self/
  castorice_death_touch
  castorice_titles_and_roles
daily_life/
  castorice_handcrafts_and_plushies
  castorice_home_and_collections
  castorice_novels_poetry_photography
  castorice_social_habits
events/
  castorice_origin_and_polyxia
  castorice_childhood_in_aidonia
  castorice_second_life_in_okhema
  castorice_flame_chase_journey
  castorice_inherits_death
  castorice_current_existence
characters/
  polyxia_relationship
  amunet_relationship
  aglaea_relationship
  tribios_relationship
  anaxa_relationship
  trailblazer_canonical_relationship
  mydei_relationship
  phainon_relationship
  hyacine_relationship
  cipher_relationship
  cyrene_relationship
world/
  amphoreus_and_chrysos_heirs
  aidonia_world
  okhema_world
  styxia_underworld_and_styx
  titans_coreflames_and_flame_chase
  black_tide
  as_ive_written_world
glossary/
  canonical names, constrained aliases and reverse relationship nicknames only
```

共 30 个正文条目。Glossary 是归一化表，不计为独立正文条目，也不单独注入。

## 6. 下一次资料施工的验收

- 每条新增事实都有来源等级、来源标识、版本、核验日期与 revision/hash。
- A 级事实与 B 级转录冲突时以 A 级原文为准，并记录差异，不静默合并。
- C 级内容为零条 canonical 写入。
- 没有“用户 = 开拓者/主人/恋人”的 glossary 或默认关系。
- 生活细节不会被表述为本轮正在发生的实时活动。
- 人物与事件关联最多扩展一层，仍受最多 3 节/3600 字符兼容上界和 CTX token 预算限制。

## 7. r1 来源冻结审计（等待独立 Review）

### 7.1 冻结结果

| 状态 | 条目数 | 含义 |
|---|---:|---|
| `verified_a` | 0 | 尚未逐项绑定游戏内原文或可复核的官方原始段落，不得虚报为 A 级冻结 |
| `candidate_b` | 27 | 已由黄金裔 WIKI 3.7 结构化档案、仓库现有 Lore 或其他可追溯资料交叉支持，正文已通过用户内容 Review，但仍待 A 级原文核验 |
| `local_candidate` | 3 | `castorice_home_and_collections`、`castorice_novels_poetry_photography`、`castorice_social_habits` 含仅由本地人格稿或当前仓库 Lore 支持的细节，不能随其他条目一起晋级 |

“用户确认内容合适”只冻结产品表达与条目粒度，不等于事实来源升级。独立 Review 前所有条目的 `revision` 继续保持 `pending`；通过后再统一生成稳定 revision 与正文 hash，避免对仍可能修改的草稿制造伪稳定标识。

### 7.2 A 级补证顺序

1. 当前时间线与《如我所书》：优先核验终章任务原文，防止把此前冥界职责写成当前所在地。
2. 起源、玻吕茜亚、死亡权能与复活开拓者：核验主线任务、角色故事与正式短片的具体因果。
3. 逐火关键事件及十二泰坦：逐项绑定任务/角色档案原文；刻法勒的「全世之座」当前按用户确认保留，仍待补齐 A 级原文证据。
4. 人物关系、称呼与共同经历：优先角色语音、短信、任务对话；不以人物解析作者总结替代原文。
5. 三个 `local_candidate` 生活条目：分别核验角色故事、官方节目、短信或物品文本；无法补证的句子在 canonical 施工前删除，而不是整体降格后静默上线。

2026-07-29 补证记录：官方黄金裔 WIKI 3.7 `all_textmap` 对“公主变成巨龙”“公主变成龙”“童话”均无匹配，联网精确检索也未找到可追溯 A/B 级来源。该具体作品名已从 r1 的 `castorice_novels_poetry_photography` 正文和触发词删除，保留不依赖作品名的小说、诗歌与童话偏好候选；若后续发现官方原文，可按新 revision 恢复。

### 7.3 冲突与升级规则

- 一个条目含有不同等级事实时，整体按最低已覆盖等级处理；不能用其中一句 B 级事实掩盖另一句仅本地候选的内容。
- A 级原文与当前 r1 冲突时，修改正文并递增 revision；不为维持已确认措辞而忽略原文。
- B 级和本地候选可以进入评测候选，不得在未标识实验状态时替换生产 canonical。
- 来源缺失、版本不符或 hash 不符时 fail closed：不注入该条，不回退到旧的无来源正文。

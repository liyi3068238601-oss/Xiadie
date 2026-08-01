# 统一运行日志首版设计

- 日期：2026-08-01
- 状态：设计已获用户批准，待书面复核
- 当前分支：`agent/life-v2-specialty`
- 范围：已有运行事件的本地只读统一审计视图
- LIFE v2 边界：Persona Active；ShortMemo Shadow；InnerStateProjection Shadow；WorldBook r1 Shadow

## 1. 目标与阶段定位

本功能将当前分散在聊天消息、后台模型任务、DecisionRun、知识召回、ContextPackage、工具日志和决策状态事件中的已有记录，聚合为一个本地统一运行日志页面。首版服务于人工检查实际运行情况，尤其是查看 CIE 连续输入最终如何归成一轮，以及该轮持久化后的最终回复。

本功能不是新的遥测系统，不新增统一日志表，不复制历史事件，不埋点聊天流，不改变任何生成行为。它只读取现有权威表，并在请求时构造临时展示对象。

## 2. 已确认的范围

首版覆盖六类已有事件源：

1. `model`：持久化的助手回复，以及已有记忆观察、情绪观察、会话摘要后台模型任务。
2. `reasoning`：已有 `decision_runs` 中可审计的结构化动作、理由码、置信度、模型和耗时元数据。
3. `retrieval`：已有知识召回决策元数据。
4. `context`：已有 ContextPackage 预算、来源计数和裁剪元数据。
5. `tool`：已有工具日志，包括现有短摘要。
6. `system`：已有决策状态迁移事件。

首版不专门展示 LIFE v2 rollout、Persona profile/compiler、ShortMemo 或 InnerStateProjection 元数据，不增加 CIE chunk、首 token、取消瞬间或展示边界埋点。

## 3. 不可越界边界

实现必须满足以下约束：

- 不新增或修改数据库 Schema，不占用迁移号 83。
- 不新增后台 worker、定时任务或事件写入。
- 不修改 `/api/chat` 请求、SSE 响应或持久化协议。
- 不修改 Persona profile、compiler、证书、输出守卫或 fallback。
- 不修改 ShortMemo 提取、写入、TTL、容量、召回、开关或发布门。
- 不修改 InnerStateProjection 构建、装配或发布门。
- 不修改 CIE、CTX、KIG、Knowledge、Memory、Affect、Relationship、Provider 或模型调用路径。
- 不展示系统提示词、隐藏思维链、API Key、Authorization Header、知识正文、记忆正文或模型原始内部输出。
- 不提供导出、二次持久化、日志清除或全库正文搜索。

当前 LIFE2-P Persona 真实体验观察基线必须保持不变。运行日志改动不得改变 Persona profile/compiler hash、模型指纹、temperature、ShortMemo rollout 或 InnerStateProjection rollout。

## 4. 架构

### 4.1 后端聚合层

`backend/app/runtime_logs.py` 是统一运行日志唯一聚合层。它只建立短生命周期数据库连接，读取已有表，将不同来源映射为统一事件结构，然后关闭连接。

统一列表事件结构：

```json
{
  "id": "chat:<assistant_message_id>",
  "source": "chat",
  "category": "model",
  "title": "对话模型回复",
  "summary": "<有界输入输出预览>",
  "status": "completed",
  "status_group": "success",
  "created_at": 0,
  "details": {
    "model": "...",
    "session_id": "...",
    "message_id": "...",
    "input_count": 1
  },
  "detail_available": true
}
```

事件 ID 必须由受控 source 和原始记录 ID 组成。详情查找不得把 event ID 拼接进 SQL；解析 source 后使用参数化查询。

### 4.2 列表接口

保留：

```text
GET /api/runtime-logs?category=&status=&limit=
```

行为：

- category 只接受六类白名单枚举。
- status 只接受 `success/warning/error/pending`。
- limit 限制为 1～500，默认 200。
- 返回统一排序后的当前结果、当前查询窗口的分类计数、当前返回条数和隐私说明。
- `total` 明确表示本次返回条数，不表示数据库历史总量。
- 聊天列表项只返回严格截断、规范换行的输入输出预览，不返回完整正文。
- 搜索由前端在列表已返回字段中完成，不触发全库正文扫描。

各来源读取有界候选，统一按 `(created_at, id)` 倒序稳定排序，再应用筛选和最终 limit。分类计数必须使用定义明确的同一查询窗口，不能把“每个来源各取 N 条”的总和伪称为“最近 N 条”。

### 4.3 详情接口

新增：

```text
GET /api/runtime-logs/{event_id}
```

首版仅聊天事件提供正文详情；非聊天事件继续使用列表事件中的结构化 `details`，不借详情接口读取额外正文。

聊天详情结构：

```json
{
  "id": "chat:<assistant_message_id>",
  "source": "chat",
  "session_id": "...",
  "assistant": {
    "message_id": "...",
    "content": "<最终持久化回复>",
    "model": "...",
    "created_at": 0
  },
  "inputs": [
    {
      "message_id": "...",
      "content": "<持久化用户输入>",
      "created_at": 0
    }
  ],
  "representation": "persisted-turn-final-v1"
}
```

### 4.4 CIE 轮次归组

对某条目标 assistant 消息，本轮输入定义为：同一会话内，上一条 assistant 消息之后、目标 assistant 消息之前的连续 user 消息集合。

- 输入按 `(created_at, id)` 正序稳定排列。
- 单条普通输入产生一个 input。
- CIE 积累窗口内连续持久化的多条 user 消息产生多个 input，并保持原顺序。
- 上一轮 user 消息不得串入当前轮。
- 主动陪伴等没有前置 user 的 assistant 消息允许 `inputs=[]`。
- 不根据文本、时间差或模型输出猜测不存在的轮次关系。
- 详情代表持久化输入和最终回复，不代表逐 chunk 回放。

## 5. 前端交互

### 5.1 列表

页面保留六类分类筛选、四类状态筛选、列表文本搜索和分类数量。列表搜索只检查标题、截断预览、状态和已返回元数据。

聊天事件折叠状态显示：

- 输入与输出的短预览；
- 模型、状态和时间；
- 用户输入数量；
- 可展开标记。

其他来源沿用结构化元数据展开视图。

### 5.2 按需详情

点击聊天事件后才请求详情：

- 首次显示“正在加载本轮详情”。
- 成功后分为“本轮输入”和“最终回复”。
- 多条用户输入逐条显示消息 ID、时间和正文。
- 正文使用 React 默认纯文本转义，不使用 `dangerouslySetInnerHTML`，不执行正文内 HTML。
- 可复制单条用户输入或最终回复；首版不做批量导出。
- 详情缓存在当前页面组件内存中，重复展开不再次查询。
- 离开页面后缓存自然丢弃，不写 localStorage 或数据库。
- 404 显示“原始对话已删除或不可用”。
- 详情失败只影响当前事件，不清空已有列表。

### 5.3 刷新

自动刷新默认关闭。用户主动开启后，每 5 秒刷新列表；刷新列表不主动刷新已展开正文详情。手动刷新始终可用。

列表刷新失败时保留上一次成功结果，并显示非阻断提示。首次加载失败时显示完整错误状态。

### 5.4 页面披露

页面顶部明确说明：

- 本页会展示本地保存的用户输入和助手最终回复；
- 不展示系统提示词、隐藏思维链、密钥、知识正文、记忆正文或模型原始内部输出；
- 不建立独立日志副本，删除原会话后正文不可恢复；
- 聊天详情不是逐 chunk 回放，不能单独证明首 token、展示节奏或取消瞬间行为。

## 6. 错误处理与兼容

- 未知 category/status 返回 400 和稳定错误码。
- 非整数 limit 由 FastAPI 参数校验拒绝；整数越界由聚合层限制到 1～500。
- 未知、格式错误或已删除的 event ID 返回 404，不暴露 SQL 和文件路径。
- 历史数据库缺少某个可选事件表时，仅该来源降级为空，其他来源继续返回。
- `sqlite3.OperationalError` 只在明确的 `no such table` 兼容场景下被降级；列名错误、SQL 语法错误或其他数据库故障必须继续抛出，不能静默伪装为空数据。
- API 不返回数据库异常文本、SQL、绝对路径或堆栈。
- 工具短摘要作为已有本地审计内容继续显示，但统一日志只使用字段白名单，不做 `SELECT *` 或整行透传。工具摘要写入方继续承担不得记录密钥和不必要敏感正文的既有边界。

## 7. 性能设计

- 列表读取每个来源的有界候选，不扫描无关历史正文。
- 聊天列表为生成预览只读取有限候选消息文本；完整正文只在单事件详情请求中读取。
- 详情使用单个 assistant message ID 和已有 `idx_messages_session(session_id, created_at)` 完成有界查询。
- 不新增索引，不修改 Schema。
- 默认不轮询；开启后列表轮询间隔固定为 5 秒。
- 性能测试应证明查询次数和读取范围不随数据库总历史消息量线性增长。

## 8. 测试设计

### 8.1 后端专项测试

至少覆盖：

1. 六类事件统一映射、稳定排序和状态分组。
2. category/status/limit 的合法与非法输入。
3. 聊天列表只有截断预览，没有完整长正文。
4. 单 user → assistant 的轮次详情。
5. 连续多条 user → assistant 的 CIE 轮次详情和顺序。
6. 相邻多轮输入不串轮。
7. 无前置 user 的主动 assistant 允许空 inputs。
8. 相同时间戳使用 ID 稳定排序。
9. 会话删除后详情返回 404。
10. 缺少可选历史表只降级该来源，其他 OperationalError 不被吞掉。
11. 响应不包含 Provider API Key、系统提示词、知识正文、记忆正文或隐藏推理字段。
12. 列表和详情 GET 前后相关表行数与状态不变。
13. 大历史库下列表与单详情保持有界查询和读取。

### 8.2 前端测试

至少覆盖：

- 列表和详情 API URL、query 和 event ID 编码契约；
- 自动刷新初始状态为关闭；
- 只有点击聊天事件才请求详情；
- 详情加载、成功、404 和一般失败状态；
- 多输入顺序和最终回复展示；
- 正文使用安全纯文本渲染；
- 分类、状态、搜索和当前页面详情缓存；
- 列表刷新失败保留旧结果；
- 隐私披露和“非逐 chunk 回放”说明。

若项目当前没有 React DOM 测试环境，首版不为此引入大型依赖。保留并加强现有 `node:test` 契约测试，将真实交互纳入浏览器或 Electron smoke。

### 8.3 回归与人工验收

实施完成后运行：

- 后端统一运行日志专项测试；
- 前端现有全量测试；
- 前端 TypeScript/生产构建；
- Electron contract；
- 后端全量 pytest；
- `git diff --check`；
- 真实应用 smoke：普通单输入、多条连续输入、最终回复、分类/状态/搜索、手动刷新、主动开启刷新、会话删除后的详情失效。

验证前后比较 Persona profile/compiler hash、ShortMemo rollout 和 InnerStateProjection rollout，确认没有变化。最终 diff 不得包含迁移、Persona、ShortMemo、InnerStateProjection、CTX、CIE 生成路径或 Provider 调用路径变更。

## 9. 完成定义与能力限制

首版完成后，运行日志可以回答：

- 连续用户输入最终如何归成一轮；
- 持久化后的助手最终回复是什么；
- 同期存在哪些模型、结构化决策、检索、上下文与工具事件；
- 事件处于成功、注意、异常还是进行中状态。

首版不能单独回答：

- 首 token 的真实延迟；
- 每个 SSE chunk 的到达和展示节奏；
- protected boundary 的中间展示状态；
- 取消瞬间是否存在未持久化内容泄漏；
- 模型隐藏推理或系统提示词内容。

这些仍由 CIE 固定集、专项测试和真实 smoke 负责，不能因统一运行日志页面存在而宣称 CIE 全量指标已被运行时追踪。

## 10. 回退

本功能没有数据迁移和写入，因此回退只需移除统一运行日志 API、聚合器、前端类型、页面交互和对应测试。既有消息、DecisionRun、检索、ContextPackage 和工具日志不受影响。

如果实现期间发现必须修改聊天生成链、增加事件持久化或新增 Schema 才能满足需求，应暂停实施并重新设计，不得以统一运行日志名义扩大范围。

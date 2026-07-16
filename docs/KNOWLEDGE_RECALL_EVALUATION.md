# 知识库自然召回固定评测说明

这套评测用于回答三个问题：遐蝶什么时候应该想到资料、什么时候应该保持普通陪伴、想到资料后是否仍遵守远传边界。
K.0 只建立分类和虚构样本，不设定 dense 绝对阈值，也不让样本进入真实聊天逻辑。

## 文件位置

- 固定样本：`backend/tests/fixtures/knowledge_recall_evaluation_v1.json`
- 契约测试：`backend/tests/test_knowledge_recall_evaluation_fixture.py`

fixture 中的文档、人名、项目和对话全部是为测试编写的虚构内容，不来自用户文件、聊天记录或应用数据库。

## 必须覆盖的类别

| 类别 | 要证明什么 |
|---|---|
| `explicit_recall` | 用户明确要求查资料时应进入既有检索路径。 |
| `natural_recall` | 没说“查资料”，但问题明显依赖已导入知识。 |
| `skip` | 问候、情绪陪伴和简单任务不应触发。 |
| `lexical_strong` | 精确术语和编号应由 FTS 可靠命中。 |
| `vector_strong` | 不同措辞表达相同含义时评估 dense 收益。 |
| `duplicate_sources` | 多文档重复内容不能挤占全部预算。 |
| `memory_conflict` | 知识事实和相处记忆必须保留不同来源。 |
| `local_only` | 找到资料也不能向远程 Provider 发送。 |
| `prompt_injection` | 文档中的命令不能提升为系统指令。 |
| `source_changed` | 删除、重建或哈希变化后旧结果失效。 |
| `provider_changed` | Provider/model 变化后旧授权失效。 |
| `ambiguous_context` | 无明确实体的代词追问不能凭空检索。 |

## 后续使用规则

1. K.2 只在 shadow 模式记录 action、reason code、分数分桶、数量和耗时，不保存样本以外的查询正文。
2. K.3 才运行 FTS、dense 和融合对比，并根据结果定义 low/medium/high；fixture 不预设拍脑袋分数。
3. 新协议版本需要保留旧结果并输出差异，不能为了让测试通过随意改 expected。
4. 真实隐私数据不得加入 Git。需要人工抽样时只记录匿名结论，不复制原消息或文档正文。


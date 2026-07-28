# ADR-0062：KIG 是治理与投影层，不是大一统正文数据库

- 状态：Accepted for KIG construction
- 日期：2026-07-27
- 关联：KIG.0、`source-ref-v1`、现有 Knowledge/MEM/LIFE/CTX

## 决策

1. 原始聊天、知识文件、记忆、LIFE、Task/ToolRun 与 Lore 正文继续由各自权威系统保存；KIG 不复制成第二套通用正文库。
2. KIG 只保存 typed SourceRef 信封、revision/hash/status/locator/privacy 快照、派生依赖、EvidenceLink、版本/新鲜度关系和可重建 PWM 投影。
3. SourceAdapterRegistry 在读取时向权威所有者验证 exists、revision、hash、privacy、locator 与 deletion；多态来源不伪装成数据库外键完整性。
4. 来源缺失、变化、撤销或不可访问时，KIG 将派生对象标为 missing/stale/revoked/inaccessible，不能继续作为有效证据，也不反向修改权威来源。
5. KnowledgeDocument、Chunk、导入、解析、索引、引用、授权和删除主链保持现状；结构或索引增强必须旁路构建、对照验证后原子切换。
6. KIG 派生层可删除并从当前权威来源重建；恢复顺序始终是权威来源在前、KIG 在后。

## 失败与回滚

任一 adapter 不可用时只降级对应来源，普通聊天和其他来源继续工作。回滚 KIG 不删除或改写聊天、文件、记忆、LIFE、Task、ToolRun 或 Lore。

## 明确拒绝

- 不新建第二套 KnowledgeDocument/Chunk/Search/Citation。
- 不把所有既有来源预迁移成平行通用来源行。
- 不让 KIG 成为 MEM、LIFE、CTX、EAP 或 ToolRegistry 的第二写入者。

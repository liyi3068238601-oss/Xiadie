# KIG.12 Owner 治理接线

- Schema 79 的 `kig_system_proposals` 覆盖 MemoryClassification、MemoryConflict、EpisodeBoundary、SagaTransition 与 memory alias sync。
- 接受/拒绝仅记录 owner 决定，不执行 MEM/Episode/Saga 正式写入。
- LIFE SelfTimeline、ToolRun 与 EAP 状态均为只读；不写 LifeEvent/Diary/Date/Goal 或 EAP 六协议。
- CTX 继续拥有最终 `RetrievalBundle` 装配；KIG 关闭不替换原 Knowledge/MEM/CTX/LIFE 路径。
- 临时聊天排除长期 Memory 与跨会话 History；各 owner 开关和隐私策略在候选进入前生效。

# KIG.14 现有知识库上的世界模型 UI

- 复用 `FilesPage`，没有第二套知识主页或路由。
- 展示项目/实体、事件时间线、维护建议、PWM 开关和“Shadow/来源化/可重建”状态。
- 删除确认先请求真实 impact preview，列出切片、向量、引用与派生关联影响，并明确不会删除 owner 数据。
- 文档详情、索引、来源入口、传输策略和模型设置继续使用既有 UI/API。
- developer diagnostics 仅返回协议、计数、状态、冲突和 proposal metadata，不返回 query/source 正文。

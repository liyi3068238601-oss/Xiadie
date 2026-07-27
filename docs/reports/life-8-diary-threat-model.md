# LIFE.8 日记验收与静态存储威胁模型

- 日期：2026-07-26
- Schema：70
- 正文存储：本机 SQLite 明文

日记仅从 revision 匹配的有效 LIFE 来源生成；planned 事件不能成为“做过”的日记事实。来源失效时 rebuild 扫描撤销 link，最后来源消失则日记 revoked。30 天确定性 fallback 样本无完全重复，连续 motif 第四次会被 fatigue guard 拒绝。

private/never 永不分享；ask 需要逐次授权。敏感日记仅在用户逐次授权，或本地 Provider 达到 `local_sensitive_verified` 时可分享。远程 Provider 的其他认证不能替代逐次授权。

威胁模型：v1 没有宣称静态加密。能读取数据库文件或未加密备份的本机账户/恶意软件可读取日记正文；备份与磁盘镜像会扩大暴露面。未来加密必须包含密钥保管、备份恢复、迁移回滚和遗忘语义的独立设计与数据迁移。

专项验证：9 passed。阶段独立 Review 留待 LIFE 总体 Review。

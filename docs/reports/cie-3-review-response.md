# CIE.3 独立 Review 响应

- Review：`E:\Xiadie\review\cie3-review\cie3-review.html`
- 结论：通过，0 P0 / 0 P1；允许进入 CIE.4
- 日期：2026-07-29

## 采纳情况

| Review 项 | 决定 | 处理 |
|---|---|---|
| P2-1 `attachment_block` 图片分支缺少意图注释 | 采纳 | 在绑定分支明确说明图片只经 `apply_images` 进入本轮 LLM messages，Memory/Knowledge/KIG 仍只看文本。 |
| P2-2 `cleanup_expired()` 只在启动调用 | 采纳 | 每次图片通过接纳校验、保存前增加轻量过期清理；拒绝路径仍无副作用，启动 GC 继续负责无后续上传的重启恢复。当前不新建常驻 worker。 |
| P2-3 `save()` 未统一使用 `_safe_path()` | 采纳 | 正式文件和原子写临时文件均改经 `_safe_path()` 解析，保留 alnum ID 前置校验。 |
| 未来本地 OCR 回退 | 延期 | 当前明确拒绝已经满足 CIE.3 诚实性门；OCR 涉及新依赖、质量门和来源标注，不插入 CIE.4。 |
| 更完整 Provider 图片授权矩阵 | 延期至 CIE.6 | CIE.3 已覆盖当前远程/本地快照与零写入边界；跨 Provider 完整矩阵按总体验收执行。 |

## 额外自审修正

Review 生成后又补强了文件系统/数据库崩溃窗口：消息事务提交时保留 `storage_path`，先删除原始文件，再清空路径。若进程在两步之间退出，启动或上传 GC 仍能从数据库定位并删除残留，不会形成无引用永久文件。

CIE.3 冻结于 Schema 81；历史 CIE.0/CIE.1/KIG 报告继续保留各自 Schema 80 事实，读取“当前 schema”的测试和 CDS 动态报告更新为 81。

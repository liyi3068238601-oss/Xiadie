# KIG.4 结构优先语义切片施工报告

- 日期：2026-07-27
- Schema：74
- Chunker：`knowledge-structure-chunker-v2`
- FTS：写入 `knowledge-fts-terms-v2`，读取兼容 v1/v2

## 实现

- 在段落合并前识别 fenced code、Markdown/制表表格、列表、heading 与 prose。
- heading path、page range、line/char range、paragraph range、chunk kind、previous/next ordinal 均随 Chunk 保存。
- 普通结构保持 1200 字符硬上限；代码/表格在 4000 字符内保持完整，超限仅按换行切分。
- Chunk content 始终是 normalized raw text 的精确 `[char_start:char_end]`，模型没有正文生成入口。
- 新版本通过 `knowledge_rebuild_chunks` 旁路构建并单事务切换；活动 v1 索引在切换前可用。

## 可选模型边界

`knowledge_boundary_proposal` 注册在 CDS Shadow。输入只给出确定性安全 cut offsets；模型只能选择其子集，不能发明 offset、改写原文或授予应用权。任何无效输出使用完整确定性 offset 集回退，`apply_exact_slices` 还会验证重新拼接等于原文。

## 验收

- 标题、定义、步骤、警告、列表、表格、代码与普通正文质量场景通过。
- 正常尺寸 100 行代码块与 80 行表格均保持单 Chunk。
- 每个 Chunk 的 content、char locator 与 SHA-256 一致，相邻 ordinal 链完整。
- v2 原子重建后 document content hash 与物理原文件字节不变，active index revision 单调 +1。
- 把文档 index_version 临时改回 v1 后仍可检索，证明兼容读取。
- Knowledge/KIG 回归：198 passed，1 warning。

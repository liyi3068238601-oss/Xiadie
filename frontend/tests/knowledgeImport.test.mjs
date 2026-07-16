import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("knowledge import requires an explicit local-only confirmation", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /导入前确认|确认导入到本地|仅复制到遐蝶本地应用数据目录/);
  assert.match(page, /本机完成，不扫描原目录、不把正文发往远程向量服务/);
  assert.match(page, /敏感资料|10 MiB|UTF-8/);
  assert.match(api, /X-Xiadie-Filename|X-Xiadie-Sensitivity|importKnowledgeFile/);
});

test("knowledge UI reports admitted files honestly before parsing exists", async () => {
  const page = await readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8");
  assert.match(page, /已安全保存 · 等待解析/);
  assert.match(page, /PDF 引用保留真实页码，扫描图片暂不做 OCR/);
  assert.doesNotMatch(page, /导入后将自动生成摘要与标签/);
});

test("knowledge chunking stays honest about stable locators and missing index", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /切片完成 · 等待索引|稳定切片/);
  assert.match(page, /标题、段落、行号与字符范围/);
  assert.match(page, /chunking_started|chunking_completed|切片中/);
  assert.doesNotMatch(page, /切片完成 · 可检索/);
  assert.match(api, /chunker_version|chunked_at|chunk_count/);
});

test("knowledge indexing exposes local readiness without claiming dialogue citations", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /索引中|已索引 · 可检索|本地索引已就绪/);
  assert.match(page, /indexing_started|indexing_completed/);
  assert.match(page, /本地 BGE-M3 已就绪|自动使用全文检索/);
  assert.match(api, /searchKnowledge|KnowledgeSearchResult|context_window/);
});

test("knowledge formats and local vectors expose capability and fallback honestly", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /txt.*md.*pdf.*docx/i);
  assert.match(page, /建立失败.*自动退回全文检索/);
  assert.match(page, /不上传正文|不把正文发往远程向量服务/);
  assert.match(api, /getKnowledgeEmbeddingStatus|buildKnowledgeEmbedding|retrieval_mode/);
});

test("knowledge citations are clickable and backed by the verified source endpoint", async () => {
  const [chat, api] = await Promise.all([
    readFile(new URL("../src/components/ChatView.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(chat, /knowledge_citations|资料原文|content_fingerprint/);
  assert.match(api, /getKnowledgeCitation|\/api\/knowledge\/citations/);
});

test("knowledge parsing progress exposes cancellation, recovery and an event timeline", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /解析完成 · 等待切片|尚未切片或索引/);
  assert.match(page, /进度详情|停止处理|recovery_pending/);
  assert.match(page, /parsing_started|parsing_completed|retry_scheduled/);
  assert.match(api, /getKnowledgeImportRun|cancelKnowledgeImportRun|KnowledgeImportEvent/);
});

test("knowledge management exposes filters, tags, reindex, retry and verified deletion", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /按文件名搜索|全部 collection|全部状态|来源详情/);
  assert.match(page, /标签|重建索引|重试处理|重试删除/);
  assert.match(page, /应用外的原文件或备份不会同步删除/);
  assert.match(api, /updateKnowledgeTags|reindexKnowledgeDocument|deleteKnowledgeDocument|retryKnowledgeDeletion/);
});

test("knowledge retrieval audit UI states that query bodies are not stored", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /最近检索审计（不保存查询正文）|没有找到资料|原会话已删除/);
  assert.match(api, /listKnowledgeRetrievals|query_fingerprint|session_available/);
});

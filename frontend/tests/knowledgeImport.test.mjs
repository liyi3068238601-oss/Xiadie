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
  assert.match(page, /文件已安全保存/);
  assert.match(page, /支持.*TXT.*Markdown.*PDF.*DOCX/);
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

test("cross-source evidence uses a lightweight strip and explicit unavailable state", async () => {
  const [chat, api] = await Promise.all([
    readFile(new URL("../src/components/ChatView.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(chat, /evidence_links|跨来源证据|来源不可用|unavailable_reason/);
  assert.match(api, /getEvidenceLink|\/api\/kig\/evidence-links|EvidenceLink/);
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

test("knowledge lifecycle preserves citations and exposes guarded local cleanup", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /引用随消息保留|已最小化|新文档默认/);
  assert.match(page, /导出元数据清单|完整清除知识库|CLEAR_ALL_KNOWLEDGE/);
  assert.match(page, /最近召回|累计.*次.*引用.*条/);
  assert.match(api, /updateKnowledgeCollectionPolicy|getKnowledgeAuditLifecycle/);
  assert.match(api, /getKnowledgeExportManifest|clearAllKnowledge/);
});

test("knowledge transmission policy and provider location stay explicit", async () => {
  const [files, settings, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/SettingsPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(files, /用之前问我|只在本机用|可以分享给遐蝶/);
  assert.match(files, /sensitivity !== "sensitive"/);
  assert.match(settings, /模型运行位置|未知（按远程处理）|只有本机回环地址/);
  assert.match(api, /transmission_policy|policy_revision|execution_location|location_revision/);
  assert.match(api, /updateKnowledgeTransmissionPolicy/);
});

test("knowledge shadow recall diagnostics are honest and contain no query body", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /知识召回判断（仅 high 会实际影响回答）/);
  assert.match(page, /建议召回|确认后召回|本轮未使用|跳过/);
  assert.match(api, /KnowledgeRecallDecision|listKnowledgeRecallDecisions|query_fingerprint/);
  assert.match(page, /条样本|P90|向量可用/);
  assert.match(api, /KnowledgeRecallStats|getKnowledgeRecallStats/);
  assert.doesNotMatch(api, /KnowledgeRecallDecision[\s\S]{0,900}\bquery:\s*string/);
});

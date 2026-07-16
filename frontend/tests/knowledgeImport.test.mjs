import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("knowledge import requires an explicit local-only confirmation", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /导入前确认|确认导入到本地|仅复制到遐蝶本地应用数据目录/);
  assert.match(page, /不调用远程模型、不生成 Embedding、不扫描原目录/);
  assert.match(page, /敏感资料|10 MiB|UTF-8/);
  assert.match(api, /X-Xiadie-Filename|X-Xiadie-Sensitivity|importKnowledgeFile/);
});

test("knowledge UI reports admitted files honestly before parsing exists", async () => {
  const page = await readFile(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8");
  assert.match(page, /已安全保存 · 等待解析/);
  assert.match(page, /索引和对话引用仍在施工/);
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

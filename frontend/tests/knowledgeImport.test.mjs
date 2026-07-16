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
  assert.match(page, /解析、索引和对话引用仍在施工/);
  assert.doesNotMatch(page, /导入后将自动生成摘要与标签/);
});

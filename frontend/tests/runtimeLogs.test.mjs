import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/components/ToolLogsPage.tsx", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

const CATEGORIES = ["model", "reasoning", "retrieval", "context", "tool", "system"];
const STATUSES = ["success", "warning", "error", "pending"];

test("runtime log API fixes the list and persisted-turn detail contracts", () => {
  assert.match(api, /export type RuntimeLogCategory/);
  assert.match(api, /export type RuntimeLogStatus/);
  for (const category of CATEGORIES) assert.match(api, new RegExp(`"${category}"`));
  for (const status of STATUSES) assert.match(api, new RegExp(`"${status}"`));
  assert.match(api, /detail_available: boolean/);
  assert.match(api, /export interface RuntimeLogTurnDetail/);
  assert.match(api, /representation: "persisted-turn-final-v1"/);
  assert.match(api, /getRuntimeLogDetail = \(eventId: string\)/);
  assert.match(api, /encodeURIComponent\(eventId\)/);
});

test("runtime log page keeps refresh opt-in and loads chat details on demand", () => {
  for (const category of CATEGORIES) assert.match(page, new RegExp(`value: "${category}"`));
  assert.match(page, /useState\(false\)/);
  assert.match(page, /window\.setInterval\(\(\) => load\(true\), 5000\)/);
  assert.match(page, /currentDetail\?\.status === "loading" \|\| currentDetail\?\.status === "loaded"/);
  assert.match(page, /api\.getRuntimeLogDetail\(item\.id\)/);
  assert.match(page, /\[item\.id\]: \{ status: "loaded", value \}/);
  assert.match(page, /原始对话已删除或不可用/);
  assert.match(page, /刷新失败：\{error\}，已保留上一次结果/);
});

test("runtime log page renders ordered inputs and final output as copyable text", () => {
  assert.match(page, /本轮输入/);
  assert.match(page, /detail\.inputs\.map/);
  assert.match(page, /item\.details\.model/);
  assert.match(page, /item\.details\.input_count/);
  assert.match(page, /最终回复/);
  assert.match(page, /navigator\.clipboard\.writeText\(text\)/);
  assert.match(page, /<pre>\{input\.content\}<\/pre>/);
  assert.match(page, /<pre>\{detail\.assistant\.content\}<\/pre>/);
  assert.doesNotMatch(page, /dangerouslySetInnerHTML/);
});

test("runtime log disclosure states the allowed body and tracing boundaries", () => {
  assert.match(page, /本地对话输入与最终回复/);
  assert.match(page, /不展示系统提示词、隐藏思维链、密钥、知识正文或记忆正文/);
  assert.match(page, /不是逐 chunk 回放/);
  assert.match(page, /不能单独证明首 Token、展示节奏或取消瞬间行为/);
  assert.match(app, /label: "运行日志"/);
});

test("runtime log page retains filters and structured metadata fallback", () => {
  assert.match(page, /RuntimeLogStatus/);
  assert.match(page, /搜索模型、错误码或理由/);
  assert.match(page, /Object\.entries\(item\.details\)/);
  assert.match(page, /setExpanded/);
});

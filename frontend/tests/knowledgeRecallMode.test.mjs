import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/components/FilesPage.tsx", import.meta.url), "utf8");

test("knowledge page exposes conservative off explicit smart modes", () => {
  assert.match(api, /mode: "off" \| "explicit" \| "smart"/);
  assert.match(page, /\["off", "explicit", "smart"\]/);
  assert.match(page, /默认模式：只有你明确提到知识库/);
  assert.match(page, /自然对话仅在高置信命中时使用/);
});

test("smart mode UI keeps remote authorization explicit", () => {
  assert.match(page, /发送给在线模型仍遵守你设置的偏好/);
  assert.match(page, /updateKnowledgeRecallSettings/);
});

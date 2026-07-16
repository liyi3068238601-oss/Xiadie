import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const chat = readFileSync(new URL("../src/components/ChatView.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("chat performs local preflight before streaming restricted knowledge", () => {
  assert.match(chat, /preflightKnowledgeTransmission/);
  assert.ok(chat.indexOf("preflightKnowledgeTransmission") < chat.indexOf("runChat({ content"));
  assert.match(api, /knowledge_grant_token/);
  assert.match(api, /knowledge_skip_restricted/);
});

test("grant confirmation exposes all four explicit user choices", () => {
  for (const label of ["只允许这一次", "本次不使用资料", "以后始终允许", "设为仅限本地"]) {
    assert.match(chat, new RegExp(label));
  }
  assert.match(chat, /位置：/);
  assert.match(chat, /token_range/);
  assert.match(css, /\.knowledge-grant-card/);
  assert.match(chat, /aria-modal="true"/);
  assert.match(chat, /event\.key === "Escape"/);
  assert.match(chat, /event\.key !== "Tab"/);
  assert.match(chat, /aria-live="polite"/);
});

test("structured authorization errors retain backend messages", () => {
  assert.match(api, /detail\?\.message/);
  assert.match(api, /knowledge_grant_required/);
});

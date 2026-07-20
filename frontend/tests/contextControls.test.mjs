import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const settings = await readFile(
  new URL("../src/components/SettingsPage.tsx", import.meta.url), "utf8",
);
const chat = await readFile(
  new URL("../src/components/ChatView.tsx", import.meta.url), "utf8",
);

test("conversation history and summary controls remain separate from long-term memory", () => {
  assert.match(settings, /参考过往聊天/);
  assert.match(settings, /reference_chat_history/);
  assert.match(settings, /summary_injection_enabled/);
  assert.match(settings, /与长期记忆开关相互独立/);
});

test("advanced diagnostics state that bodies are not recorded and raw chat is preserved", () => {
  assert.match(settings, /高级上下文诊断/);
  assert.match(settings, /不显示聊天、摘要、记忆或知识正文/);
  assert.match(settings, /原始聊天不会被删除/);
  assert.match(settings, /诊断正文/);
  assert.match(settings, /不记录/);
});

test("normal companion chat does not expose technical memory or knowledge counters", () => {
  assert.doesNotMatch(chat, /本轮参考了/);
  assert.doesNotMatch(chat, /正在核对.*本地资料/);
  assert.doesNotMatch(chat, /memoryCount/);
  assert.doesNotMatch(chat, /knowledgeCount/);
});

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const settings = await readFile(
  new URL("../src/components/SettingsPage.tsx", import.meta.url), "utf8",
);
const chat = await readFile(
  new URL("../src/components/ChatView.tsx", import.meta.url), "utf8",
);
const api = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");

test("conversation history and summary controls remain separate from long-term memory", () => {
  assert.match(settings, /参考过往聊天/);
  assert.match(settings, /reference_chat_history/);
  assert.match(settings, /summary_injection_enabled/);
  assert.match(settings, /与长期记忆开关相互独立/);
});

test("summary model destination is disclosed separately from summary injection", () => {
  assert.match(api, /export interface ConversationSummaryModelConfig/);
  assert.match(api, /getConversationSummaryModelConfig/);
  assert.match(api, /\/api\/conversation-summaries\/model-config/);
  assert.match(settings, /resolved_provider_id/);
  assert.match(settings, /resolved_model/);
  assert.match(settings, /execution_location/);
  assert.match(settings, /远程处理：生成摘要所需的历史对话文本会发送给上方远程模型/);
  assert.match(settings, /关闭后只停止摘要注入，不停止自动整理，也不改变摘要模型的数据去向/);
  assert.doesNotMatch(settings, /允许远程模型处理历史对话/);
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

test("CIE context contributors expose body-free diagnostics and per-source switches", () => {
  assert.match(settings, /当前没有已注册的第三方上下文来源/);
  assert.match(settings, /正文不会写入诊断/);
  assert.match(settings, /setContextContributorEnabled/);
  assert.match(api, /context-contribution-v1/);
  assert.match(api, /\/api\/cie\/context-contributors/);
});

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { dispatchChatSseEvent } from "./fixtures/chatSseProtocol.mjs";

const typedProtocol = await readFile(
  new URL("../src/chatSseProtocol.ts", import.meta.url), "utf8",
);

test("authoritative final replaces streamed text before done", () => {
  let text = "";
  let done = null;
  const callbacks = {
    onDelta: (delta) => { text += delta; },
    onFinal: (payload) => { text = payload.content; },
    onDone: (payload) => { done = payload; },
  };

  dispatchChatSseEvent("delta", { text: "伪造 [资料:K9]" }, callbacks);
  dispatchChatSseEvent("final", { content: "已校验 [资料引用无效]", message_id: "m1" }, callbacks);
  dispatchChatSseEvent("done", { message_id: "m1" }, callbacks);

  assert.equal(text, "已校验 [资料引用无效]");
  assert.equal(done.message_id, "m1");
});

test("legacy done content remains an authoritative fallback", () => {
  let text = "流式旧文本";
  let done = null;
  const callbacks = {
    onFinal: (payload) => { text = payload.content; },
    onDone: (payload) => { done = payload; },
  };

  dispatchChatSseEvent("done", { message_id: "m2", content: "旧服务端最终文本" }, callbacks);

  assert.equal(text, "旧服务端最终文本");
  assert.equal(done.message_id, "m2");
});

test("typed runtime protocol preserves final and legacy done replacement", async () => {
  const normalized = typedProtocol
    .replace(/export interface[\s\S]*?\n}\n\n/, "")
    .replace(/export function dispatchChatSseEvent\([\s\S]*?\): void \{/, "export function dispatchChatSseEvent(event, data, callbacks) {");
  const fixture = await readFile(
    new URL("./fixtures/chatSseProtocol.mjs", import.meta.url), "utf8",
  );
  assert.match(typedProtocol, /event === "final"/);
  assert.match(typedProtocol, /typeof data\.content === "string"/);
  assert.match(typedProtocol, /callbacks\.onFinal\?\.\(data\)/);
  assert.equal(normalized.trim(), fixture.trim());
});

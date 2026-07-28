import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  MAX_MESSAGES,
  TurnIngressBuffer,
  buildTurnEnvelopeContent,
  normalizeWindowMs,
} from "../src/turnIngressBuffer.mjs";

function harness(windowMs = 500) {
  let nextTimer = 1;
  const timers = new Map();
  const flushed = [];
  const buffer = new TurnIngressBuffer({
    windowMs,
    onFlush: async (scope, entries, reason) => flushed.push({ scope, entries, reason }),
    setTimer: (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
  });
  return { buffer, flushed, timers };
}

function entry(index, boundary = "idle_timeout") {
  return {
    client_message_id: `client_message_${index.toString().padStart(2, "0")}`,
    content: `消息 ${index}`,
    attachment_ids: [`attachment-${index}`],
    boundary,
  };
}

test("window is clamped to 300..800ms and envelope preserves order", () => {
  assert.equal(normalizeWindowMs(1), 300);
  assert.equal(normalizeWindowMs(9999), 800);
  assert.equal(buildTurnEnvelopeContent([entry(1), entry(2)]), "消息 1\n\n消息 2");
});

test("idle messages debounce into one ordered flush without duplicates", async () => {
  const { buffer, timers, flushed } = harness();
  assert.equal(buffer.enqueue("session-a:window-a", entry(1)), 1);
  assert.equal(buffer.enqueue("session-a:window-a", entry(2)), 2);
  assert.equal(timers.size, 1);
  const timer = [...timers.values()][0];
  assert.equal(timer.delay, 500);
  await timer.callback();
  await Promise.resolve();
  assert.deepEqual(flushed[0].entries.map((item) => item.client_message_id), [
    "client_message_01", "client_message_02",
  ]);
  assert.equal(flushed[0].reason, "idle_timeout");
});

test("session and window scopes are isolated", async () => {
  const { buffer, flushed } = harness();
  buffer.enqueue("session-a:window-a", entry(1));
  buffer.enqueue("session-b:window-a", entry(2));
  await buffer.flush("session-a:window-a", "explicit_send");
  assert.equal(flushed.length, 1);
  assert.equal(flushed[0].scope, "session-a:window-a");
  assert.equal(buffer.pendingCount("session-b:window-a"), 1);
});

test("explicit boundaries and max message count seal immediately", async () => {
  const explicit = harness();
  assert.equal(explicit.buffer.enqueue("scope", entry(1, "explicit_send")), 0);
  await Promise.resolve();
  assert.equal(explicit.flushed[0].reason, "explicit_send");

  const bounded = harness();
  for (let index = 1; index <= MAX_MESSAGES; index += 1) {
    const pending = bounded.buffer.enqueue("scope", entry(index));
    if (index === MAX_MESSAGES) assert.equal(pending, 0);
  }
  await Promise.resolve();
  assert.equal(bounded.flushed[0].entries.length, MAX_MESSAGES);
  assert.equal(bounded.flushed[0].reason, "max_messages");
});

test("duplicate client IDs fail before flush", () => {
  const { buffer } = harness();
  buffer.enqueue("scope", entry(1));
  assert.throws(() => buffer.enqueue("scope", entry(1)), /duplicate client_message_id/);
});

test("failed flush restores deeply immutable entries for a safe retry", async () => {
  let attempts = 0;
  const counts = [];
  const buffer = new TurnIngressBuffer({
    onFlush: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("preflight failed");
    },
    onPendingChange: (_scope, count) => counts.push(count),
  });
  buffer.enqueue("scope", { ...entry(1), attachments: [{ id: "attachment-1" }] });
  await assert.rejects(buffer.flush("scope"), /preflight failed/);
  assert.equal(buffer.pendingCount("scope"), 1);
  assert.deepEqual(counts.slice(-2), [0, 1]);
  const restored = buffer.queues.get("scope").entries[0];
  assert.throws(() => restored.attachment_ids.push("changed"));
  assert.throws(() => { restored.attachments[0].id = "changed"; });
  await buffer.flush("scope");
  assert.equal(buffer.pendingCount("scope"), 0);
});

test("dispose clears idle timers without flushing after unmount", () => {
  const { buffer, timers, flushed } = harness();
  buffer.enqueue("scope", entry(1));
  assert.equal(timers.size, 1);
  buffer.dispose();
  assert.equal(timers.size, 0);
  assert.equal(flushed.length, 0);
});

test("ChatView and API keep CIE behind the server gate and expose hard boundaries", async () => {
  const chatView = await readFile(new URL("../src/components/ChatView.tsx", import.meta.url), "utf8");
  const api = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");
  assert.match(chatView, /api\.getCieSettings\(\)/);
  assert.match(chatView, /content === "\/stop"/);
  assert.match(chatView, /e\.ctrlKey \|\| e\.metaKey/);
  assert.match(chatView, /ingress_messages: ingressMessages/);
  assert.match(chatView, /setStreaming\(null\)/);
  assert.match(api, /"\/api\/cie\/settings"/);
  assert.match(api, /ingress_messages\?: TurnIngressMessage\[\]/);
  assert.match(chatView, /stopActiveGeneration/);
  assert.match(chatView, />停止</);
  assert.match(api, /signal\?: AbortSignal/);
  assert.match(api, /"\/api\/chat\/cancel"/);
});

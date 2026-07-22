import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const root = new URL("../../", import.meta.url);

test("Electron bridge claims and begins before invoking local channels", async () => {
  const source = await readFile(new URL("desktop/main.js", root), "utf8");
  const claim = source.indexOf("/api/proactive-deliveries/claim");
  const begin = source.indexOf("/begin`");
  const renderer = source.indexOf('webContents.send("proactive-delivery"');
  assert.ok(claim >= 0 && begin > claim && renderer > begin);
  assert.match(source, /Notification\.isSupported\(\)/);
  assert.match(source, /notice\.once\("show"/);
  assert.match(source, /notification_failed/);
  assert.doesNotMatch(source, /external.*proactive-deliveries/i);
});

test("renderer delivery ack is restricted to the pet window", async () => {
  const main = await readFile(new URL("desktop/main.js", root), "utf8");
  const preload = await readFile(new URL("desktop/preload.js", root), "utf8");
  assert.match(main, /event\.sender\.id !== petWin\.webContents\.id/);
  assert.match(main, /rendererDeliveries\.get\(payload\?\.id\)/);
  assert.match(preload, /proactive-delivery-ack/);
  assert.match(preload, /removeListener\("proactive-delivery"/);
});

test("Level 2 bubble auto-dismisses and Level 3 refreshes the matching chat", async () => {
  const pet = await readFile(new URL("frontend/src/pet.tsx", root), "utf8");
  const chat = await readFile(new URL("frontend/src/components/ChatView.tsx", root), "utf8");
  assert.match(pet, /dismiss_after_ms \|\| 5000/);
  assert.match(pet, /confirmProactiveDelivery\?\.\(item\.id, true\)/);
  assert.match(chat, /item\.session_id !== sessionId/);
  assert.match(chat, /api\.listMessages\(sessionId\)\.then\(setMessages\)/);
});

test("real local delivery is a separate opt-in and schema diagnostics are current", async () => {
  const settings = await readFile(new URL("frontend/src/components/SettingsPage.tsx", root), "utf8");
  assert.match(settings, /proactive_local_delivery_enabled: "0"/);
  assert.match(settings, /启用本机主动表达（实验）/);
  assert.match(settings, /Schema 版本：59/);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const chat = readFileSync(new URL("../src/components/ChatView.tsx", import.meta.url), "utf8");

test("native images require capability evidence and a request-bound destination snapshot", () => {
  assert.match(api, /\/api\/cie\/vision-capability/);
  assert.match(api, /image_provider_id/);
  assert.match(api, /image_location_revision/);
  assert.match(chat, /probeVisionCapability/);
  assert.match(chat, /window\.confirm/);
  assert.match(chat, /remote_image_once/);
  assert.match(chat, /image_authorization_snapshot_changed/);
});

test("image UI exposes bounded formats without claiming historical raw access", () => {
  assert.match(chat, /\.png,\.jpg,\.jpeg/);
  assert.match(chat, /每轮最多选择 4 张图片/);
  assert.match(chat, /原始字节已销毁/);
  assert.match(chat, /attachment\.attachment_kind !== "image"/);
});

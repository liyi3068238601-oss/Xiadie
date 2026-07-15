import test from "node:test";
import assert from "node:assert/strict";
import {
  MEMORY_NOTICE_INTERVAL_MS,
  memoryNoticeText,
  shouldShowMemoryNotice,
} from "../src/memoryNotice.mjs";

test("memory notice is rate limited for five minutes", () => {
  const now = 1_000_000;
  assert.equal(shouldShowMemoryNotice(Number.NaN, now), true);
  assert.equal(shouldShowMemoryNotice(now - MEMORY_NOTICE_INTERVAL_MS + 1, now), false);
  assert.equal(shouldShowMemoryNotice(now - MEMORY_NOTICE_INTERVAL_MS, now), true);
});

test("memory notice never exposes remembered content", () => {
  assert.equal(memoryNoticeText(1), "遐蝶记住了这件事");
  assert.equal(memoryNoticeText(3), "遐蝶记住了 3 件值得留下的事");
});

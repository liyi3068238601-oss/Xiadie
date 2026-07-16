import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  MEMORY_OBSERVER_MAX_WAIT_MS,
  memoryObserverPollDelay,
  shouldContinueMemoryObserverPolling,
} from "../src/observerPolling.mjs";

test("memory observer polling waits for delayed background processing without hammering", () => {
  assert.equal(MEMORY_OBSERVER_MAX_WAIT_MS, 15 * 60 * 1000);
  assert.equal(memoryObserverPollDelay(0), 1000);
  assert.equal(memoryObserverPollDelay(14_999), 1000);
  assert.equal(memoryObserverPollDelay(15_000), 5000);
  assert.equal(shouldContinueMemoryObserverPolling(60_000, 0), true);
  assert.equal(shouldContinueMemoryObserverPolling(MEMORY_OBSERVER_MAX_WAIT_MS, 0), false);
  assert.equal(shouldContinueMemoryObserverPolling(1_000, 3), false);
});

test("Live2D perk animation cancels an older frame loop before it can overwrite state", async () => {
  const source = await readFile(new URL("../src/pet.tsx", import.meta.url), "utf8");
  assert.match(source, /__perkGeneration/);
  assert.match(source, /model\.__perkGeneration !== generation/);
  assert.match(source, /setExpression\(model, getClusterPresentation\(cluster\)\.expression\)/);
});

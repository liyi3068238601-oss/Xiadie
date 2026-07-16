import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("Fragment lifecycle management exposes score, protection, restore and privacy deletion", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/components/MemoriesPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /getMemoryLifecycle|protection_reasons|components/);
  assert.match(page, /restoreMemory|lifecycle_revision/);
  assert.match(page, /privacyDeleteMemory|请输入 DELETE|不会自动创建备份/);
  assert.match(api, /privacy=true|target_status: "active"/);
});

test("conflict relations remain advisory and have an auditable disposition UI", async () => {
  const page = await readFile(
    new URL("../src/components/MemoriesPage.tsx", import.meta.url), "utf8",
  );
  assert.match(page, /不会自动改写或删除任何记忆/);
  assert.match(page, /scanMemoryRelations|setMemoryRelationStatus/);
  assert.match(page, /conflict_count|relation_count/);
  assert.match(page, /标记已解决|忽略提示/);
});

test("Episode slow lifecycle UI includes all states, events and guarded deletion", async () => {
  const section = await readFile(
    new URL("../src/components/EpisodesSection.tsx", import.meta.url), "utf8",
  );
  assert.match(section, /completed: "已成熟"|archived: "已归档"/);
  assert.match(section, /lifecycle_events|transitionEpisode|expected_revision/);
  assert.match(section, /请输入 DELETE|不会自动创建备份/);
});

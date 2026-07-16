import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  allowedSagaTransitions,
  sagaEventLabel,
  sagaRoleLabel,
  sagaStatusPresentation,
  sagaSummaryPresentation,
} from "../src/sagaPresentation.mjs";

test("all Saga lifecycle and summary states have explicit user-facing text", () => {
  const labels = ["active", "completed", "archived", "tombstone"]
    .map((status) => sagaStatusPresentation(status).label);
  assert.deepEqual(labels, ["进行中", "已完成", "已归档", "已删除"]);
  assert.equal(new Set(labels).size, 4);
  assert.equal(sagaStatusPresentation("future-status").label, "状态未知");

  for (const status of ["model_validated", "extractive_fallback", "user_edited", "legacy_rule"]) {
    assert.ok(sagaSummaryPresentation(status).label);
    assert.ok(sagaSummaryPresentation(status).detail);
  }
});

test("Saga lifecycle controls expose only backend-supported transitions", () => {
  assert.deepEqual(allowedSagaTransitions("active"), ["completed", "tombstone"]);
  assert.deepEqual(allowedSagaTransitions("completed"), ["active", "archived", "tombstone"]);
  assert.deepEqual(allowedSagaTransitions("archived"), ["active", "tombstone"]);
  assert.deepEqual(allowedSagaTransitions("tombstone"), []);
  assert.deepEqual(allowedSagaTransitions("future-status"), []);
});

test("Saga timeline and event audit vocabulary stays readable", () => {
  assert.deepEqual(
    ["anchor", "development", "resolution"].map(sagaRoleLabel),
    ["故事起点", "后续发展", "收束经历"],
  );
  assert.equal(sagaEventLabel("sources_corrected"), "纠正来源归组");
  assert.equal(sagaEventLabel("future-action"), "系统记录");
});

test("main memory page exposes formal Saga audit and correction controls", async () => {
  const [section, page] = await Promise.all([
    readFile(new URL("../src/components/SagasSection.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/MemoriesPage.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(page, /<SagasSection/);
  assert.match(section, /listSagas|correctSagaSources|transitionSaga/);
  assert.match(section, /expected_revision|error\.status === 409/);
  assert.match(section, /详情已刷新，请确认后重试/);
  assert.match(section, /aria-expanded|fieldset/);
  assert.doesNotMatch(section, /acceptEpisodeCandidate|rejectEpisodeCandidate/);
});

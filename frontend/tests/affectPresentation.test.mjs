import test from "node:test";
import assert from "node:assert/strict";
import {
  CLUSTER_PRESENTATION,
  getClusterPresentation,
} from "../src/affectPresentation.mjs";

const EXPECTED_CLUSTERS = [
  "bright", "serene", "agitated", "melancholic", "focused",
  "contemplative", "pleased", "subdued", "neutral",
];

test("all nine backend clusters have one display and Live2D mapping", () => {
  assert.deepEqual(Object.keys(CLUSTER_PRESENTATION).sort(), EXPECTED_CLUSTERS.sort());
  for (const item of Object.values(CLUSTER_PRESENTATION)) {
    assert.equal(typeof item.icon, "string");
    assert.ok(Number.isInteger(item.expression));
    assert.ok(item.summary.length > 0);
  }
});

test("unknown clusters fall back to neutral", () => {
  assert.equal(getClusterPresentation("future-cluster"), CLUSTER_PRESENTATION.neutral);
});

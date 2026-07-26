import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/components/LifePage.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("life product surface covers all user-facing continuity areas", () => {
  for (const tab of ["today", "diary", "dates", "goals", "settings"]) {
    assert.match(page, new RegExp(`\\['${tab}'`));
  }
  assert.match(page, /className="life-private"/);
  assert.doesNotMatch(page, /<details[^>]+life-private[^>]+open/);
  assert.match(page, /continuous_simulated/);
  assert.match(page, /rebuildLifeViews/);
  assert.match(page, /exportLifeData/);
});

test("ordinary life UI does not expose model scoring internals", () => {
  assert.doesNotMatch(page, /confidence_band|reason_codes|candidate_id|model_output/);
  assert.match(page, /state_revision/);
  assert.match(page, /state_algorithm/);
  assert.match(page, /anomaly_code/);
});

test("life API and accessible responsive styling are wired", () => {
  for (const route of [
    "/api/life/settings", "/api/life/diary", "/api/life/dates",
    "/api/life/goals", "/api/life/rebuild", "/api/life/export",
    "/api/life/diagnostics",
  ]) {
    assert.match(api, new RegExp(route.replaceAll("/", "\\/")));
  }
  assert.match(styles, /\.life-tabs button:focus-visible/);
  assert.match(styles, /@media \(max-width: 800px\)/);
});

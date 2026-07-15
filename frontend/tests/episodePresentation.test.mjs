import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  episodeSummaryPresentation,
  shortSourceHash,
} from "../src/episodePresentation.mjs";

test("all formal Episode summary states have user-facing explanations", () => {
  assert.deepEqual(
    ["model_validated", "extractive_fallback", "user_edited", "legacy_rule"].map(
      (status) => episodeSummaryPresentation(status).label
    ),
    ["来源校验摘要", "原文整理", "人工纠错", "旧版经历"],
  );
  assert.equal(episodeSummaryPresentation("future-status").label, "校验状态未知");
});

test("source fingerprints are shortened only when they are valid hashes", () => {
  const hash = "a1".repeat(32);
  assert.equal(shortSourceHash(hash), hash.slice(0, 12));
  assert.equal(shortSourceHash(""), "未记录");
  assert.equal(shortSourceHash("not-a-hash"), "未记录");
});

test("main Episode UI no longer exposes candidate confirmation controls", async () => {
  const source = await readFile(
    new URL("../src/components/EpisodesSection.tsx", import.meta.url), "utf8"
  );
  assert.doesNotMatch(source, /listEpisodeCandidates|acceptEpisodeCandidate|rejectEpisodeCandidate/);
  assert.doesNotMatch(source, /接受 Episode|待确认/);
  assert.match(source, /correctEpisode|纠正这段经历|来源校验/);
});

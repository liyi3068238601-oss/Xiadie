import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  REPLY_PRESENTATION_PROTOCOL_VERSION,
  ReplyPresentationBuffer,
  splitPresentationUnits,
} from "../src/replyPresentation.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(HERE, "../../backend/tests/fixtures/cie0_interaction_v1.json");

function clock() {
  const callbacks = [];
  return {
    setTimer(callback) { callbacks.push(callback); return callbacks.length; },
    clearTimer() { callbacks.length = 0; },
    drain() { while (callbacks.length) callbacks.shift()(); },
  };
}

export function buildReport() {
  const fixture = JSON.parse(readFileSync(FIXTURE, "utf8"));
  let reconstructionDiffs = 0;
  let duplicateSends = 0;
  let codeBlockBreaks = 0;
  for (const item of fixture.rhythm) {
    const split = splitPresentationUnits(item.content, { final: true });
    if (split.units.join("") + split.remainder !== item.content) reconstructionDiffs += 1;
    if (split.units.some((unit) => ((unit.match(/```/g) || []).length % 2) !== 0)) {
      codeBlockBreaks += 1;
    }
    const timer = clock();
    let visible = "";
    let replacements = 0;
    const presentation = new ReplyPresentationBuffer({
      onDisplay: (text) => { visible += text; },
      onReplace: (text) => { visible = text; replacements += 1; },
      setTimer: timer.setTimer,
      clearTimer: timer.clearTimer,
    });
    const midpoint = Math.max(1, Math.floor(item.content.length / 2));
    presentation.push(item.content.slice(0, midpoint));
    presentation.push(item.content.slice(midpoint));
    timer.drain();
    presentation.finish(item.content);
    if (visible !== item.content || replacements !== 1) duplicateSends += 1;
  }

  const interruptTimer = clock();
  let interruptedVisible = "";
  const interruption = new ReplyPresentationBuffer({
    onDisplay: (text) => { interruptedVisible += text; },
    onReplace: () => undefined,
    setTimer: interruptTimer.setTimer,
    clearTimer: interruptTimer.clearTimer,
  });
  interruption.push("已展示。不可展示。也不可展示。");
  const visibleBeforeCancel = interruptedVisible;
  interruption.cancel();
  interruptTimer.drain();

  const samples = fixture.rhythm.length;
  return {
    protocol_version: REPLY_PRESENTATION_PROTOCOL_VERSION,
    sample_count: samples,
    source_fixture: "cie0_interaction_v1.json#rhythm",
    provider_calls: 0,
    schema_version: 81,
    uses_schema_82: false,
    metrics: {
      text_reconstruction_diff_rate: reconstructionDiffs / samples,
      duplicate_send_rate: duplicateSends / samples,
      code_block_break_rate: codeBlockBreaks / samples,
      interruption_undisplayed_leak_rate: interruptedVisible === visibleBeforeCancel ? 0.0 : 1.0,
      semantic_model_rewrite_calls: 0,
    },
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const report = JSON.stringify(buildReport(), null, 2) + "\n";
  const outputIndex = process.argv.indexOf("--output");
  if (outputIndex >= 0 && process.argv[outputIndex + 1]) {
    writeFileSync(resolve(process.argv[outputIndex + 1]), report, "utf8");
  }
  process.stdout.write(report);
}

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  ReplyPresentationBuffer,
  splitPresentationUnits,
} from "../src/replyPresentation.mjs";
import { buildReport } from "../scripts/run-cie4-acceptance.mjs";

const chatViewSource = readFileSync(new URL("../src/components/ChatView.tsx", import.meta.url), "utf8");

const FIXTURES = [
  "你好。今天也一起慢慢来！",
  "版本是 3.14，不要把小数拆开。下一句。",
  "链接 https://example.com/a.b?x=1.2 请完整显示。",
  "引用：“我会在这里。” 然后继续。",
  "Markdown [说明。仍在链接内](https://example.com/a.b) 结束。",
  "代码如下：\n```ts\nconst value = 3.14;\nconsole.log(value);\n```\n代码之后。",
  "行内 `a.b();` 不应形成展示边界。最后一句。",
];

test("presentation segmentation reconstructs every fixture byte-for-byte", () => {
  for (const fixture of FIXTURES) {
    const split = splitPresentationUnits(fixture, { final: true });
    assert.equal(split.units.join("") + split.remainder, fixture);
  }
});

test("code fences, URLs, Markdown links, decimals and quotes stay protected", () => {
  const source = FIXTURES.join("\n");
  const split = splitPresentationUnits(source, { final: true });
  const codeUnits = split.units.filter((unit) => unit.includes("```"));
  assert.equal(codeUnits.length, 1);
  assert.equal((codeUnits[0].match(/```/g) || []).length, 2);
  assert.ok(split.units.some((unit) => unit.includes("https://example.com/a.b?x=1.2")));
  assert.ok(split.units.some((unit) => unit.includes("[说明。仍在链接内](https://example.com/a.b)")));
  assert.ok(split.units.some((unit) => unit.includes("3.14")));
  assert.ok(split.units.some((unit) => unit.includes("。”")));
});

test("every possible two-chunk boundary preserves the exact source", () => {
  for (const fixture of FIXTURES) {
    for (let cut = 0; cut <= fixture.length; cut += 1) {
      const first = splitPresentationUnits(fixture.slice(0, cut));
      const second = splitPresentationUnits(first.remainder + fixture.slice(cut), { final: true });
      assert.equal([...first.units, ...second.units].join("") + second.remainder, fixture);
      assert.ok([...first.units, ...second.units].every(
        (unit) => ((unit.match(/```/g) || []).length % 2) === 0,
      ));
    }
  }
});

function fakeClock() {
  const callbacks = new Map();
  let sequence = 0;
  return {
    setTimer(callback) {
      const id = ++sequence;
      callbacks.set(id, callback);
      return id;
    },
    clearTimer(id) { callbacks.delete(id); },
    tick() {
      const next = callbacks.entries().next().value;
      if (!next) return false;
      callbacks.delete(next[0]);
      next[1]();
      return true;
    },
  };
}

test("paced display never duplicates and authoritative final replaces the preview", () => {
  const clock = fakeClock();
  let displayed = "";
  let replaced = "";
  const buffer = new ReplyPresentationBuffer({
    onDisplay: (text) => { displayed += text; },
    onReplace: (text) => { replaced = text; },
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });
  buffer.push("第一句。第二句。第三句");
  assert.equal(displayed, "第一句。");
  clock.tick();
  assert.equal(displayed, "第一句。第二句。");
  const authoritative = "第一句。第二句。第三句。";
  buffer.finish(authoritative);
  while (clock.tick()) { /* cancelled timers must be inert */ }
  assert.equal(replaced, authoritative);
  assert.equal(displayed, "第一句。第二句。");
});

test("new user interruption discards all not-yet-displayed segments", () => {
  const clock = fakeClock();
  let displayed = "";
  const buffer = new ReplyPresentationBuffer({
    onDisplay: (text) => { displayed += text; },
    onReplace: () => undefined,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });
  buffer.push("已经展示。不能再展示。也不能展示。");
  assert.equal(displayed, "已经展示。");
  buffer.cancel();
  while (clock.tick()) { /* cancelled timers must be inert */ }
  assert.equal(displayed, "已经展示。");
});

test("CIE.4 fixed rhythm set meets every zero-tolerance gate", () => {
  const report = buildReport();
  assert.equal(report.sample_count, 20);
  assert.equal(report.uses_schema_82, false);
  assert.deepEqual(report.metrics, {
    text_reconstruction_diff_rate: 0,
    duplicate_send_rate: 0,
    code_block_break_rate: 0,
    interruption_undisplayed_leak_rate: 0,
    semantic_model_rewrite_calls: 0,
  });
});

test("ChatView gates presentation, clears interruption timers and uses natural phase labels", () => {
  assert.match(chatViewSource, /cieEnabled \? new ReplyPresentationBuffer/);
  assert.match(chatViewSource, /replyPresentationRef\.current\?\.cancel\(\)/);
  assert.match(chatViewSource, /正在准备回复/);
  assert.match(chatViewSource, /正在组织语言/);
  assert.match(chatViewSource, /text: final\.content, phase: "completed"/);
  assert.doesNotMatch(chatViewSource, />retrieval</);
});

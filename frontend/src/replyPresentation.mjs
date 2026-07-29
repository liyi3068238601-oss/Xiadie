export const REPLY_PRESENTATION_PROTOCOL_VERSION = "reply-presentation-v1";
export const DEFAULT_CADENCE_MS = 45;

const CJK_BOUNDARIES = new Set(["。", "！", "？", "；"]);
const LATIN_BOUNDARIES = new Set([".", "!", "?", ";"]);
const CLOSERS = new Set(['"', "'", "”", "’", "」", "』", "】", ")", "）", "]", "》"]);

function markdownSpanEnd(text, start) {
  const labelStart = text[start] === "!" && text[start + 1] === "[" ? start + 1 : start;
  if (text[labelStart] !== "[") return -1;
  const labelEnd = text.indexOf("](", labelStart + 1);
  if (labelEnd < 0) return -1;
  const targetEnd = text.indexOf(")", labelEnd + 2);
  return targetEnd < 0 ? text.length : targetEnd + 1;
}

function urlSpanEnd(text, start) {
  if (!text.startsWith("https://", start) && !text.startsWith("http://", start)) return -1;
  let end = start;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  return end;
}

export function splitPresentationUnits(text, { final = false } = {}) {
  const units = [];
  let unitStart = 0;
  let index = 0;
  let fenced = false;
  let inline = false;

  while (index < text.length) {
    if (text.startsWith("```", index) && !inline) {
      fenced = !fenced;
      index += 3;
      continue;
    }
    if (!fenced && text[index] === "`") {
      inline = !inline;
      index += 1;
      continue;
    }
    if (fenced || inline) {
      index += 1;
      continue;
    }

    const markdownEnd = markdownSpanEnd(text, index);
    if (markdownEnd >= 0) {
      if (markdownEnd === text.length && !final) break;
      index = markdownEnd;
      continue;
    }
    const urlEnd = urlSpanEnd(text, index);
    if (urlEnd >= 0) {
      if (urlEnd === text.length && !final) break;
      index = urlEnd;
      continue;
    }

    const character = text[index];
    let boundaryEnd = -1;
    if (character === "\n") {
      boundaryEnd = index + 1;
    } else if (CJK_BOUNDARIES.has(character)) {
      boundaryEnd = index + 1;
    } else if (LATIN_BOUNDARIES.has(character)) {
      const decimalPoint = character === "."
        && /\d/.test(text[index - 1] || "")
        && /\d/.test(text[index + 1] || "");
      const next = text[index + 1];
      if (!decimalPoint && (next === undefined || /\s/.test(next) || CLOSERS.has(next))) {
        boundaryEnd = index + 1;
      }
    }
    if (boundaryEnd < 0) {
      index += 1;
      continue;
    }
    while (boundaryEnd < text.length && CLOSERS.has(text[boundaryEnd])) boundaryEnd += 1;
    if (boundaryEnd === text.length && !final) break;
    if (boundaryEnd > unitStart) units.push(text.slice(unitStart, boundaryEnd));
    unitStart = boundaryEnd;
    index = boundaryEnd;
  }

  if (final && unitStart < text.length) {
    units.push(text.slice(unitStart));
    unitStart = text.length;
  }
  return { units, remainder: text.slice(unitStart) };
}

export class ReplyPresentationBuffer {
  constructor({
    onDisplay,
    onReplace,
    cadenceMs = DEFAULT_CADENCE_MS,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    if (typeof onDisplay !== "function" || typeof onReplace !== "function") {
      throw new TypeError("onDisplay and onReplace are required");
    }
    this.onDisplay = onDisplay;
    this.onReplace = onReplace;
    this.cadenceMs = Math.max(0, Number(cadenceMs) || 0);
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.remainder = "";
    this.queue = [];
    this.timer = null;
    this.cancelled = false;
  }

  push(chunk) {
    if (this.cancelled || typeof chunk !== "string" || chunk.length === 0) return;
    const split = splitPresentationUnits(this.remainder + chunk);
    this.remainder = split.remainder;
    this.queue.push(...split.units);
    this.#drain();
  }

  finish(authoritativeText) {
    if (this.cancelled) return;
    this.#clear();
    this.cancelled = true;
    this.onReplace(String(authoritativeText ?? ""));
  }

  cancel({ flush = false } = {}) {
    if (this.cancelled) return;
    if (flush) {
      const remaining = this.queue.join("") + this.remainder;
      if (remaining) this.onDisplay(remaining);
    }
    this.#clear();
    this.cancelled = true;
  }

  #drain() {
    if (this.timer !== null || this.queue.length === 0 || this.cancelled) return;
    this.onDisplay(this.queue.shift());
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.#drain();
    }, this.cadenceMs);
  }

  #clear() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.queue = [];
    this.remainder = "";
  }
}

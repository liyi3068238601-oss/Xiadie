export const TURN_INGRESS_PROTOCOL_VERSION = "turn-ingress-buffer-v1";
export const DEFAULT_WINDOW_MS = 500;
export const MIN_WINDOW_MS = 300;
export const MAX_WINDOW_MS = 800;
export const MAX_MESSAGES = 20;

export function normalizeWindowMs(value) {
  return Math.max(MIN_WINDOW_MS, Math.min(MAX_WINDOW_MS, Number(value) || DEFAULT_WINDOW_MS));
}

export function buildTurnEnvelopeContent(entries) {
  return entries.map((entry) => entry.content.trim()).filter(Boolean).join("\n\n");
}

function freezeEntry(entry) {
  const attachments = Array.isArray(entry.attachments)
    ? Object.freeze(entry.attachments.map((attachment) => Object.freeze({ ...attachment })))
    : entry.attachments;
  return Object.freeze({
    ...entry,
    attachment_ids: Object.freeze([...entry.attachment_ids]),
    ...(attachments === undefined ? {} : { attachments }),
  });
}

export class TurnIngressBuffer {
  constructor({ windowMs = DEFAULT_WINDOW_MS, onFlush, setTimer = setTimeout, clearTimer = clearTimeout }) {
    if (typeof onFlush !== "function") throw new TypeError("onFlush is required");
    this.windowMs = normalizeWindowMs(windowMs);
    this.onFlush = onFlush;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.queues = new Map();
  }

  enqueue(scope, entry) {
    if (!scope || !entry?.client_message_id) throw new TypeError("scope and client_message_id are required");
    let queue = this.queues.get(scope);
    if (!queue) {
      queue = { entries: [], timer: null };
      this.queues.set(scope, queue);
    }
    if (queue.entries.some((item) => item.client_message_id === entry.client_message_id)) {
      throw new Error("duplicate client_message_id");
    }
    queue.entries.push(freezeEntry(entry));
    if (queue.timer !== null) this.clearTimer(queue.timer);
    const immediate = entry.boundary !== "idle_timeout" || queue.entries.length >= MAX_MESSAGES;
    if (immediate) {
      void this.flush(scope, entry.boundary === "idle_timeout" ? "max_messages" : entry.boundary)
        .catch(() => undefined);
    } else {
      queue.timer = this.setTimer(
        () => void this.flush(scope, "idle_timeout").catch(() => undefined),
        this.windowMs,
      );
    }
    return this.pendingCount(scope);
  }

  pendingCount(scope) {
    return this.queues.get(scope)?.entries.length ?? 0;
  }

  async flush(scope, reason = "explicit_send") {
    const queue = this.queues.get(scope);
    if (!queue || queue.entries.length === 0) return false;
    this.queues.delete(scope);
    if (queue.timer !== null) this.clearTimer(queue.timer);
    try {
      await this.onFlush(scope, queue.entries, reason);
    } catch (error) {
      const pending = this.queues.get(scope);
      const restored = pending
        ? { entries: [...queue.entries, ...pending.entries], timer: pending.timer }
        : { entries: [...queue.entries], timer: null };
      this.queues.set(scope, restored);
      throw error;
    }
    return true;
  }
}

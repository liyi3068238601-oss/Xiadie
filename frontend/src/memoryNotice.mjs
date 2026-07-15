export const MEMORY_NOTICE_INTERVAL_MS = 5 * 60 * 1000;

export function shouldShowMemoryNotice(lastShownAt, now = Date.now()) {
  return !Number.isFinite(lastShownAt) || now - lastShownAt >= MEMORY_NOTICE_INTERVAL_MS;
}

export function memoryNoticeText(count) {
  return count > 1 ? `遐蝶记住了 ${count} 件值得留下的事` : "遐蝶记住了这件事";
}

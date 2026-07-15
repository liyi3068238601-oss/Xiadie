export const MEMORY_NOTICE_INTERVAL_MS: number;
export function shouldShowMemoryNotice(lastShownAt: number, now?: number): boolean;
export function memoryNoticeText(count: number): string;

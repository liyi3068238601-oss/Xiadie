export const MEMORY_OBSERVER_MAX_WAIT_MS: number;
export const MEMORY_OBSERVER_MAX_CONSECUTIVE_ERRORS: number;
export function memoryObserverPollDelay(elapsedMs: number): number;
export function shouldContinueMemoryObserverPolling(
  elapsedMs: number,
  consecutiveErrors: number,
): boolean;

export const MEMORY_OBSERVER_MAX_WAIT_MS = 15 * 60 * 1000;
export const MEMORY_OBSERVER_MAX_CONSECUTIVE_ERRORS = 3;

export function memoryObserverPollDelay(elapsedMs) {
  return elapsedMs < 15_000 ? 1_000 : 5_000;
}

export function shouldContinueMemoryObserverPolling(elapsedMs, consecutiveErrors) {
  return elapsedMs < MEMORY_OBSERVER_MAX_WAIT_MS
    && consecutiveErrors < MEMORY_OBSERVER_MAX_CONSECUTIVE_ERRORS;
}

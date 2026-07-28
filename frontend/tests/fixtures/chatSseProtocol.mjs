export function dispatchChatSseEvent(event, data, callbacks, state) {
  if (event === "meta") callbacks.onMeta?.(data);
  else if (event === "phase") callbacks.onPhase?.(data.phase);
  else if (event === "cancelled") callbacks.onCancelled?.(data);
  else if (event === "delta") callbacks.onDelta?.(data.text);
  else if (event === "error") callbacks.onError?.(data.message, data.hint);
  else if (event === "final") {
    if (state) state.finalSeen = true;
    callbacks.onFinal?.(data);
  }
  else if (event === "done") {
    if (typeof data.content === "string" && !state?.finalSeen) callbacks.onFinal?.(data);
    callbacks.onDone?.(data);
  }
}

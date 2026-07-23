export function dispatchChatSseEvent(event, data, callbacks) {
  if (event === "meta") callbacks.onMeta?.(data);
  else if (event === "delta") callbacks.onDelta?.(data.text);
  else if (event === "error") callbacks.onError?.(data.message, data.hint);
  else if (event === "final") callbacks.onFinal?.(data);
  else if (event === "done") {
    if (typeof data.content === "string") callbacks.onFinal?.(data);
    callbacks.onDone?.(data);
  }
}

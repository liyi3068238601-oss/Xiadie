export interface ChatSseCallbacks {
  onMeta?: (data: any) => void;
  onDelta?: (text: string) => void;
  onError?: (message: string, hint: string) => void;
  onFinal?: (data: any) => void;
  onDone?: (data: any) => void;
}

export function dispatchChatSseEvent(
  event: string,
  data: any,
  callbacks: ChatSseCallbacks,
): void {
  if (event === "meta") callbacks.onMeta?.(data);
  else if (event === "delta") callbacks.onDelta?.(data.text);
  else if (event === "error") callbacks.onError?.(data.message, data.hint);
  else if (event === "final") callbacks.onFinal?.(data);
  else if (event === "done") {
    if (typeof data.content === "string") callbacks.onFinal?.(data);
    callbacks.onDone?.(data);
  }
}

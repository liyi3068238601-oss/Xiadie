export interface ChatSseCallbacks {
  onMeta?: (data: any) => void;
  onDelta?: (text: string) => void;
  onError?: (message: string, hint: string) => void;
  onFinal?: (data: any) => void;
  onDone?: (data: any) => void;
  onPhase?: (phase: "retrieval" | "generation" | "persistence" | "completed") => void;
  onCancelled?: (data: { phase: string; persisted: boolean }) => void;
}

export function dispatchChatSseEvent(
  event: string,
  data: any,
  callbacks: ChatSseCallbacks,
  state?: { finalSeen: boolean },
): void {
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

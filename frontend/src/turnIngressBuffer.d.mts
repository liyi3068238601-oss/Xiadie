export interface BufferedIngressLike {
  client_message_id: string;
  content: string;
  attachment_ids: string[];
  boundary: string;
}

export const TURN_INGRESS_PROTOCOL_VERSION: string;
export const DEFAULT_WINDOW_MS: number;
export const MIN_WINDOW_MS: number;
export const MAX_WINDOW_MS: number;
export const MAX_MESSAGES: number;

export function normalizeWindowMs(value: number): number;
export function buildTurnEnvelopeContent(entries: BufferedIngressLike[]): string;

export class TurnIngressBuffer<T extends BufferedIngressLike = BufferedIngressLike> {
  constructor(options: {
    windowMs?: number;
    onFlush: (scope: string, entries: T[], reason: string) => void | Promise<void>;
    onPendingChange?: (scope: string, count: number) => void;
    setTimer?: typeof setTimeout;
    clearTimer?: typeof clearTimeout;
  });
  enqueue(scope: string, entry: T): number;
  pendingCount(scope: string): number;
  flush(scope: string, reason?: string): Promise<boolean>;
  dispose(): void;
}

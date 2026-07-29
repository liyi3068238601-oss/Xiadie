export const REPLY_PRESENTATION_PROTOCOL_VERSION: string;
export const DEFAULT_CADENCE_MS: number;

export function splitPresentationUnits(
  text: string,
  options?: { final?: boolean },
): { units: string[]; remainder: string };

export class ReplyPresentationBuffer {
  constructor(options: {
    onDisplay: (text: string) => void;
    onReplace: (text: string) => void;
    cadenceMs?: number;
    setTimer?: typeof setTimeout;
    clearTimer?: typeof clearTimeout;
  });
  push(chunk: string): void;
  finish(authoritativeText: string): void;
  cancel(options?: { flush?: boolean }): void;
}

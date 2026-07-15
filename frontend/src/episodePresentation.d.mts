export interface EpisodeSummaryPresentation {
  label: string;
  tone: "verified" | "extractive" | "corrected" | "legacy";
  detail: string;
}

export function episodeSummaryPresentation(status: string): EpisodeSummaryPresentation;
export function shortSourceHash(value: string | null | undefined): string;

export type EmotionCluster =
  | "bright"
  | "serene"
  | "agitated"
  | "melancholic"
  | "focused"
  | "contemplative"
  | "pleased"
  | "subdued"
  | "neutral";

export interface ClusterPresentation {
  icon: string;
  expression: number;
  summary: string;
}

export const CLUSTER_PRESENTATION: Readonly<Record<EmotionCluster, ClusterPresentation>>;
export function getClusterPresentation(cluster: string): ClusterPresentation;

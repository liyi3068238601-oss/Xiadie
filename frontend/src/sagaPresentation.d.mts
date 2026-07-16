export interface SagaPresentation {
  label: string;
  detail: string;
  tone: string;
}

export function sagaStatusPresentation(status: string): SagaPresentation;
export function sagaSummaryPresentation(status: string): SagaPresentation;
export function sagaEventLabel(action: string): string;
export function sagaRoleLabel(role: string): string;
export function allowedSagaTransitions(status: string): string[];

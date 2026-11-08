export interface ConnectorFormState {
  currentStep: number;
  source: string | null;
  destination: string | null;
  destinationConfigId: number | null;
  configuration: Record<string, string>;
}

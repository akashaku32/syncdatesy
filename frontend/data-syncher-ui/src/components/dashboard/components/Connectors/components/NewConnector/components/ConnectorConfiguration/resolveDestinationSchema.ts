/**
 * Resolve the destination schema for connector create/update payloads.
 *
 * Must be the schema name from the connection form (e.g. LANDING), not the
 * selected destination config display name (e.g. "My Databricks Prod").
 */
export function resolveDestinationSchema(
  formValues: Record<string, unknown>,
  fallback?: string | null,
): string {
  const fromForm = String(formValues.destination_schema ?? "").trim();
  if (fromForm) {
    return fromForm;
  }
  return String(fallback ?? "").trim();
}

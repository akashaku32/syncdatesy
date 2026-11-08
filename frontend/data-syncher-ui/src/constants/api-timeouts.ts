/** Django → migration-function Databricks cold start (default backend ~960s). */
export const LONG_DESTINATION_AUTH_TIMEOUT_MS = Number(
  import.meta.env.VITE_LONG_DESTINATION_AUTH_TIMEOUT_MS ?? 20 * 60 * 1000,
);

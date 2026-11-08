/** Shared table failure copy for connectors, pipelines, and batch views. */

const GENERIC_TABLE_FAILURE_RE = /^Tables failed:\s*\[/i;

export function isGenericTableFailureMessage(message: string): boolean {
  const text = message.trim();
  if (!text) return true;
  const lower = text.toLowerCase();
  if (lower.includes("migration in progress")) return true;
  if (lower.includes("retrying:") && lower.includes("tables failed"))
    return true;
  if ((text.match(/Tables failed:/gi) || []).length > 1) return true;
  return GENERIC_TABLE_FAILURE_RE.test(text);
}

export function isTableRowFailed(table: {
  status?: string | null;
  status_icon?: string | null;
}): boolean {
  const raw = String(table.status_icon || table.status || "").toLowerCase();
  return ["failed", "error", "f", "e"].includes(raw);
}

export function resolveTableDisplayError(table: {
  table_name?: string | null;
  error_message?: string | null;
  message?: string | null;
}): string {
  const candidates = [table.error_message, table.message]
    .map((value) => String(value || "").trim())
    .filter(Boolean);

  for (const candidate of candidates) {
    if (!isGenericTableFailureMessage(candidate)) {
      return candidate;
    }
  }
  return candidates[0] || "";
}

/** Per-table error for UI tooltips; uses batch/node fallback when row text is generic or empty. */
export function resolveTableRowDisplayError(
  table: {
    table_name?: string | null;
    error_message?: string | null;
    message?: string | null;
  },
  nodeOrBatchFallback?: string | null,
): string {
  const rowError = resolveTableDisplayError(table);
  if (rowError && !isGenericTableFailureMessage(rowError)) {
    return rowError;
  }
  const fallback = String(nodeOrBatchFallback || "").trim();
  if (fallback) {
    return fallback;
  }
  return rowError;
}

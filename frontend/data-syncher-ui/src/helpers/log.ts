export const getUiState = (
  uiState: string | undefined | null,
  status: string | undefined | null,
  message: string | undefined | null,
): string => {
  const lowerStatus = (status || "").toLowerCase();
  const lowerMessage = (message || "").toLowerCase();

  const hasInitiated =
    lowerMessage.includes("initiated") ||
    lowerMessage.includes("initiate") ||
    lowerMessage.includes("in progress");
  const hasCompleted =
    lowerMessage.includes("completed") ||
    lowerMessage.includes("successful") ||
    lowerMessage.includes("successfully");
  const hasError =
    lowerMessage.includes("error") || lowerMessage.includes("failed");

  // Terminal DB status wins over stale "Migration in progress" message text.
  if (["s", "success", "completed"].includes(lowerStatus)) {
    return "success";
  }
  if (["f", "e", "error", "failed"].includes(lowerStatus)) {
    return hasCompleted && !hasError ? "warning" : "error";
  }
  if (lowerStatus === "p") return "warning";

  // If status is missing but message says initiated/in progress, treat as in progress
  if (!lowerStatus && hasInitiated) return "in_progress";
  if (!lowerStatus && hasCompleted) return "success";

  // Fallback / Existing high priority overrides
  if (uiState) {
    const normalizedUiState = uiState.toLowerCase();
    if (["s", "success", "completed"].includes(normalizedUiState))
      return "success";
    if (["e", "error", "failed"].includes(normalizedUiState)) return "error";
    if (["i", "in_progress", "running"].includes(normalizedUiState))
      return "in_progress";
    if (["p", "w", "warning"].includes(normalizedUiState)) return "warning";
    if (["skipped"].includes(normalizedUiState)) return "skipped";
    return normalizedUiState;
  }

  // General catch-all for message content (e.g. paused/active)
  if (lowerMessage.includes("paused")) return "paused";
  if (lowerMessage.includes("active")) return "active";

  // Check for error/failure in message generally
  if (hasError && !hasInitiated && lowerStatus !== "s") {
    const isPartialSuccess =
      (lowerMessage.includes("successful") ||
        lowerMessage.includes("completed")) &&
      !lowerMessage.includes("0 tables") &&
      !lowerMessage.includes("0/0 successful") &&
      !lowerMessage.includes("0/1 successful") &&
      !lowerMessage.includes(": 0 successful") &&
      !lowerMessage.includes(" 0 successful");

    if (isPartialSuccess) {
      return "warning";
    }
    return "error";
  }

  // General status fallbacks
  if (["s", "success", "completed"].includes(lowerStatus)) return "success";
  if (["e", "error", "failed", "f"].includes(lowerStatus)) return "error";
  if (["i", "in_progress", "running"].includes(lowerStatus))
    return "in_progress";
  if (["w", "warning"].includes(lowerStatus)) return "warning";
  if (["skipped"].includes(lowerStatus)) return "skipped";

  if (
    lowerMessage.includes("drop and reload") ||
    lowerMessage.includes("migration in progress") ||
    lowerMessage.includes("reloading") ||
    lowerMessage.includes("delta refresh") ||
    lowerMessage.includes("schema refresh in progress") ||
    lowerMessage.includes("update schema in progress")
  )
    return "in_progress";

  if (
    lowerMessage.includes("completed successfully") ||
    lowerMessage.includes("updated fields") ||
    lowerMessage.includes("table selection updated") ||
    lowerMessage.includes("schema refresh completed") ||
    lowerMessage.includes("schema updated") ||
    lowerMessage.includes("update completed") ||
    lowerMessage.includes("fetch tables completed")
  )
    return "success";

  if (lowerMessage.includes("processing") || lowerMessage.includes("started"))
    return "in_progress";

  return "";
};

import { format } from "date-fns";

import { dateTimeFormat } from "@/constants/common";
import {
  isGenericTableFailureMessage,
  resolveTableDisplayError,
} from "@/helpers/migrationTableErrors";
import {
  type PipelineDetail,
  type PipelineRunDetail,
  type PipelineRunMode,
  type PipelineRunNodeDetail,
  type PipelineRunOverall,
} from "@/types/pipeline";

/** True when Validate has published a runnable topology (Run Now / schedule). */
export function pipelineHasPublishedRunnableGraph(
  pipeline:
    | Pick<PipelineDetail, "has_published_graph" | "published_graph">
    | null
    | undefined,
): boolean {
  if (!pipeline?.has_published_graph) return false;
  const nodeIds = pipeline.published_graph?.node_ids;
  return Array.isArray(nodeIds) && nodeIds.length > 0;
}

const MIGRATION_ACTIVE_STATUSES = new Set([
  "running",
  "in_progress",
  "completed",
  "failed",
  "timeout",
]);

const ACTIVE_NODE_STATUSES = new Set([
  "running",
  "in_progress",
  "failed",
  "timeout",
]);

export function resolvePipelineRunMode(
  run: Pick<PipelineRunDetail, "run_mode">,
): PipelineRunMode {
  return run.run_mode === "draft" ? "draft" : "published";
}

export function pipelineRunModeLabel(mode: PipelineRunMode): string {
  return mode === "published" ? "Published flow" : "Draft flow";
}

export function pipelineRunModeColor(mode: PipelineRunMode): string {
  return mode === "published" ? "green" : "orange";
}

export function nodeHasMigrationActivity(node: PipelineRunNodeDetail): boolean {
  if (
    node.migration_session_id !== null &&
    node.migration_session_id !== undefined
  ) {
    return true;
  }
  return MIGRATION_ACTIVE_STATUSES.has(String(node.status).toLowerCase());
}

/** Run is live but this task has not opened a migration session yet (worker prep / orchestrator). */
export function isPipelineNodeStartingUp(
  run: Pick<
    PipelineRunDetail,
    | "status"
    | "current_node_id"
    | "current_node_ids"
    | "waiting_for_flow_slot"
    | "overall"
  >,
  node: Pick<
    PipelineRunNodeDetail,
    "node_id" | "status" | "migration_session_id"
  >,
): boolean {
  if (run.waiting_for_flow_slot) return false;
  const runStatus = resolvePipelineRunStatus(run);
  if (runStatus !== "running" && runStatus !== "in_progress") return false;
  if (
    node.migration_session_id !== null &&
    node.migration_session_id !== undefined
  ) {
    return false;
  }
  const nodeStatus = String(node.status || "").toLowerCase();
  if (!["pending", "waiting", ""].includes(nodeStatus)) return false;

  const activeIds =
    run.current_node_ids && run.current_node_ids.length > 0
      ? run.current_node_ids
      : run.current_node_id !== null && run.current_node_id !== undefined
        ? [run.current_node_id]
        : [];

  if (activeIds.includes(node.node_id)) return true;
  // Single-wave runs often have empty current_node_ids until the session is created.
  return (run.overall?.nodes_running ?? 0) > 0 || activeIds.length === 0;
}

export function pipelineNodeStartupMessage(
  run: Pick<
    PipelineRunDetail,
    | "status"
    | "current_node_id"
    | "current_node_ids"
    | "waiting_for_flow_slot"
    | "overall"
  >,
  node: Pick<
    PipelineRunNodeDetail,
    "node_id" | "status" | "migration_session_id"
  >,
): string | null {
  if (run.waiting_for_flow_slot) {
    return "Queued — waiting for a free flow slot on your plan…";
  }
  if (isPipelineNodeStartingUp(run, node)) {
    return "Starting task — preparing workers and migration session…";
  }
  return null;
}

export function executionLogNodeBadge(
  runMode: PipelineRunMode,
  node: PipelineRunNodeDetail,
  run?: Pick<
    PipelineRunDetail,
    | "status"
    | "current_node_id"
    | "current_node_ids"
    | "waiting_for_flow_slot"
    | "overall"
  >,
): {
  label: string;
  colorPalette: string;
  variant: "subtle" | "outline";
} | null {
  if (runMode === "draft") {
    return { label: "Draft", colorPalette: "orange", variant: "subtle" };
  }
  if (run && isPipelineNodeStartingUp(run, node)) {
    return { label: "starting", colorPalette: "blue", variant: "subtle" };
  }
  if (!nodeHasMigrationActivity(node)) {
    return null;
  }
  const status = String(node.status).toLowerCase();
  return {
    label: String(node.status),
    colorPalette: pipelineStatusColor(node.status),
    variant: status === "skipped" ? "outline" : "subtle",
  };
}

export function pipelineStatusColor(status: string): string {
  const s = status.toLowerCase();
  if (s === "completed") return "green";
  if (s === "failed" || s === "timeout") return "red";
  if (s === "queued" || s === "waiting") return "purple";
  if (s === "running" || s === "in_progress") return "blue";
  if (s === "skipped") return "orange";
  return "gray";
}

export function pickDefaultNodeTab(
  nodes: PipelineRunNodeDetail[],
  currentNodeId: number | null,
  currentNodeIds?: number[] | null,
): string {
  const activeIds = currentNodeIds?.length
    ? currentNodeIds
    : currentNodeId !== null && currentNodeId !== undefined
      ? [currentNodeId]
      : [];
  if (activeIds.length) {
    return String(activeIds[0]);
  }
  const active =
    nodes.find((n) =>
      ACTIVE_NODE_STATUSES.has(String(n.status).toLowerCase()),
    ) ?? nodes[0];
  return active ? String(active.node_id) : "";
}

export { isGenericTableFailureMessage };

/** Prefer actionable per-table errors over stored aggregate node metadata. */
export function summarizePipelineTableErrors(
  tables:
    | ReadonlyArray<{
        table_name?: string;
        status?: string;
        status_icon?: string;
        error_message?: string | null;
        message?: string | null;
      }>
    | undefined,
): string | null {
  if (!tables?.length) return null;
  const parts: string[] = [];
  for (const table of tables) {
    const status = String(
      table.status_icon || table.status || "",
    ).toLowerCase();
    if (!["failed", "error", "f", "e"].includes(status)) continue;
    const raw = resolveTableDisplayError(table);
    if (!raw || isGenericTableFailureMessage(raw)) continue;
    const name = (table.table_name || "").trim();
    const line =
      name && !raw.toLowerCase().includes(name.toLowerCase())
        ? `${name}: ${raw}`
        : raw;
    if (!parts.includes(line)) parts.push(line);
  }
  if (!parts.length) return null;
  return parts.slice(0, 3).join("; ");
}

/** Node-level error, or run-level orchestrator error when the task never started. */
export function resolvePipelineNodeError(
  run: Pick<
    PipelineRunDetail,
    "status" | "error" | "overall" | "waiting_for_flow_slot"
  >,
  node: Pick<
    PipelineRunNodeDetail,
    "status" | "error" | "migration_session_id"
  >,
): string | null {
  const nodeError = node.error?.trim();
  if (nodeError) return nodeError;

  const runStatus = resolvePipelineRunStatus(run);
  const nodeStatus = String(node.status || "").toLowerCase();
  const runError = run.error?.trim();
  if (
    runError &&
    (runStatus === "failed" || runStatus === "timeout") &&
    (nodeStatus === "pending" || nodeStatus === "waiting") &&
    (node.migration_session_id === null ||
      node.migration_session_id === undefined)
  ) {
    return runError;
  }
  return null;
}

export function resolvePipelineRunStatus(
  run: Pick<PipelineRunDetail, "status" | "overall" | "waiting_for_flow_slot">,
): string {
  const top = (run.status || "").toLowerCase();
  const rolled = (run.overall?.status || "").toLowerCase();

  // While the backend run is still active, stay "running" until it reaches a
  // terminal status (retries may temporarily surface table error logs).
  if (top === "running" || top === "in_progress") {
    if (run.waiting_for_flow_slot) return "queued";
    if (rolled === "completed") return "completed";
    return top;
  }

  if (top === "failed" || top === "timeout") return top;
  if (rolled === "failed" || rolled === "timeout") return rolled;
  if (top === "completed") return "completed";
  if (rolled === "completed") return "completed";
  return top || rolled || "pending";
}

export function computeNodeProgress(overall: PipelineRunOverall): number {
  if (overall.nodes_total <= 0) return 0;
  const resolved =
    overall.nodes_completed +
    (overall.nodes_failed ?? 0) +
    (overall.nodes_skipped ?? 0);
  return Math.round((resolved / overall.nodes_total) * 100);
}

export function computeTableProgress(overall: PipelineRunOverall): number {
  if (overall.tables_total <= 0) return 0;
  const resolved = overall.tables_completed + (overall.tables_failed ?? 0);
  return Math.round((resolved / overall.tables_total) * 100);
}

/** Statuses that should keep Scheduling live-polling (no pipeline-run WebSocket). */
export function isLivePipelineRunStatus(
  status: string | undefined | null,
): boolean {
  const s = (status || "").toLowerCase();
  return (
    s === "running" || s === "in_progress" || s === "queued" || s === "waiting"
  );
}

/** Poll cadence while a run is queued/running. Kept short — there is no WS push. */
export const PIPELINE_RUN_LIVE_REFETCH_MS = 2000;

export function pipelineRunRefetchInterval(
  status: string | undefined,
): number | false {
  if (isLivePipelineRunStatus(status)) return PIPELINE_RUN_LIVE_REFETCH_MS;
  return false;
}

export function formatPipelineRunTimestamp(
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return null;
  return format(new Date(parsed), dateTimeFormat);
}

/** Shorter timestamp for inline header use (center of progress bar row). */
export function formatPipelineRunTimestampCompact(
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return null;
  return format(new Date(parsed), "MMM d, h:mm a");
}

export function formatElapsedDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export function computeRunDurationMs(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
  nowMs: number = Date.now(),
): number | null {
  if (!startedAt) return null;
  const startMs = Date.parse(startedAt);
  if (Number.isNaN(startMs)) return null;
  const endMs = finishedAt ? Date.parse(finishedAt) : nowMs;
  if (Number.isNaN(endMs)) return null;
  return Math.max(0, endMs - startMs);
}

/** Body returned by PipelineRunNowView (and similar) on failure. */
export type PipelineRunApiErrorBody = {
  error?: string;
  warning?: string;
  code?: string;
  errors?: string[];
  warnings?: string[];
};

/**
 * Axios interceptor rejects with `error.response.data` (not the full AxiosError).
 * Support both the unwrapped body and a classic `{ response: { data, status } }` shape.
 */
export function unwrapPipelineRunApiError(err: unknown): {
  data: PipelineRunApiErrorBody | undefined;
  status: number | undefined;
} {
  if (!err || typeof err !== "object") {
    return { data: undefined, status: undefined };
  }
  const asAxios = err as {
    response?: { status?: number; data?: PipelineRunApiErrorBody };
  };
  if (asAxios.response?.data && typeof asAxios.response.data === "object") {
    return {
      data: asAxios.response.data,
      status: asAxios.response.status,
    };
  }
  const body = err as PipelineRunApiErrorBody;
  if (
    body.code !== undefined ||
    body.error !== undefined ||
    body.warning !== undefined ||
    body.errors !== undefined
  ) {
    return { data: body, status: undefined };
  }
  return { data: undefined, status: undefined };
}

/** True when Run Now was blocked by the org concurrent-flow / worker-fleet plan cap. */
export function isPlanFlowCapError(
  data: PipelineRunApiErrorBody | undefined,
  status?: number,
): boolean {
  if (!data) return false;
  if (data.code === "plan_flow_cap") return true;
  if (status !== 409) return false;
  if (data.warning) return true;
  const errorText = data.error?.toLowerCase() ?? "";
  return errorText.includes("concurrent");
}

export const PLAN_FLOW_CAP_FALLBACK =
  "Your plan's concurrent flow limit is in use. Wait for a running flow to finish.";

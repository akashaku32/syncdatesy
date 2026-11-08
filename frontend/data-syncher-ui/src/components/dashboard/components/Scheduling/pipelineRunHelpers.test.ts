import { describe, expect, it } from "vitest";

import {
  computeNodeProgress,
  computeRunDurationMs,
  computeTableProgress,
  formatElapsedDuration,
  formatPipelineRunTimestamp,
  formatPipelineRunTimestampCompact,
  isPlanFlowCapError,
  pickDefaultNodeTab,
  pipelineHasPublishedRunnableGraph,
  pipelineRunRefetchInterval,
  pipelineStatusColor,
  resolvePipelineRunStatus,
  unwrapPipelineRunApiError,
} from "./pipelineRunHelpers";

describe("pipelineRunHelpers", () => {
  describe("pipelineStatusColor", () => {
    it.each([
      ["completed", "green"],
      ["failed", "red"],
      ["timeout", "red"],
      ["running", "blue"],
      ["in_progress", "blue"],
      ["pending", "gray"],
    ])("maps %s to %s", (status, color) => {
      expect(pipelineStatusColor(status)).toBe(color);
    });
  });

  describe("pickDefaultNodeTab", () => {
    const nodes = [
      { node_id: 5, status: "completed" },
      { node_id: 7, status: "running" },
    ] as Parameters<typeof pickDefaultNodeTab>[0];

    it("prefers current node when set", () => {
      expect(pickDefaultNodeTab(nodes, 5)).toBe("5");
    });

    it("prefers first current_node_ids entry when provided", () => {
      expect(pickDefaultNodeTab(nodes, null, [7, 5])).toBe("7");
    });

    it("selects first active node when current node is null", () => {
      expect(pickDefaultNodeTab(nodes, null)).toBe("7");
    });

    it("falls back to first node when none are active", () => {
      const completedOnly = [
        { node_id: 5, status: "completed" },
        { node_id: 7, status: "completed" },
      ] as Parameters<typeof pickDefaultNodeTab>[0];
      expect(pickDefaultNodeTab(completedOnly, null)).toBe("5");
    });

    it("returns empty string for empty node list", () => {
      expect(pickDefaultNodeTab([], null)).toBe("");
    });
  });

  describe("progress helpers", () => {
    it("computes node progress percentage", () => {
      expect(
        computeNodeProgress({
          nodes_total: 4,
          nodes_completed: 1,
          nodes_failed: 0,
          nodes_running: 1,
          tables_total: 0,
          tables_completed: 0,
          tables_failed: 0,
          status: "running",
        }),
      ).toBe(25);
    });

    it("counts failed nodes in node progress", () => {
      expect(
        computeNodeProgress({
          nodes_total: 4,
          nodes_completed: 1,
          nodes_failed: 1,
          nodes_running: 1,
          tables_total: 0,
          tables_completed: 0,
          tables_failed: 0,
          status: "running",
        }),
      ).toBe(50);
    });

    it("counts failed tables in table progress", () => {
      expect(
        computeTableProgress({
          nodes_total: 1,
          nodes_completed: 0,
          nodes_failed: 0,
          nodes_running: 1,
          tables_total: 2,
          tables_completed: 0,
          tables_failed: 1,
          status: "running",
        }),
      ).toBe(50);
    });

    it("computes table progress percentage", () => {
      expect(
        computeTableProgress({
          nodes_total: 1,
          nodes_completed: 0,
          nodes_failed: 0,
          nodes_running: 1,
          tables_total: 2,
          tables_completed: 1,
          tables_failed: 0,
          status: "running",
        }),
      ).toBe(50);
    });

    it("returns zero when totals are empty", () => {
      expect(
        computeNodeProgress({
          nodes_total: 0,
          nodes_completed: 0,
          nodes_failed: 0,
          nodes_running: 0,
          tables_total: 0,
          tables_completed: 0,
          tables_failed: 0,
          status: "running",
        }),
      ).toBe(0);
    });
  });

  describe("resolvePipelineRunStatus", () => {
    it("prefers explicit failed status", () => {
      expect(
        resolvePipelineRunStatus({
          status: "failed",
          overall: {
            nodes_total: 2,
            nodes_completed: 0,
            nodes_failed: 1,
            nodes_running: 0,
            tables_total: 2,
            tables_completed: 0,
            tables_failed: 1,
            status: "running",
          },
        }),
      ).toBe("failed");
    });

    it("stays running while backend run is active even if tables_failed > 0", () => {
      expect(
        resolvePipelineRunStatus({
          status: "running",
          overall: {
            nodes_total: 2,
            nodes_completed: 0,
            nodes_failed: 0,
            nodes_running: 2,
            tables_total: 5,
            tables_completed: 1,
            tables_failed: 1,
            status: "failed",
          },
        }),
      ).toBe("running");
    });
  });

  describe("pipelineRunRefetchInterval", () => {
    it("polls every 2s while running or queued", () => {
      expect(pipelineRunRefetchInterval("running")).toBe(2000);
      expect(pipelineRunRefetchInterval("queued")).toBe(2000);
      expect(pipelineRunRefetchInterval("in_progress")).toBe(2000);
    });

    it("stops polling when run is terminal", () => {
      expect(pipelineRunRefetchInterval("completed")).toBe(false);
      expect(pipelineRunRefetchInterval("failed")).toBe(false);
      expect(pipelineRunRefetchInterval(undefined)).toBe(false);
    });
  });

  describe("run timing helpers", () => {
    it("formats timestamps for display", () => {
      expect(formatPipelineRunTimestamp("2026-06-26T08:18:29.000Z")).toMatch(
        /Jun 26, 2026/,
      );
      expect(
        formatPipelineRunTimestampCompact("2026-06-26T08:18:29.000Z"),
      ).toMatch(/Jun 26/);
      expect(formatPipelineRunTimestamp(null)).toBeNull();
    });

    it("formats elapsed duration", () => {
      expect(formatElapsedDuration(90_000)).toBe("1m 30s");
      expect(formatElapsedDuration(3_661_000)).toBe("1h 1m 1s");
    });

    it("computes duration between start and end", () => {
      expect(
        computeRunDurationMs(
          "2026-01-01T00:00:00.000Z",
          "2026-01-01T00:05:10.000Z",
        ),
      ).toBe(310_000);
    });
  });

  describe("unwrapPipelineRunApiError / isPlanFlowCapError", () => {
    it("reads plan_flow_cap from interceptor-unwrapped body", () => {
      const body = {
        code: "plan_flow_cap",
        warning: "Your free plan allows 1 concurrent flow.",
        error: "Your free plan allows 1 concurrent flow.",
      };
      const { data, status } = unwrapPipelineRunApiError(body);
      expect(status).toBeUndefined();
      expect(data?.code).toBe("plan_flow_cap");
      expect(isPlanFlowCapError(data, status)).toBe(true);
    });

    it("reads plan_flow_cap from classic AxiosError shape", () => {
      const { data, status } = unwrapPipelineRunApiError({
        response: {
          status: 409,
          data: {
            code: "plan_flow_cap",
            warning: "limit reached",
          },
        },
      });
      expect(status).toBe(409);
      expect(isPlanFlowCapError(data, status)).toBe(true);
    });

    it("does not treat unrelated errors as plan cap", () => {
      const { data, status } = unwrapPipelineRunApiError({
        error: "Validate the Draft flow first.",
      });
      expect(isPlanFlowCapError(data, status)).toBe(false);
    });
  });

  describe("pipelineHasPublishedRunnableGraph", () => {
    it("requires published batch nodes", () => {
      expect(
        pipelineHasPublishedRunnableGraph({
          has_published_graph: true,
          published_graph: { node_ids: [1, 2], edges: [] },
        } as never),
      ).toBe(true);
      expect(
        pipelineHasPublishedRunnableGraph({
          has_published_graph: true,
          published_graph: { node_ids: [], edges: [] },
        } as never),
      ).toBe(false);
      expect(
        pipelineHasPublishedRunnableGraph({
          has_published_graph: false,
          published_graph: null,
        } as never),
      ).toBe(false);
    });
  });
});

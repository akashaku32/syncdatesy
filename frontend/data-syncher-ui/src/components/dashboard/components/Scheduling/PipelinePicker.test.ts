import { describe, expect, it } from "vitest";

import { type PipelineDetail } from "@/types/pipeline";

import {
  getPipelineListGroup,
  isPipelineFlowPaused,
  sortPipelinesForPicker,
} from "./PipelinePicker";

const pipeline = (
  id: number,
  name: string,
  status: "active" | "paused",
  latest_run_status: PipelineDetail["latest_run_status"] = null,
): PipelineDetail =>
  ({
    id,
    name,
    status,
    latest_run_status,
    schedule_type: "manual",
    time_frequency: "15",
    schedule_config: {},
    sync_start_date: null,
    notify_on_flow_finish: false,
    notification_email_group_ids: [],
    notification_emails: [],
    created_at: "",
    updated_at: "",
    nodes: [],
    edges: [],
  }) as PipelineDetail;

describe("sortPipelinesForPicker", () => {
  it("orders running, queued, active, then paused", () => {
    const sorted = sortPipelinesForPicker([
      pipeline(1, "Z paused", "paused"),
      pipeline(2, "B active", "active"),
      pipeline(3, "A running", "active", "running"),
      pipeline(4, "C active", "active"),
      pipeline(5, "Y paused running", "paused", "running"),
      pipeline(6, "Q queued", "active", "queued"),
    ]);

    expect(sorted.map((p) => p.id)).toEqual([3, 5, 6, 2, 4, 1]);
  });

  it("sorts alphabetically within the same group", () => {
    const sorted = sortPipelinesForPicker([
      pipeline(1, "Beta", "active"),
      pipeline(2, "Alpha", "active"),
    ]);

    expect(sorted.map((p) => p.name)).toEqual(["Alpha", "Beta"]);
  });
});

describe("isPipelineFlowPaused", () => {
  it("uses flow status and ignores schedule_paused", () => {
    expect(
      isPipelineFlowPaused({
        ...pipeline(1, "x", "active"),
        schedule_paused: true,
      }),
    ).toBe(false);
    expect(
      isPipelineFlowPaused({
        ...pipeline(2, "y", "paused"),
        schedule_paused: false,
      }),
    ).toBe(true);
    expect(
      isPipelineFlowPaused({
        ...pipeline(3, "z", "active"),
        schedule_paused: false,
      }),
    ).toBe(false);
  });
});

describe("getPipelineListGroup", () => {
  it("does not group a schedule-paused flow as paused when the flow is active", () => {
    expect(
      getPipelineListGroup({
        ...pipeline(1, "x", "active"),
        schedule_paused: true,
      }),
    ).toBe("active");
  });

  it("treats running execution as the running group regardless of schedule", () => {
    expect(getPipelineListGroup(pipeline(1, "x", "paused", "running"))).toBe(
      "running",
    );
    expect(getPipelineListGroup(pipeline(2, "x", "active"))).toBe("active");
    expect(getPipelineListGroup(pipeline(3, "x", "paused"))).toBe("paused");
    expect(getPipelineListGroup(pipeline(4, "x", "active", "queued"))).toBe(
      "queued",
    );
  });
});

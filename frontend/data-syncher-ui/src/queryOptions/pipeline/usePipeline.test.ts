import { describe, expect, it } from "vitest";

import { pipelineRunQueryKey, pipelineRunsQueryKey } from "./usePipeline";

describe("usePipeline query keys", () => {
  it("builds stable pipeline runs key", () => {
    expect(pipelineRunsQueryKey(1)).toEqual(["pipelineRuns", 1]);
    expect(pipelineRunsQueryKey(42)).toEqual(["pipelineRuns", 42]);
  });

  it("builds pipeline run detail key with run id", () => {
    expect(pipelineRunQueryKey(1, 83)).toEqual(["pipelineRun", 1, 83]);
    expect(pipelineRunQueryKey(1, null)).toEqual(["pipelineRun", 1, null]);
  });
});

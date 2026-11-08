import { describe, expect, it } from "vitest";

import { pipelineNodeChrome } from "./pipelineNodeStyles";

describe("pipelineNodeChrome", () => {
  it("uses a single dashed running outline without inner border or shadow", () => {
    const chrome = pipelineNodeChrome({ runStatus: "running" });
    expect(chrome.borderColor).toBe("transparent");
    expect(chrome.boxShadow).toBe("none");
  });

  it("keeps solid border for completed nodes", () => {
    const chrome = pipelineNodeChrome({ runStatus: "completed" });
    expect(chrome.borderColor).toBe("green.300");
    expect(chrome.borderStyle).toBe("solid");
  });
});

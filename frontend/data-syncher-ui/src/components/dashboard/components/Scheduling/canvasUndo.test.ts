import { describe, expect, it } from "vitest";

import { type PipelineEdgeDto } from "@/types/pipeline";

import {
  computeDeleteBridgePairs,
  snapshotNodeDeleteForUndo,
} from "./canvasUndo";

describe("computeDeleteBridgePairs / snapshotNodeDeleteForUndo", () => {
  it("bridges simple 1→N when within child limit", () => {
    const edges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 2 },
      { id: 2, pipeline_id: 1, from_node_id: 2, to_node_id: 3 },
      { id: 3, pipeline_id: 1, from_node_id: 2, to_node_id: 4 },
    ];

    const snap = snapshotNodeDeleteForUndo(2, edges);
    expect(snap.incoming).toEqual([1]);
    expect(snap.outgoing).toEqual([3, 4]);
    expect(snap.bridgesCreated).toEqual([
      { from_node_id: 1, to_node_id: 3 },
      { from_node_id: 1, to_node_id: 4 },
    ]);
  });

  it("skips multi×multi complicated remaps (e.g. exf-style)", () => {
    // 2 parents → deleted → 2 children
    const edges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 10, to_node_id: 2 },
      { id: 2, pipeline_id: 1, from_node_id: 11, to_node_id: 2 },
      { id: 3, pipeline_id: 1, from_node_id: 2, to_node_id: 3 },
      { id: 4, pipeline_id: 1, from_node_id: 2, to_node_id: 4 },
    ];

    expect(computeDeleteBridgePairs([10, 11], [3, 4], edges)).toEqual([]);
    expect(snapshotNodeDeleteForUndo(2, edges).bridgesCreated).toEqual([]);
  });

  it("skips when bridging would exceed max children", () => {
    // Parent already has 2 children (deleted + sibling); bridging 2 succs → 3
    const edges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 2 },
      { id: 2, pipeline_id: 1, from_node_id: 1, to_node_id: 5 },
      { id: 3, pipeline_id: 1, from_node_id: 2, to_node_id: 3 },
      { id: 4, pipeline_id: 1, from_node_id: 2, to_node_id: 4 },
    ];

    expect(snapshotNodeDeleteForUndo(2, edges).bridgesCreated).toEqual([]);
  });

  it("returns empty when node has no edges", () => {
    expect(snapshotNodeDeleteForUndo(99, [])).toEqual({
      incoming: [],
      outgoing: [],
      bridgesCreated: [],
    });
  });
});

import { describe, expect, it } from "vitest";

import { type PipelineEdgeDto, type PipelineNodeDto } from "@/types/pipeline";

import {
  buildAutoArrangeUpdates,
  computeRootNodeIds,
  getParentBatchName,
  layoutPipelineLR,
  resolvePipelineNodePosition,
} from "./pipelineLayout";

const startNode: PipelineNodeDto = {
  id: 1,
  pipeline_id: 1,
  node_kind: "start",
  connection_id: null,
  batch_id: null,
  batch_name: "",
  node_label: "Start",
  x: 0,
  y: 0,
  order_index: 0,
  execution_order: "parallel",
  schedule_type: "interval",
  time_frequency: "15",
  schedule_config: {},
  sync_start_date: null,
};

const nodes: PipelineNodeDto[] = [
  startNode,
  {
    id: 2,
    pipeline_id: 1,
    node_kind: "batch",
    connection_id: 10,
    batch_id: 100,
    batch_name: "root-batch",
    node_label: "Root",
    x: 0,
    y: 0,
    order_index: 0,
    execution_order: "parallel",
    schedule_type: "interval",
    time_frequency: "15",
    schedule_config: {},
    sync_start_date: null,
  },
  {
    id: 3,
    pipeline_id: 1,
    node_kind: "batch",
    connection_id: 11,
    batch_id: 101,
    batch_name: "child-batch",
    node_label: "Child",
    x: 0,
    y: 0,
    order_index: 1,
    execution_order: "parallel",
    schedule_type: "interval",
    time_frequency: "15",
    schedule_config: {},
    sync_start_date: null,
  },
];

const edges: PipelineEdgeDto[] = [
  { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 2 },
  { id: 2, pipeline_id: 1, from_node_id: 2, to_node_id: 3 },
];

describe("pipelineLayout", () => {
  it("computes entry nodes connected from Start", () => {
    expect(computeRootNodeIds(nodes, edges)).toEqual([2]);
  });

  it("returns empty roots for empty graph", () => {
    expect(computeRootNodeIds([], [])).toEqual([]);
  });

  it("resolves parent batch name from incoming edge", () => {
    expect(getParentBatchName(3, nodes, edges)).toBe("root-batch");
    expect(getParentBatchName(2, nodes, edges)).toBe("Start");
  });

  it("joins multiple parent batch names", () => {
    const multiParentNodes: PipelineNodeDto[] = [
      { ...nodes[1], id: 1, batch_name: "batch f", node_kind: "batch" },
      { ...nodes[1], id: 2, batch_name: "batch pr", node_kind: "batch" },
      { ...nodes[1], id: 3, batch_name: "batch s", node_kind: "batch" },
    ];
    const multiParentEdges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 3 },
      { id: 2, pipeline_id: 1, from_node_id: 2, to_node_id: 3 },
    ];
    expect(getParentBatchName(3, multiParentNodes, multiParentEdges)).toBe(
      "batch f, batch pr",
    );
  });

  it("aligns nodes to a fixed column grid", () => {
    const positions = layoutPipelineLR(nodes, edges);
    expect(positions.get(2)!.x).toBe(220);
    expect(positions.get(3)!.x).toBe(440);
    expect(positions.get(1)!.x).toBe(0);
  });

  it("lays out nodes left-to-right by dependency rank", () => {
    const positions = layoutPipelineLR(nodes, edges);
    expect(positions.get(1)?.x).toBe(0);
    expect(positions.get(3)?.x).toBeGreaterThan(positions.get(2)!.x);
  });

  it("centers a multi-child fan around the parent Y", () => {
    const batch = (id: number, name: string): PipelineNodeDto => ({
      ...nodes[1],
      id,
      batch_id: id * 10,
      batch_name: name,
      node_label: name,
    });
    const graphNodes: PipelineNodeDto[] = [
      startNode,
      batch(10, "parent"),
      batch(20, "c1"),
      batch(21, "c2"),
      batch(22, "c3"),
      batch(23, "c4"),
      batch(24, "c5"),
    ];
    const graphEdges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 10 },
      { id: 2, pipeline_id: 1, from_node_id: 10, to_node_id: 20 },
      { id: 3, pipeline_id: 1, from_node_id: 10, to_node_id: 21 },
      { id: 4, pipeline_id: 1, from_node_id: 10, to_node_id: 22 },
      { id: 5, pipeline_id: 1, from_node_id: 10, to_node_id: 23 },
      { id: 6, pipeline_id: 1, from_node_id: 10, to_node_id: 24 },
    ];
    const positions = layoutPipelineLR(graphNodes, graphEdges);
    const parentY = positions.get(10)!.y;
    const childYs = [20, 21, 22, 23, 24].map((id) => positions.get(id)!.y);
    const midChild = (Math.min(...childYs) + Math.max(...childYs)) / 2;
    // Parent sits near the middle of its children fan.
    expect(Math.abs(midChild - parentY)).toBeLessThan(48);
    expect(Math.min(...childYs)).toBeLessThan(parentY);
    expect(Math.max(...childYs)).toBeGreaterThan(parentY);
  });

  it("centers each sibling fan on its own parent when child counts differ", () => {
    const batch = (id: number, name: string): PipelineNodeDto => ({
      ...nodes[1],
      id,
      batch_id: id * 10,
      batch_name: name,
      node_label: name,
    });
    const graphNodes: PipelineNodeDto[] = [
      startNode,
      batch(10, "A"),
      batch(20, "B"),
      batch(11, "A-child"),
      batch(21, "B1"),
      batch(22, "B2"),
      batch(23, "B3"),
      batch(24, "B4"),
      batch(25, "B5"),
    ];
    const graphEdges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 10 },
      { id: 2, pipeline_id: 1, from_node_id: 1, to_node_id: 20 },
      { id: 3, pipeline_id: 1, from_node_id: 10, to_node_id: 11 },
      { id: 4, pipeline_id: 1, from_node_id: 20, to_node_id: 21 },
      { id: 5, pipeline_id: 1, from_node_id: 20, to_node_id: 22 },
      { id: 6, pipeline_id: 1, from_node_id: 20, to_node_id: 23 },
      { id: 7, pipeline_id: 1, from_node_id: 20, to_node_id: 24 },
      { id: 8, pipeline_id: 1, from_node_id: 20, to_node_id: 25 },
    ];
    const positions = layoutPipelineLR(graphNodes, graphEdges);
    const aY = positions.get(10)!.y;
    const bY = positions.get(20)!.y;
    const bChildYs = [21, 22, 23, 24, 25].map((id) => positions.get(id)!.y);
    const bMid = (Math.min(...bChildYs) + Math.max(...bChildYs)) / 2;

    expect(Math.abs(bMid - bY)).toBeLessThan(48);
    // Uneven fans stay on separate vertical centers (A not dragged to B's pack).
    expect(Math.abs(aY - bY)).toBeGreaterThan(40);
    // A's single child stays near A.
    expect(Math.abs(positions.get(11)!.y - aY)).toBeLessThan(48);
  });

  it("places Start on the vertical midline of the arranged graph", () => {
    const batch = (id: number, name: string): PipelineNodeDto => ({
      ...nodes[1],
      id,
      batch_id: id * 10,
      batch_name: name,
      node_label: name,
    });
    const graphNodes: PipelineNodeDto[] = [
      startNode,
      batch(10, "top"),
      batch(20, "bottom"),
      batch(11, "t1"),
      batch(12, "t2"),
      batch(13, "t3"),
      batch(21, "b1"),
    ];
    const graphEdges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 10 },
      { id: 2, pipeline_id: 1, from_node_id: 1, to_node_id: 20 },
      { id: 3, pipeline_id: 1, from_node_id: 10, to_node_id: 11 },
      { id: 4, pipeline_id: 1, from_node_id: 10, to_node_id: 12 },
      { id: 5, pipeline_id: 1, from_node_id: 10, to_node_id: 13 },
      { id: 6, pipeline_id: 1, from_node_id: 20, to_node_id: 21 },
    ];
    const positions = layoutPipelineLR(graphNodes, graphEdges);
    const ys = [...positions.values()].map((p) => p.y);
    const mid = (Math.min(...ys) + Math.max(...ys)) / 2;
    expect(Math.abs(positions.get(1)!.y - mid)).toBeLessThan(60);
  });

  it("orders columns by parent position to avoid crossed edges", () => {
    // Mirrors the last-column X in the flow UI: smaller-id sink on top by
    // id-sort would cross parents, but barycenter keeps edges parallel.
    const batch = (
      id: number,
      name: string,
      order_index: number,
    ): PipelineNodeDto => ({
      ...nodes[1],
      id,
      batch_id: id * 10,
      batch_name: name,
      node_label: name,
      order_index,
    });
    const graphNodes: PipelineNodeDto[] = [
      startNode,
      batch(10, "financ", 0),
      batch(20, "accnt", 1),
      batch(5, "master-data", 2), // smaller id — id-sort would put this first
      batch(30, "retl", 3),
    ];
    const graphEdges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 10 },
      { id: 2, pipeline_id: 1, from_node_id: 1, to_node_id: 20 },
      { id: 3, pipeline_id: 1, from_node_id: 10, to_node_id: 30 },
      { id: 4, pipeline_id: 1, from_node_id: 20, to_node_id: 5 },
    ];

    const positions = layoutPipelineLR(graphNodes, graphEdges);
    // Children follow parent order — no X crossing between the two chains.
    const financAboveAccnt = positions.get(10)!.y < positions.get(20)!.y;
    if (financAboveAccnt) {
      expect(positions.get(30)!.y).toBeLessThan(positions.get(5)!.y);
    } else {
      expect(positions.get(30)!.y).toBeGreaterThan(positions.get(5)!.y);
    }
  });

  it("keeps long-span edges off unrelated leaf nodes", () => {
    // Mirrors published Spectra flow: eco_res → Batch1 spans over the column
    // that holds leaf cust_invo_bat. Layout must not park the leaf on that wire.
    const batch = (id: number, name: string): PipelineNodeDto => ({
      ...nodes[1],
      id,
      batch_id: id * 10,
      batch_name: name,
      node_label: name,
    });
    const graphNodes: PipelineNodeDto[] = [
      startNode,
      batch(27, "bud_bat"),
      batch(28, "acc_bat"),
      batch(103, "dimension_bat"),
      batch(129, "Master Data Attribute"),
      batch(116, "eco_res"),
      batch(130, "dlv"),
      batch(121, "bud_account_batch"),
      batch(122, "RETL_BATCH"),
      batch(118, "cust_invo_bat"),
      batch(117, "inventdist"),
      batch(133, "Batch 1"),
    ];
    const graphEdges: PipelineEdgeDto[] = [
      { id: 1, pipeline_id: 1, from_node_id: 1, to_node_id: 27 },
      { id: 2, pipeline_id: 1, from_node_id: 1, to_node_id: 28 },
      { id: 3, pipeline_id: 1, from_node_id: 27, to_node_id: 103 },
      { id: 4, pipeline_id: 1, from_node_id: 28, to_node_id: 129 },
      { id: 5, pipeline_id: 1, from_node_id: 103, to_node_id: 116 },
      { id: 6, pipeline_id: 1, from_node_id: 103, to_node_id: 130 },
      { id: 7, pipeline_id: 1, from_node_id: 129, to_node_id: 121 },
      { id: 8, pipeline_id: 1, from_node_id: 129, to_node_id: 122 },
      { id: 9, pipeline_id: 1, from_node_id: 130, to_node_id: 118 },
      { id: 10, pipeline_id: 1, from_node_id: 121, to_node_id: 117 },
      { id: 11, pipeline_id: 1, from_node_id: 116, to_node_id: 133 },
      { id: 12, pipeline_id: 1, from_node_id: 117, to_node_id: 133 },
    ];
    const positions = layoutPipelineLR(graphNodes, graphEdges);

    const ecoY = positions.get(116)!.y;
    const batch1Y = positions.get(133)!.y;
    const custY = positions.get(118)!.y;
    const inventY = positions.get(117)!.y;

    // Long edge corridor from eco_res toward Batch 1 — leaf must not sit between.
    const lo = Math.min(ecoY, batch1Y);
    const hi = Math.max(ecoY, batch1Y) + 72;
    const custCenter = custY + 36;
    const inCorridor = custCenter >= lo && custCenter <= hi;
    expect(inCorridor).toBe(false);

    // Batch 1 should sit between its two real parents vertically.
    const parentLo = Math.min(ecoY, inventY);
    const parentHi = Math.max(ecoY, inventY);
    expect(batch1Y).toBeGreaterThanOrEqual(parentLo - 48);
    expect(batch1Y).toBeLessThanOrEqual(parentHi + 48);
  });

  it("assigns positions for disconnected nodes", () => {
    const solo: PipelineNodeDto[] = [{ ...nodes[0], id: 99 }];
    const positions = layoutPipelineLR(solo, []);
    // Start is anchored at START_ANCHOR_Y (80).
    expect(positions.get(99)).toEqual({ x: 0, y: 80 });
  });

  it("prefers saved coordinates over computed layout", () => {
    expect(
      resolvePipelineNodePosition({ x: 120, y: 80 }, { x: 0, y: 0 }),
    ).toEqual({
      x: 120,
      y: 80,
    });
    expect(
      resolvePipelineNodePosition({ x: 0, y: 0 }, { x: 300, y: 140 }),
    ).toEqual({
      x: 300,
      y: 140,
    });
  });

  it("buildAutoArrangeUpdates returns positions for all nodes", () => {
    const updates = buildAutoArrangeUpdates(nodes, edges);
    expect(updates.map((u) => u.nodeId).sort()).toEqual([1, 2, 3]);
    expect(
      updates.every((u) => Number.isInteger(u.x) && Number.isInteger(u.y)),
    ).toBe(true);
  });
});

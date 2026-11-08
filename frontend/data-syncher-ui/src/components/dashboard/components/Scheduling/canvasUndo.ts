import {
  type AddPipelineNodePayload,
  type PipelineEdgeDto,
} from "@/types/pipeline";

export type PipelineEdgePair = {
  from_node_id: number;
  to_node_id: number;
};

export type CanvasUndoEntry =
  | {
      type: "positions";
      positions: Array<{ nodeId: number; x: number; y: number }>;
    }
  | {
      type: "removeNode";
      nodeId: number;
    }
  | {
      type: "readdNode";
      payload: AddPipelineNodePayload;
      /** Direct predecessors before delete (to restore after re-add). */
      incoming?: number[];
      /** Direct successors before delete (to restore after re-add). */
      outgoing?: number[];
      /**
       * Bridge edges created by a *simple* delete remap (pred→succ).
       * Empty when delete skipped bridging (complicated graph).
       */
      bridgesCreated?: PipelineEdgePair[];
    }
  | {
      type: "removeEdge";
      edgeId: number;
    }
  | {
      type: "readdEdge";
      from_node_id: number;
      to_node_id: number;
    };

/**
 * Mirror of backend compute_delete_bridge_pairs:
 * bridge only for simple 1→1 / 1→N / N→1 remaps that stay within max children.
 * Multi×multi (both > 1) → no bridges.
 */
export function computeDeleteBridgePairs(
  incoming: number[],
  outgoing: number[],
  edges: PipelineEdgeDto[],
  maxChildrenPerNode = 2,
): PipelineEdgePair[] {
  if (incoming.length === 0 || outgoing.length === 0) return [];
  if (incoming.length > 1 && outgoing.length > 1) return [];

  const existing = new Set(
    edges.map((e) => `${e.from_node_id}->${e.to_node_id}`),
  );
  const parentOutCounts = new Map<number, number>();
  for (const e of edges) {
    parentOutCounts.set(
      e.from_node_id,
      (parentOutCounts.get(e.from_node_id) ?? 0) + 1,
    );
  }

  const proposed: PipelineEdgePair[] = [];
  const newByParent = new Map<number, number>();
  for (const from_node_id of incoming) {
    for (const to_node_id of outgoing) {
      if (from_node_id === to_node_id) continue;
      const key = `${from_node_id}->${to_node_id}`;
      if (existing.has(key)) continue;
      proposed.push({ from_node_id, to_node_id });
      newByParent.set(from_node_id, (newByParent.get(from_node_id) ?? 0) + 1);
    }
  }

  for (const from_node_id of incoming) {
    const currentOut = parentOutCounts.get(from_node_id) ?? 0;
    const finalOut = currentOut - 1 + (newByParent.get(from_node_id) ?? 0);
    if (finalOut > maxChildrenPerNode) return [];
  }

  return proposed;
}

/** Snapshot preds/succs and any simple bridges the server will create. */
export function snapshotNodeDeleteForUndo(
  nodeId: number,
  edges: PipelineEdgeDto[],
  maxChildrenPerNode = 2,
): {
  incoming: number[];
  outgoing: number[];
  bridgesCreated: PipelineEdgePair[];
} {
  const incoming = edges
    .filter((e) => e.to_node_id === nodeId)
    .map((e) => e.from_node_id);
  const outgoing = edges
    .filter((e) => e.from_node_id === nodeId)
    .map((e) => e.to_node_id);

  return {
    incoming,
    outgoing,
    bridgesCreated: computeDeleteBridgePairs(
      incoming,
      outgoing,
      edges,
      maxChildrenPerNode,
    ),
  };
}

export const MAX_CANVAS_UNDO = 20;

export function pushCanvasUndo(
  stack: CanvasUndoEntry[],
  entry: CanvasUndoEntry,
): CanvasUndoEntry[] {
  return [...stack, entry].slice(-MAX_CANVAS_UNDO);
}

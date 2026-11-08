import { type PipelineEdgeDto, type PipelineNodeDto } from "@/types/pipeline";

/** Horizontal pitch between dependency columns (fixed grid). */
const COLUMN_PITCH = 220;
/** Vertical pitch between nodes in the same column. */
const ROW_PITCH = 96;
const START_ANCHOR_Y = 80;
const BATCH_NODE_HEIGHT = 72;
const START_NODE_HEIGHT = 88;
/** Extra vertical gap so long-span edges have a clearer corridor. */
const LONG_EDGE_CORRIDOR_GAP = 24;

export function isStartNode(node: PipelineNodeDto): boolean {
  return node.node_kind === "start";
}

export function batchNodesOnly(nodes: PipelineNodeDto[]): PipelineNodeDto[] {
  return nodes.filter((n) => !isStartNode(n));
}

function nodeHeight(nodeId: number, startId?: number): number {
  return nodeId === startId ? START_NODE_HEIGHT : BATCH_NODE_HEIGHT;
}

function columnX(rank: number): number {
  return rank * COLUMN_PITCH;
}

function computeNodeRanks(
  nodeIds: number[],
  edges: PipelineEdgeDto[],
  startId?: number,
): Map<number, number> {
  const parents = new Map<number, number[]>();
  for (const id of nodeIds) parents.set(id, []);
  for (const edge of edges) {
    if (!parents.has(edge.to_node_id)) continue;
    const list = parents.get(edge.to_node_id)!;
    list.push(edge.from_node_id);
  }

  const rank = new Map<number, number>();
  if (startId !== undefined) rank.set(startId, 0);

  let changed = true;
  while (changed) {
    changed = false;
    for (const id of nodeIds) {
      const ps = parents.get(id) ?? [];
      if (!ps.length) {
        if (!rank.has(id) && id !== startId) {
          rank.set(id, 1);
          changed = true;
        }
        continue;
      }
      const next = Math.max(...ps.map((p) => rank.get(p) ?? 0)) + 1;
      if ((rank.get(id) ?? 0) < next) {
        rank.set(id, next);
        changed = true;
      }
    }
  }

  for (const id of nodeIds) {
    if (!rank.has(id)) rank.set(id, 1);
  }
  return rank;
}

/** Stable fingerprint for pipeline graph structure (invalidates run-after-validate when flow changes). */
export function pipelineGraphFingerprint(
  nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
): string {
  const batchPart = batchNodesOnly(nodes)
    .map((n) => `${n.id}:${n.batch_id ?? 0}`)
    .sort()
    .join(",");
  const edgePart = edges
    .map((e) => `${e.from_node_id}->${e.to_node_id}`)
    .sort()
    .join(",");
  return `${batchPart}|${edgePart}`;
}

export function computeRootNodeIds(
  nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
): number[] {
  const batches = batchNodesOnly(nodes);
  if (!batches.length) return [];
  const start = nodes.find(isStartNode);
  if (start) {
    return edges
      .filter((e) => e.from_node_id === start.id)
      .map((e) => e.to_node_id)
      .filter((id) => batches.some((n) => n.id === id));
  }
  const nodeIds = new Set(batches.map((n) => n.id));
  const hasIncoming = new Set(
    edges.filter((e) => nodeIds.has(e.to_node_id)).map((e) => e.to_node_id),
  );
  return batches.filter((n) => !hasIncoming.has(n.id)).map((n) => n.id);
}

export function computeDisconnectedBatchIds(
  nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
): number[] {
  const start = nodes.find(isStartNode);
  const batches = batchNodesOnly(nodes);
  if (!start || !batches.length) return batches.map((n) => n.id);

  const adj = new Map<number, number[]>();
  for (const edge of edges) {
    const list = adj.get(edge.from_node_id) ?? [];
    list.push(edge.to_node_id);
    adj.set(edge.from_node_id, list);
  }

  const reachable = new Set<number>();
  const queue = [start.id];
  const seen = new Set<number>([start.id]);
  while (queue.length) {
    const cur = queue.shift()!;
    for (const nxt of adj.get(cur) ?? []) {
      if (!seen.has(nxt)) {
        seen.add(nxt);
        reachable.add(nxt);
        queue.push(nxt);
      }
    }
  }
  return batches.filter((n) => !reachable.has(n.id)).map((n) => n.id);
}

/** User-facing validation error when batches are not reachable from Start. */
export function formatDisconnectedBatchValidationError(
  nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
): string | null {
  const disconnectedIds = computeDisconnectedBatchIds(nodes, edges);
  if (!disconnectedIds.length) return null;

  const names = disconnectedIds.map((id) => {
    const node = nodes.find((n) => n.id === id);
    return node?.batch_name || node?.node_label || `Node ${id}`;
  });
  const count = disconnectedIds.length;
  const nameSuffix = names.length ? ` (${names.join(", ")})` : "";
  return `${count} batch${count === 1 ? "" : "es"} not connected to Start${nameSuffix} — connect them before running.`;
}

export function getParentBatchName(
  nodeId: number,
  nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
): string | null {
  const incoming = edges.filter((e) => e.to_node_id === nodeId);
  if (!incoming.length) return null;
  const parentNames = incoming
    .map((edge) => {
      const parentNode = nodes.find((n) => n.id === edge.from_node_id);
      if (!parentNode) return null;
      if (isStartNode(parentNode)) return "Start";
      return parentNode.batch_name || parentNode.node_label || null;
    })
    .filter((name): name is string => Boolean(name));
  if (!parentNames.length) return null;
  return parentNames.join(", ");
}

/**
 * Standard layered grid layout:
 * - Fixed column X per dependency rank
 * - Children placed from median of all parents (not a single "primary")
 * - Long-span edges get virtual corridor slots so unrelated leaves don't sit
 *   on the wire (avoids eco_res→Batch1 looking like cust_invo→Batch1)
 * - Crossing minimization sweeps between adjacent columns
 */
export function layoutPipelineLR(
  nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
): Map<number, { x: number; y: number }> {
  const positions = new Map<number, { x: number; y: number }>();
  if (!nodes.length) return positions;

  const start = nodes.find(isStartNode);
  const startId = start?.id;
  const nodeIds = nodes.map((n) => n.id);
  const nodeIdSet = new Set(nodeIds);

  const parentsOf = new Map<number, number[]>();
  const childrenOf = new Map<number, number[]>();
  for (const id of nodeIds) {
    parentsOf.set(id, []);
    childrenOf.set(id, []);
  }
  for (const edge of edges) {
    if (!parentsOf.has(edge.to_node_id) || !childrenOf.has(edge.from_node_id)) {
      continue;
    }
    parentsOf.get(edge.to_node_id)!.push(edge.from_node_id);
    childrenOf.get(edge.from_node_id)!.push(edge.to_node_id);
  }

  const rank = computeNodeRanks(nodeIds, edges, startId);
  const byRank = new Map<number, number[]>();
  for (const node of nodes) {
    const r = rank.get(node.id) ?? 1;
    const list = byRank.get(r) ?? [];
    list.push(node.id);
    byRank.set(r, list);
  }

  // Long-span edges (rank gap > 1): virtual corridor ids at each skipped rank.
  type Corridor = {
    key: string;
    fromId: number;
    toId: number;
    atRank: number;
  };
  const corridorsByRank = new Map<number, Corridor[]>();
  let corridorSeq = 0;
  for (const edge of edges) {
    if (!nodeIdSet.has(edge.from_node_id) || !nodeIdSet.has(edge.to_node_id)) {
      continue;
    }
    const fromRank = rank.get(edge.from_node_id);
    const toRank = rank.get(edge.to_node_id);
    if (fromRank === undefined || toRank === undefined) continue;
    if (toRank - fromRank <= 1) continue;
    for (let r = fromRank + 1; r < toRank; r++) {
      const key = `corridor:${edge.from_node_id}->${edge.to_node_id}@${r}:${corridorSeq++}`;
      const list = corridorsByRank.get(r) ?? [];
      list.push({
        key,
        fromId: edge.from_node_id,
        toId: edge.to_node_id,
        atRank: r,
      });
      corridorsByRank.set(r, list);
    }
  }

  const orderIndex = new Map<number, number>();
  if (startId !== undefined) {
    orderIndex.set(startId, 0);
    positions.set(startId, { x: 0, y: START_ANCHOR_Y });
  }

  const sortedRanks = [...byRank.keys()].sort((a, b) => a - b);
  const columnOrder = new Map<number, number[]>();
  /** Full slot order (nodes + corridor keys) per rank — preserves long-edge lanes. */
  const columnSlots = new Map<
    number,
    Array<
      | { kind: "node"; id: number }
      | { kind: "corridor"; key: string; fromId: number; toId: number }
    >
  >();

  for (const r of sortedRanks) {
    if (r === 0 && startId !== undefined) {
      columnOrder.set(0, [startId]);
      columnSlots.set(0, [{ kind: "node", id: startId }]);
      continue;
    }

    const ids = (byRank.get(r) ?? []).filter((id) => id !== startId);
    const corridors = corridorsByRank.get(r) ?? [];

    type Slot =
      | { kind: "node"; id: number }
      | { kind: "corridor"; key: string; fromId: number; toId: number };

    const slots: Slot[] = [
      ...ids.map((id): Slot => ({ kind: "node", id })),
      ...corridors.map(
        (c): Slot => ({
          kind: "corridor",
          key: c.key,
          fromId: c.fromId,
          toId: c.toId,
        }),
      ),
    ];

    const slotBarycenter = (slot: Slot): number => {
      if (slot.kind === "node") {
        const parents = parentsOf.get(slot.id) ?? [];
        const known = parents
          .map((pid) => orderIndex.get(pid))
          .filter((idx): idx is number => idx !== undefined);
        if (known.length) {
          return known.reduce((sum, idx) => sum + idx, 0) / known.length;
        }
        return Number.POSITIVE_INFINITY;
      }
      const fromIdx = orderIndex.get(slot.fromId);
      if (fromIdx !== undefined) return fromIdx;
      return Number.POSITIVE_INFINITY;
    };

    const slotParentY = (slot: Slot): number => {
      if (slot.kind === "node") {
        const parents = parentsOf.get(slot.id) ?? [];
        const centers = parents
          .map((pid) => {
            const pos = positions.get(pid);
            if (!pos) return null;
            return pos.y + nodeHeight(pid, startId) / 2;
          })
          .filter((y): y is number => y !== null);
        if (centers.length) {
          return centers.reduce((sum, y) => sum + y, 0) / centers.length;
        }
        return Number.POSITIVE_INFINITY;
      }
      const fromPos = positions.get(slot.fromId);
      if (fromPos) {
        return fromPos.y + nodeHeight(slot.fromId, startId) / 2;
      }
      return Number.POSITIVE_INFINITY;
    };

    slots.sort((a, b) => {
      const ay = slotParentY(a);
      const by = slotParentY(b);
      if (ay !== by) return ay - by;
      const ab = slotBarycenter(a);
      const bb = slotBarycenter(b);
      if (ab !== bb) return ab - bb;
      if (a.kind !== b.kind) return a.kind === "corridor" ? -1 : 1;
      if (a.kind === "node" && b.kind === "node") return a.id - b.id;
      if (a.kind === "corridor" && b.kind === "corridor") {
        return a.key.localeCompare(b.key);
      }
      return 0;
    });

    for (let pass = 0; pass < 2; pass++) {
      slots.sort((a, b) => {
        const ab = slotBarycenter(a);
        const bb = slotBarycenter(b);
        if (ab !== bb) return ab - bb;
        return slotParentY(a) - slotParentY(b);
      });
    }

    const orderedIds = slots
      .filter((s): s is Extract<Slot, { kind: "node" }> => s.kind === "node")
      .map((s) => s.id);
    columnOrder.set(r, orderedIds);
    columnSlots.set(r, slots);
    const colX = columnX(r);

    const desiredYs: number[] = [];
    for (const slot of slots) {
      if (slot.kind === "corridor") {
        const fromPos = positions.get(slot.fromId);
        desiredYs.push(
          fromPos
            ? fromPos.y +
                nodeHeight(slot.fromId, startId) / 2 -
                BATCH_NODE_HEIGHT / 2
            : START_ANCHOR_Y,
        );
        continue;
      }
      const parents = parentsOf.get(slot.id) ?? [];
      const parentCenters = parents
        .map((pid) => {
          const pos = positions.get(pid);
          if (!pos) return null;
          return pos.y + nodeHeight(pid, startId) / 2;
        })
        .filter((y): y is number => y !== null);
      if (parentCenters.length) {
        desiredYs.push(median(parentCenters) - BATCH_NODE_HEIGHT / 2);
      } else {
        desiredYs.push(START_ANCHOR_Y);
      }
    }

    const minGaps = slots.map((slot, index) => {
      if (index === 0) return 0;
      const prev = slots[index - 1];
      const cur = slot;
      if (prev.kind === "corridor" || cur.kind === "corridor") {
        return ROW_PITCH + LONG_EDGE_CORRIDOR_GAP;
      }
      return ROW_PITCH;
    });

    const resolvedYs = resolveColumnGapsWithMins(desiredYs, minGaps);

    slots.forEach((slot, index) => {
      if (slot.kind === "node") {
        orderIndex.set(slot.id, index);
        positions.set(slot.id, { x: colX, y: resolvedYs[index] });
      }
    });
  }

  // R→L: pull parents toward median of their children, but keep corridor lanes
  // so long-span wires stay clear of unrelated leaves.
  for (let ri = sortedRanks.length - 1; ri >= 0; ri--) {
    const r = sortedRanks[ri];
    if (r === 0) continue;
    const slots = columnSlots.get(r);
    const orderedIds = columnOrder.get(r) ?? [];
    if (!orderedIds.length) continue;

    if (!slots || slots.length === orderedIds.length) {
      // No corridors in this column — simple pack.
      const desiredYs = orderedIds.map((id) => {
        const children = (childrenOf.get(id) ?? []).filter((cid) =>
          positions.has(cid),
        );
        if (!children.length) return positions.get(id)!.y;
        const childCenters = children.map(
          (cid) => positions.get(cid)!.y + nodeHeight(cid, startId) / 2,
        );
        return median(childCenters) - BATCH_NODE_HEIGHT / 2;
      });
      const resolvedYs = resolveColumnGaps(desiredYs);
      const colX = columnX(r);
      orderedIds.forEach((id, index) => {
        positions.set(id, { x: colX, y: resolvedYs[index] });
      });
      continue;
    }

    const desiredYs = slots.map((slot) => {
      if (slot.kind === "corridor") {
        const fromPos = positions.get(slot.fromId);
        const toPos = positions.get(slot.toId);
        if (fromPos && toPos) {
          // Interpolate corridor Y between endpoints for this rank.
          const fromRank = rank.get(slot.fromId) ?? r - 1;
          const toRank = rank.get(slot.toId) ?? r + 1;
          const t =
            toRank === fromRank ? 0.5 : (r - fromRank) / (toRank - fromRank);
          const fromC = fromPos.y + nodeHeight(slot.fromId, startId) / 2;
          const toC = toPos.y + nodeHeight(slot.toId, startId) / 2;
          return fromC + (toC - fromC) * t - BATCH_NODE_HEIGHT / 2;
        }
        if (fromPos) {
          return (
            fromPos.y +
            nodeHeight(slot.fromId, startId) / 2 -
            BATCH_NODE_HEIGHT / 2
          );
        }
        return START_ANCHOR_Y;
      }
      const children = (childrenOf.get(slot.id) ?? []).filter((cid) =>
        positions.has(cid),
      );
      if (!children.length) return positions.get(slot.id)!.y;
      const childCenters = children.map(
        (cid) => positions.get(cid)!.y + nodeHeight(cid, startId) / 2,
      );
      return median(childCenters) - BATCH_NODE_HEIGHT / 2;
    });

    const minGaps = slots.map((slot, index) => {
      if (index === 0) return 0;
      const prev = slots[index - 1];
      if (prev.kind === "corridor" || slot.kind === "corridor") {
        return ROW_PITCH + LONG_EDGE_CORRIDOR_GAP;
      }
      return ROW_PITCH;
    });
    const resolvedYs = resolveColumnGapsWithMins(desiredYs, minGaps);
    const colX = columnX(r);
    slots.forEach((slot, index) => {
      if (slot.kind === "node") {
        positions.set(slot.id, { x: colX, y: resolvedYs[index] });
      }
    });
  }

  // Final pass: push any node whose center still sits on a long-span corridor.
  clearLongEdgeCorridors(positions, edges, rank, startId);

  // Keep Start fixed; shift the rest so Start is the graph vertical midline.
  if (startId !== undefined && positions.has(startId)) {
    positions.set(startId, { x: 0, y: START_ANCHOR_Y });
    let minY = Infinity;
    let maxY = -Infinity;
    for (const [id, pos] of positions) {
      minY = Math.min(minY, pos.y);
      maxY = Math.max(maxY, pos.y + nodeHeight(id, startId));
    }
    const graphMidY = (minY + maxY) / 2;
    const startCenterY = START_ANCHOR_Y + START_NODE_HEIGHT / 2;
    const shiftY = startCenterY - graphMidY;
    if (shiftY !== 0) {
      for (const [id, pos] of positions) {
        if (id === startId) continue;
        positions.set(id, { x: pos.x, y: pos.y + shiftY });
      }
    }
  }

  return positions;
}

/**
 * If a real node center lies on a long-span edge's vertical band in an
 * intermediate column, nudge it just outside that band.
 */
function clearLongEdgeCorridors(
  positions: Map<number, { x: number; y: number }>,
  edges: PipelineEdgeDto[],
  rank: Map<number, number>,
  startId: number | undefined,
): void {
  const byRank = new Map<number, number[]>();
  for (const [id, pos] of positions) {
    void pos;
    const r = rank.get(id);
    if (r === undefined) continue;
    const list = byRank.get(r) ?? [];
    list.push(id);
    byRank.set(r, list);
  }

  for (const edge of edges) {
    const fromRank = rank.get(edge.from_node_id);
    const toRank = rank.get(edge.to_node_id);
    const fromPos = positions.get(edge.from_node_id);
    const toPos = positions.get(edge.to_node_id);
    if (
      fromRank === undefined ||
      toRank === undefined ||
      !fromPos ||
      !toPos ||
      toRank - fromRank <= 1
    ) {
      continue;
    }
    const fromC = fromPos.y + nodeHeight(edge.from_node_id, startId) / 2;
    const toC = toPos.y + nodeHeight(edge.to_node_id, startId) / 2;

    for (let r = fromRank + 1; r < toRank; r++) {
      const ids = byRank.get(r) ?? [];
      const t = (r - fromRank) / (toRank - fromRank);
      const corridorY = fromC + (toC - fromC) * t;
      const bandLo = corridorY - BATCH_NODE_HEIGHT / 2 - LONG_EDGE_CORRIDOR_GAP;
      const bandHi = corridorY + BATCH_NODE_HEIGHT / 2 + LONG_EDGE_CORRIDOR_GAP;

      for (const id of ids) {
        if (id === edge.from_node_id || id === edge.to_node_id) continue;
        // Real parents of the sink belong near the join — don't shove them off.
        const isParentOfSink = edges.some(
          (e) => e.from_node_id === id && e.to_node_id === edge.to_node_id,
        );
        if (isParentOfSink) continue;

        const pos = positions.get(id);
        if (!pos) continue;
        const center = pos.y + nodeHeight(id, startId) / 2;
        if (center < bandLo || center > bandHi) continue;

        // Push leaf away from the corridor, preferring the side opposite the
        // sink-parent cluster when one exists in this column.
        const parentOfSinkYs = ids
          .filter((nid) =>
            edges.some(
              (e) => e.from_node_id === nid && e.to_node_id === edge.to_node_id,
            ),
          )
          .map((nid) => {
            const p = positions.get(nid)!;
            return p.y + nodeHeight(nid, startId) / 2;
          });
        const parentCluster =
          parentOfSinkYs.length > 0
            ? parentOfSinkYs.reduce((s, y) => s + y, 0) / parentOfSinkYs.length
            : corridorY;

        if (parentCluster >= corridorY) {
          // Parents sit below / on corridor — push leaf above.
          positions.set(id, {
            x: pos.x,
            y: bandLo - BATCH_NODE_HEIGHT - LONG_EDGE_CORRIDOR_GAP,
          });
        } else {
          positions.set(id, {
            x: pos.x,
            y: bandHi + LONG_EDGE_CORRIDOR_GAP,
          });
        }
      }

      // Re-pack the column after nudges so nodes don't overlap.
      const packed = (byRank.get(r) ?? [])
        .map((id) => ({ id, y: positions.get(id)!.y }))
        .sort((a, b) => a.y - b.y);
      if (packed.length <= 1) continue;
      const desired = packed.map((p) => p.y);
      const resolved = resolveColumnGaps(desired);
      const colX = columnX(r);
      packed.forEach((p, index) => {
        positions.set(p.id, { x: colX, y: resolved[index] });
      });
    }
  }
}

function resolveColumnGaps(ys: number[]): number[] {
  return resolveColumnGapsWithMins(
    ys,
    ys.map((_, i) => (i === 0 ? 0 : ROW_PITCH)),
  );
}

function resolveColumnGapsWithMins(ys: number[], minGaps: number[]): number[] {
  if (ys.length <= 1) return [...ys];
  const out = [...ys];
  for (let i = 1; i < out.length; i++) {
    const gap = minGaps[i] ?? ROW_PITCH;
    out[i] = Math.max(out[i], out[i - 1] + gap);
  }
  // Re-center the packed block on the original mid to avoid downward bias.
  const origMid = (ys[0] + ys[ys.length - 1]) / 2;
  const newMid = (out[0] + out[out.length - 1]) / 2;
  const shift = origMid - newMid;
  if (shift !== 0) {
    for (let i = 0; i < out.length; i++) out[i] += shift;
    for (let i = 1; i < out.length; i++) {
      const gap = minGaps[i] ?? ROW_PITCH;
      out[i] = Math.max(out[i], out[i - 1] + gap);
    }
  }
  return out;
}

function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[mid - 1] + sorted[mid]) / 2;
  }
  return sorted[mid];
}

/** Prefer saved canvas coordinates; use DAG layout when position was never set. */
export function resolvePipelineNodePosition(
  node: { x: number; y: number },
  layoutPos?: { x: number; y: number },
  options?: { preferLayout?: boolean },
): { x: number; y: number } {
  if (options?.preferLayout && layoutPos) {
    return layoutPos;
  }
  if (node.x !== 0 || node.y !== 0) {
    return { x: node.x, y: node.y };
  }
  return layoutPos ?? { x: node.x, y: node.y };
}

export type PipelineNodePositionUpdate = {
  nodeId: number;
  x: number;
  y: number;
};

/** Compute left-to-right positions for every node on the canvas. */
export function buildAutoArrangeUpdates(
  nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
): PipelineNodePositionUpdate[] {
  const positions = layoutPipelineLR(nodes, edges);
  const updates: PipelineNodePositionUpdate[] = [];
  for (const node of nodes) {
    const pos = positions.get(node.id);
    if (!pos) continue;
    updates.push({
      nodeId: node.id,
      x: Math.round(pos.x),
      y: Math.round(pos.y),
    });
  }
  return updates;
}

/** Find edge whose midpoint is nearest to a flow coordinate (for insert-on-drop). */
export function findEdgeInsertTarget(
  point: { x: number; y: number },
  _nodes: PipelineNodeDto[],
  edges: PipelineEdgeDto[],
  positions: Map<number, { x: number; y: number }>,
  threshold = 90,
): { from_node_id: number; to_node_id: number } | null {
  let best: { from_node_id: number; to_node_id: number; dist: number } | null =
    null;
  for (const edge of edges) {
    const from = positions.get(edge.from_node_id);
    const to = positions.get(edge.to_node_id);
    if (!from || !to) continue;
    const mid = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
    const dist = Math.hypot(point.x - mid.x, point.y - mid.y);
    if (dist <= threshold && (!best || dist < best.dist)) {
      best = {
        from_node_id: edge.from_node_id,
        to_node_id: edge.to_node_id,
        dist,
      };
    }
  }
  if (!best) return null;
  return {
    from_node_id: best.from_node_id,
    to_node_id: best.to_node_id,
  };
}

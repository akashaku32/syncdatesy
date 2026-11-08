import { useMemo } from "react";

import {
  BaseEdge,
  type Edge,
  type EdgeProps,
  Position,
  useStore,
} from "@xyflow/react";

/** Semicircle hop only when unrelated edges truly cross mid-segment. */
const BUMP_RADIUS = 10;
const LANE_GAP = 18;
const CORNER_R = 16;
const EPS = 1;

type Point = { x: number; y: number };

type OrthoSeg =
  | { kind: "h"; y: number; x1: number; x2: number }
  | { kind: "v"; x: number; y1: number; y2: number };

type EdgeGeom = {
  id: string;
  source: string;
  target: string;
  sx: number;
  sy: number;
  tx: number;
  ty: number;
};

function almostEqual(a: number, b: number, eps = EPS): boolean {
  return Math.abs(a - b) <= eps;
}

function between(v: number, a: number, b: number, eps = 0.5): boolean {
  const lo = Math.min(a, b) - eps;
  const hi = Math.max(a, b) + eps;
  return v >= lo && v <= hi;
}

function baseMidX(sx: number, tx: number): number {
  return sx + (tx - sx) * 0.5;
}

/** Fan-out / fan-in: share an endpoint → treat as one split bundle (no bumps). */
function sharesEndpoint(a: EdgeGeom, b: EdgeGeom): boolean {
  return (
    a.source === b.source ||
    a.target === b.target ||
    a.source === b.target ||
    a.target === b.source
  );
}

/**
 * Bundle edges that share a source or target (parent split / multi-parent join).
 * Within a bundle: same mid-X lane, no hop bumps.
 * Across bundles: separate lanes + bumps only at true crossings.
 */
function bundleIds(edges: EdgeGeom[]): Map<string, number> {
  const ids = edges.map((e) => e.id);
  const parent = new Map(ids.map((id) => [id, id]));

  const find = (x: string): string => {
    let p = parent.get(x)!;
    while (p !== parent.get(p)) {
      parent.set(p, parent.get(parent.get(p)!)!);
      p = parent.get(p)!;
    }
    parent.set(x, p);
    return p;
  };
  const unite = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };

  for (let i = 0; i < edges.length; i += 1) {
    for (let j = i + 1; j < edges.length; j += 1) {
      if (sharesEndpoint(edges[i], edges[j])) {
        unite(edges[i].id, edges[j].id);
      }
    }
  }

  const rootToBundle = new Map<string, number>();
  const edgeBundle = new Map<string, number>();
  let next = 0;
  for (const e of edges) {
    const root = find(e.id);
    if (!rootToBundle.has(root)) {
      rootToBundle.set(root, next);
      next += 1;
    }
    edgeBundle.set(e.id, rootToBundle.get(root)!);
  }
  return edgeBundle;
}

/**
 * Assign mid-X lane offsets per bundle so unrelated corridors don't share a trunk
 * (e.g. eco_res→inventdist vs dlv/bud_account→proj/vend).
 */
function midXByEdgeId(edges: EdgeGeom[]): Map<string, number> {
  const bundles = bundleIds(edges);
  const byBundle = new Map<number, EdgeGeom[]>();
  for (const e of edges) {
    const b = bundles.get(e.id)!;
    const list = byBundle.get(b) ?? [];
    list.push(e);
    byBundle.set(b, list);
  }

  type BundleMeta = {
    id: number;
    mid: number;
    yMin: number;
    yMax: number;
  };
  const metas: BundleMeta[] = [];
  for (const [id, list] of byBundle) {
    const mids = list.map((e) => baseMidX(e.sx, e.tx));
    const mid = mids.reduce((s, v) => s + v, 0) / mids.length;
    let yMin = Infinity;
    let yMax = -Infinity;
    for (const e of list) {
      yMin = Math.min(yMin, e.sy, e.ty);
      yMax = Math.max(yMax, e.sy, e.ty);
    }
    metas.push({ id, mid, yMin, yMax });
  }

  // Sort by vertical center so neighboring unrelated bundles get different lanes.
  metas.sort((a, b) => (a.yMin + a.yMax) / 2 - (b.yMin + b.yMax) / 2);

  const laneOf = new Map<number, number>();
  const placed: Array<{
    lane: number;
    yMin: number;
    yMax: number;
    mid: number;
  }> = [];

  const pickLane = (used: Set<number>): number => {
    if (!used.has(0)) return 0;
    for (let i = 1; i < 12; i += 1) {
      if (!used.has(-i)) return -i;
      if (!used.has(i)) return i;
    }
    return used.size;
  };

  for (const m of metas) {
    const used = new Set<number>();
    for (const p of placed) {
      const yOverlap = !(m.yMax < p.yMin - 8 || m.yMin > p.yMax + 8);
      const xClose = Math.abs(m.mid - p.mid) < LANE_GAP * 2;
      if (yOverlap && xClose) used.add(p.lane);
    }
    const lane = pickLane(used);
    laneOf.set(m.id, lane);
    placed.push({
      lane,
      yMin: m.yMin,
      yMax: m.yMax,
      mid: m.mid + lane * LANE_GAP,
    });
  }

  const result = new Map<string, number>();
  for (const e of edges) {
    const b = bundles.get(e.id)!;
    const meta = metas.find((m) => m.id === b)!;
    const lane = laneOf.get(b) ?? 0;
    result.set(e.id, meta.mid + lane * LANE_GAP);
  }
  return result;
}

function orthoPoints(
  sx: number,
  sy: number,
  tx: number,
  ty: number,
  midX: number,
): Point[] {
  if (almostEqual(sy, ty, 1)) {
    return [
      { x: sx, y: sy },
      { x: tx, y: ty },
    ];
  }
  // Keep mid-X inside the gap between nodes.
  const lo = Math.min(sx, tx) + 8;
  const hi = Math.max(sx, tx) - 8;
  const clamped = Math.max(lo, Math.min(hi, midX));
  return [
    { x: sx, y: sy },
    { x: clamped, y: sy },
    { x: clamped, y: ty },
    { x: tx, y: ty },
  ];
}

function segmentsFromPoints(pts: Point[]): OrthoSeg[] {
  const segs: OrthoSeg[] = [];
  for (let i = 0; i < pts.length - 1; i += 1) {
    const a = pts[i];
    const b = pts[i + 1];
    if (almostEqual(a.y, b.y, 0.5)) {
      segs.push({ kind: "h", y: a.y, x1: a.x, x2: b.x });
    } else if (almostEqual(a.x, b.x, 0.5)) {
      segs.push({ kind: "v", x: a.x, y1: a.y, y2: b.y });
    }
  }
  return segs;
}

/**
 * Hop only when this edge crosses an *unrelated* edge mid-segment.
 * Same-source / same-target splits never bump.
 */
function trueCrossingsForEdge(
  self: EdgeGeom,
  selfPts: Point[],
  others: Array<{ geom: EdgeGeom; pts: Point[] }>,
): Point[] {
  const selfSegs = segmentsFromPoints(selfPts);
  const hits: Point[] = [];

  for (const other of others) {
    if (other.geom.id === self.id) continue;
    if (sharesEndpoint(self, other.geom)) continue; // fan-out / fan-in: no bump
    // Only one edge hops at a crossing (stable by id).
    if (other.geom.id > self.id) continue;

    const otherSegs = segmentsFromPoints(other.pts);
    for (const a of selfSegs) {
      for (const b of otherSegs) {
        if (a.kind === "v" && b.kind === "h") {
          if (between(b.y, a.y1, a.y2) && between(a.x, b.x1, b.x2)) {
            const ySpan = Math.abs(a.y1 - a.y2);
            const fromEnd = Math.min(
              Math.abs(b.y - a.y1),
              Math.abs(b.y - a.y2),
            );
            // Not near the node elbows — only mid-corridor hops.
            if (ySpan > BUMP_RADIUS * 4 && fromEnd > BUMP_RADIUS * 2) {
              hits.push({ x: a.x, y: b.y });
            }
          }
        } else if (a.kind === "h" && b.kind === "v") {
          if (between(a.y, b.y1, b.y2) && between(b.x, a.x1, a.x2)) {
            const xSpan = Math.abs(a.x1 - a.x2);
            const fromEnd = Math.min(
              Math.abs(b.x - a.x1),
              Math.abs(b.x - a.x2),
            );
            if (xSpan > BUMP_RADIUS * 4 && fromEnd > BUMP_RADIUS * 2) {
              hits.push({ x: b.x, y: a.y });
            }
          }
        }
      }
    }
  }

  const unique: Point[] = [];
  for (const h of hits) {
    if (
      unique.some((u) => Math.hypot(u.x - h.x, u.y - h.y) < BUMP_RADIUS * 1.5)
    ) {
      continue;
    }
    unique.push(h);
  }
  return unique;
}

function pathWithBumps(pts: Point[], bumps: Point[]): string {
  if (pts.length < 2) return "";

  const parts: string[] = [`M ${pts[0].x} ${pts[0].y}`];

  for (let i = 0; i < pts.length - 1; i += 1) {
    const a = pts[i];
    const b = pts[i + 1];
    const isH = almostEqual(a.y, b.y, 0.5);
    const isV = almostEqual(a.x, b.x, 0.5);

    const segBumps = bumps
      .filter((bp) => {
        if (isH) return almostEqual(bp.y, a.y) && between(bp.x, a.x, b.x);
        if (isV) return almostEqual(bp.x, a.x) && between(bp.y, a.y, b.y);
        return false;
      })
      .sort((p, q) => {
        if (isH) return a.x < b.x ? p.x - q.x : q.x - p.x;
        return a.y < b.y ? p.y - q.y : q.y - p.y;
      });

    let cursor = { ...a };
    for (const bp of segBumps) {
      const rr = BUMP_RADIUS;
      if (isV) {
        const goingDown = b.y > a.y;
        const beforeY = goingDown ? bp.y - rr : bp.y + rr;
        const afterY = goingDown ? bp.y + rr : bp.y - rr;
        if (!between(beforeY, a.y, b.y) || !between(afterY, a.y, b.y)) continue;
        parts.push(`L ${a.x} ${beforeY}`);
        const sweep = goingDown ? 1 : 0;
        parts.push(`A ${rr} ${rr} 0 0 ${sweep} ${a.x} ${afterY}`);
        cursor = { x: a.x, y: afterY };
      } else if (isH) {
        const goingRight = b.x > a.x;
        const beforeX = goingRight ? bp.x - rr : bp.x + rr;
        const afterX = goingRight ? bp.x + rr : bp.x - rr;
        if (!between(beforeX, a.x, b.x) || !between(afterX, a.x, b.x)) continue;
        parts.push(`L ${beforeX} ${a.y}`);
        const sweep = goingRight ? 0 : 1;
        parts.push(`A ${rr} ${rr} 0 0 ${sweep} ${afterX} ${a.y}`);
        cursor = { x: afterX, y: a.y };
      }
    }

    if (i < pts.length - 2) {
      const c = pts[i + 2];
      const fromH = almostEqual(cursor.y, b.y, 0.5) || isH;
      const toH = almostEqual(b.y, c.y, 0.5);
      if (fromH && !toH) {
        const dir = b.x >= cursor.x ? 1 : -1;
        const approachX = b.x - dir * CORNER_R;
        if (
          between(approachX, cursor.x, b.x) ||
          almostEqual(cursor.x, approachX, 1)
        ) {
          parts.push(`L ${approachX} ${b.y}`);
        }
        const vdir = c.y >= b.y ? 1 : -1;
        parts.push(`Q ${b.x} ${b.y} ${b.x} ${b.y + vdir * CORNER_R}`);
      } else if (!fromH && toH) {
        const dir = b.y >= cursor.y ? 1 : -1;
        const approachY = b.y - dir * CORNER_R;
        if (
          between(approachY, cursor.y, b.y) ||
          almostEqual(cursor.y, approachY, 1)
        ) {
          parts.push(`L ${b.x} ${approachY}`);
        }
        const hdir = c.x >= b.x ? 1 : -1;
        parts.push(`Q ${b.x} ${b.y} ${b.x + hdir * CORNER_R} ${b.y}`);
      } else {
        parts.push(`L ${b.x} ${b.y}`);
      }
    } else {
      parts.push(`L ${b.x} ${b.y}`);
    }
  }

  return parts.join(" ");
}

function edgeEndpoints(
  edge: Edge,
  nodeLookup: Map<string, NodeLike>,
): Omit<EdgeGeom, "id" | "source" | "target"> | null {
  const source = nodeLookup.get(edge.source);
  const target = nodeLookup.get(edge.target);
  if (!source || !target) return null;

  const sw = source.measured?.width ?? source.width ?? 180;
  const sh = source.measured?.height ?? source.height ?? 72;
  const th = target.measured?.height ?? target.height ?? 72;

  return {
    sx: source.position.x + sw,
    sy: source.position.y + sh / 2,
    tx: target.position.x,
    ty: target.position.y + th / 2,
  };
}

type NodeLike = {
  id: string;
  position: { x: number; y: number };
  width?: number | null;
  height?: number | null;
  measured?: { width?: number; height?: number };
};

/**
 * Orthogonal edges with:
 * - Shared mid-X for fan-outs / multi-parent joins (no bumps at the split)
 * - Separate lanes for unrelated corridors (inventdist vs proj/vend)
 * - Hop bumps only when unrelated edges truly cross mid-segment
 */
export default function PipelineBumpEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition: _sourcePosition = Position.Right,
  targetPosition: _targetPosition = Position.Left,
  style,
  markerEnd,
  markerStart,
  source,
  target,
}: EdgeProps) {
  void _sourcePosition;
  void _targetPosition;

  const graph = useStore((s) => ({
    edges: s.edges,
    nodes: s.nodes,
  }));

  const path = useMemo(() => {
    const nodeLookup = new Map(graph.nodes.map((n) => [n.id, n as NodeLike]));

    const geoms: EdgeGeom[] = [];
    for (const e of graph.edges) {
      const ends = edgeEndpoints(e, nodeLookup);
      if (!ends) continue;
      geoms.push({
        id: e.id,
        source: e.source,
        target: e.target,
        ...ends,
      });
    }

    // Ensure the rendering edge is included (handles may differ slightly).
    if (!geoms.some((g) => g.id === id)) {
      geoms.push({
        id,
        source: String(source),
        target: String(target),
        sx: sourceX,
        sy: sourceY,
        tx: targetX,
        ty: targetY,
      });
    } else {
      // Prefer live handle coords for this edge.
      const self = geoms.find((g) => g.id === id)!;
      self.sx = sourceX;
      self.sy = sourceY;
      self.tx = targetX;
      self.ty = targetY;
    }

    const midXs = midXByEdgeId(geoms);
    const withPts = geoms.map((g) => ({
      geom: g,
      pts: orthoPoints(
        g.sx,
        g.sy,
        g.tx,
        g.ty,
        midXs.get(g.id) ?? baseMidX(g.sx, g.tx),
      ),
    }));

    const self = withPts.find((e) => e.geom.id === id)!;
    const bumps = trueCrossingsForEdge(self.geom, self.pts, withPts);
    return pathWithBumps(self.pts, bumps);
  }, [
    graph.edges,
    graph.nodes,
    id,
    source,
    target,
    sourceX,
    sourceY,
    targetX,
    targetY,
  ]);

  return (
    <BaseEdge
      id={id}
      path={path}
      style={style}
      markerEnd={markerEnd}
      markerStart={markerStart}
    />
  );
}

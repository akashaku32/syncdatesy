"""Pure helpers for pipeline run status (no Django imports — safe for unit tests)."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional


# Plan tier → max pods for an entire pipeline run (shared across in-flight nodes).
PLAN_RUN_POD_CAPS: dict[str, int] = {
    "free": 2,
    "standard": 2,
    "discounted": 2,
    "premium": 4,
    "enterprise": 6,
}

# Plan tier → max concurrent batch nodes.
PLAN_PARALLEL_SLOTS: dict[str, int] = {
    "free": 2,
    "standard": 2,
    "discounted": 2,
    "premium": 3,
    "enterprise": 4,
}

# Plan tier → default worker concurrency (mirrors celery_job_worker_pool).
PLAN_CONCURRENCY: dict[str, int] = {
    "free": 3,
    "standard": 3,
    "discounted": 3,
    "premium": 4,
    "enterprise": 4,  # same per-pod concurrency as premium; enterprise = premium pods + 2
}


class DagCycleError(ValueError):
    """Raised when pipeline dependency edges contain a cycle."""


def dag_execution_levels(
    node_ids: list[int],
    edges: list[tuple[int, int]],
) -> list[list[int]]:
    """Return DAG execution levels; nodes in each level may run in parallel."""
    if not node_ids:
        return []

    node_id_set = set(node_ids)
    indegree = {nid: 0 for nid in node_ids}
    graph = defaultdict(list)

    for from_id, to_id in edges:
        if from_id not in node_id_set or to_id not in node_id_set:
            raise ValueError("Invalid dependency edge.")
        if from_id == to_id:
            raise ValueError("Self dependency is not allowed.")
        graph[from_id].append(to_id)
        indegree[to_id] += 1

    levels = []
    current = sorted(nid for nid, deg in indegree.items() if deg == 0)
    seen = 0
    while current:
        levels.append(current)
        seen += len(current)
        next_level = []
        for cur in current:
            for nxt in graph[cur]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    next_level.append(nxt)
        current = sorted(next_level)

    if seen != len(node_ids):
        raise DagCycleError("Dependencies contain a cycle.")
    return levels


def build_batch_dependency_graph(
    node_ids: list[int],
    edges: list[tuple[int, int]],
) -> tuple[dict[int, set[int]], dict[int, list[int]]]:
    """Return parent and child adjacency maps for batch-only DAG edges."""
    node_id_set = set(node_ids)
    parents: dict[int, set[int]] = {node_id: set() for node_id in node_ids}
    children: dict[int, list[int]] = {node_id: [] for node_id in node_ids}

    for from_id, to_id in edges:
        if from_id not in node_id_set or to_id not in node_id_set:
            raise ValueError("Invalid dependency edge.")
        if from_id == to_id:
            raise ValueError("Self dependency is not allowed.")
        parents[to_id].add(from_id)
        children[from_id].append(to_id)

    return parents, children


def ready_batch_node_ids(
    node_ids: list[int],
    parents: dict[int, set[int]],
    completed: set[int],
    in_flight: set[int],
) -> list[int]:
    """Nodes whose batch parents are completed and that are not already running."""
    ready: list[int] = []
    for node_id in sorted(node_ids):
        if node_id in completed or node_id in in_flight:
            continue
        if all(parent_id in completed for parent_id in parents.get(node_id, ())):
            ready.append(node_id)
    return ready


def normalize_plan_name(plan: Optional[str]) -> str:
    return (plan or "").strip().lower()


def plan_run_pod_cap(plan: Optional[str], override: Optional[int] = None) -> int:
    """Max pods for an entire pipeline run (shared across in-flight nodes)."""
    if override is not None:
        cap = int(override)
        if cap > 0:
            return max(1, cap)
    return PLAN_RUN_POD_CAPS.get(normalize_plan_name(plan), 2)


def plan_parallel_slots(plan: Optional[str], override: Optional[int] = None) -> int:
    """Max concurrent batch nodes for a pipeline run, by plan tier."""
    if override is not None:
        slots = int(override)
        if slots > 0:
            return max(1, slots)
    return PLAN_PARALLEL_SLOTS.get(normalize_plan_name(plan), 2)


def plan_concurrency(plan: Optional[str]) -> int:
    return PLAN_CONCURRENCY.get(normalize_plan_name(plan), 3)


def subtree_table_weights(
    node_ids: list[int],
    children: dict[int, list[int]],
    table_counts: dict[int, int],
) -> dict[int, int]:
    """Sum of table_count for each node plus all unique reachable descendants."""

    def _descendants(node_id: int) -> set[int]:
        seen: set[int] = set()
        stack = list(children.get(node_id, ()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(children.get(cur, ()))
        return seen

    out: dict[int, int] = {}
    for node_id in node_ids:
        total = max(0, int(table_counts.get(node_id, 0)))
        for desc in _descendants(node_id):
            total += max(0, int(table_counts.get(desc, 0)))
        out[node_id] = total
    return out


def compute_node_weights(
    node_ids: list[int],
    edges: list[tuple[int, int]],
    table_counts: dict[int, int],
    *,
    levels: Optional[list[list[int]]] = None,
) -> dict[str, dict[str, int]]:
    """
    Build per-node scheduling weights.

    Returns keys as string node ids for JSON-friendly PipelineRun.metadata:
      { "12": {"tables": 40, "subtree": 120, "level": 2} }
    """
    _parents, children = build_batch_dependency_graph(node_ids, edges)
    if levels is None:
        try:
            levels = dag_execution_levels(node_ids, edges)
        except (DagCycleError, ValueError):
            levels = [sorted(node_ids)]
    level_map = node_level_index_map(levels)
    subtree = subtree_table_weights(node_ids, children, table_counts)
    out: dict[str, dict[str, int]] = {}
    for node_id in node_ids:
        out[str(node_id)] = {
            "tables": max(0, int(table_counts.get(node_id, 0))),
            "subtree": max(0, int(subtree.get(node_id, 0))),
            "level": int(level_map.get(node_id, 0)),
        }
    return out


def pick_ready_nodes_weighted(
    ready: list[int],
    weights: dict[str, dict[str, Any]],
    limit: int,
    wait_rounds: Optional[dict[int, int]] = None,
) -> list[int]:
    """
    Pick up to `limit` ready nodes by descending workload score.

    score = tables + 0.3 * subtree_tables - 0.1 * level_index
            + STARVATION_BONUS * wait_rounds
    Tie-break: lower node_id first.

    wait_rounds: how many scheduler loops a node has been ready but not started.
    A single wait round is enough for a light node to outrank a heavy newcomer,
    so siblings like RETL_BATCH are not starved behind Batch 1 forever.
    """
    if limit <= 0 or not ready:
        return []

    # One wait loop ≈ beating ~80 tables of weight so light ready nodes get a turn.
    starvation_bonus = 80.0

    def _score(node_id: int) -> tuple[float, int]:
        w = weights.get(str(node_id)) or {}
        tables = float(w.get("tables") or 0)
        subtree = float(w.get("subtree") or 0)
        level = float(w.get("level") or 0)
        waited = float((wait_rounds or {}).get(node_id, 0) or 0)
        return (
            tables + 0.3 * subtree - 0.1 * level + starvation_bonus * waited,
            -node_id,
        )

    ranked = sorted(ready, key=_score, reverse=True)
    return ranked[:limit]


def transitive_dependents(
    root_ids: set[int],
    children: dict[int, list[int]],
) -> set[int]:
    """All nodes reachable downstream from any root (not including roots)."""
    out: set[int] = set()
    stack = []
    for rid in root_ids:
        stack.extend(children.get(rid, ()))
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, ()))
    return out


def allocate_flow_pod_caps(
    in_flight_nodes: dict[int, int],
    flow_budget: int,
    concurrency: int = 3,
) -> dict[int, int]:
    """
    Split a pipeline-run pod budget across concurrent nodes.

    in_flight_nodes: {node_id: table_count}
    flow_budget: plan_max_pods for the whole run

    Returns {node_id: pod_cap} where sum(caps) <= flow_budget and each cap >= 1
    when tables > 0 (or 0 when tables == 0).
    """
    budget = max(0, int(flow_budget))
    conc = max(1, int(concurrency or 1))
    if not in_flight_nodes or budget <= 0:
        return {nid: 0 for nid in in_flight_nodes}

    needs: dict[int, int] = {}
    for node_id, tables in in_flight_nodes.items():
        t = max(0, int(tables))
        if t == 0:
            needs[node_id] = 0
        else:
            needs[node_id] = min(math.ceil(t / conc), t)

    active = {nid: need for nid, need in needs.items() if need > 0}
    if not active:
        return {nid: 0 for nid in in_flight_nodes}

    # Ideal case: every node gets its full need.
    total_need = sum(active.values())
    if total_need <= budget:
        return {nid: needs[nid] for nid in in_flight_nodes}

    # Not enough budget for all needs — give at least 1 to each active node
    # when possible, then distribute remainder by table weight.
    n_active = len(active)
    if budget < n_active:
        # Prefer heaviest nodes when we cannot give every node a pod.
        by_weight = sorted(
            active.keys(),
            key=lambda nid: (int(in_flight_nodes.get(nid, 0)), -nid),
            reverse=True,
        )
        caps = {nid: 0 for nid in in_flight_nodes}
        for nid in by_weight[:budget]:
            caps[nid] = 1
        return caps

    # Floor 1 for each active node, then proportionally fill remainder.
    caps = {nid: (1 if needs[nid] > 0 else 0) for nid in in_flight_nodes}
    remaining = budget - sum(1 for n in active)
    if remaining <= 0:
        return caps

    total_tables = sum(max(0, int(in_flight_nodes[nid])) for nid in active) or 1
    fractions: list[tuple[float, int]] = []
    assigned_extra = 0
    for nid in active:
        # Extra pods beyond the floor of 1, capped by need-1.
        max_extra = max(0, needs[nid] - 1)
        share = remaining * (max(0, int(in_flight_nodes[nid])) / total_tables)
        extra = min(max_extra, int(math.floor(share)))
        caps[nid] += extra
        assigned_extra += extra
        fractions.append((share - math.floor(share), nid))

    leftover = remaining - assigned_extra
    fractions.sort(key=lambda item: (-item[0], -int(in_flight_nodes.get(item[1], 0)), item[1]))
    for _frac, nid in fractions:
        if leftover <= 0:
            break
        if caps[nid] < needs[nid]:
            caps[nid] += 1
            leftover -= 1

    # Safety: never exceed budget.
    while sum(caps.values()) > budget:
        # Trim from lightest node that has >1.
        candidates = sorted(
            [nid for nid in active if caps[nid] > 1],
            key=lambda nid: (caps[nid], int(in_flight_nodes.get(nid, 0)), nid),
        )
        if not candidates:
            break
        caps[candidates[0]] -= 1

    return caps


def node_level_index_map(levels: list[list[int]]) -> dict[int, int]:
    """Map batch node id -> zero-based DAG execution level index."""
    out: dict[int, int] = {}
    for level_idx, level_nodes in enumerate(levels):
        for node_id in level_nodes:
            out[node_id] = level_idx
    return out


def dag_level_width_violations(
    levels: list[list[int]],
    max_nodes_per_level: int,
) -> list[tuple[int, int]]:
    """Return (level_index, node_count) for levels that exceed the parallel cap.

    Deprecated for pipeline graph rules — prefer node_children_count_violations
    (max children per node, not max nodes per DAG level).
    """
    violations: list[tuple[int, int]] = []
    for level_idx, level_nodes in enumerate(levels):
        count = len(level_nodes)
        if count > max_nodes_per_level:
            violations.append((level_idx, count))
    return violations


def node_children_count_violations(
    edges: list[tuple[int, int]],
    max_children_per_node: int,
    *,
    node_ids: Optional[set[int]] = None,
) -> list[tuple[int, int]]:
    """
    Return (node_id, child_count) for nodes whose out-degree exceeds the cap.

    Each node may have at most max_children_per_node direct children — DAG level
    width is intentionally not capped here.
    """
    counts: dict[int, int] = defaultdict(int)
    for from_id, to_id in edges:
        if node_ids is not None and (
            from_id not in node_ids or to_id not in node_ids
        ):
            continue
        counts[from_id] += 1
    return [
        (nid, count)
        for nid, count in sorted(counts.items())
        if count > max_children_per_node
    ]


def pick_ready_node_respecting_level_cap(
    ready: list[int],
    in_flight: dict,
    node_level: dict[int, int],
    max_per_level: int,
) -> Optional[int]:
    """Pick the first ready node whose DAG level is below the in-flight cap.

    Deprecated: pipeline runs no longer throttle by DAG level width.
    """
    for index, node_id in enumerate(ready):
        level = node_level.get(node_id, 0)
        in_flight_at_level = sum(
            1 for nid in in_flight.values() if node_level.get(nid, 0) == level
        )
        if in_flight_at_level < max_per_level:
            return index
    return None


def task_had_immediate_failure(task_results) -> bool:
    if not task_results:
        return True
    for res in task_results:
        if not isinstance(res, dict):
            continue
        if res.get('status') in ('failed', 'error'):
            return True
        if res.get('error'):
            return True
        if res.get('message') and res.get('status') == 'failed':
            return True
    return False


def pipeline_run_failure_message(meta: dict, fallback: str = "") -> str:
    """
    User-facing pipeline run error: prefer failed batch names + table errors over
    generic "Migration ended with status: failed" strings.
    """
    failed_parts: list[str] = []
    for key in sorted(
        (meta.get("nodes") or {}).keys(),
        key=lambda item: (0, int(item)) if str(item).isdigit() else (1, str(item)),
    ):
        entry = (meta.get("nodes") or {}).get(key) or {}
        if str(entry.get("status") or "").lower() != "failed":
            continue
        label = (
            str(entry.get("batch_name") or entry.get("node_label") or "").strip()
            or f"Node {key}"
        )
        detail = str(entry.get("error") or "").strip()
        lowered = detail.lower()
        if detail.startswith("Skipped"):
            continue
        if lowered.startswith("migration ended with status"):
            detail = ""
        if lowered.startswith("pipeline node ") and "migration session ended" in lowered:
            detail = ""
        if detail:
            failed_parts.append(f"{label}: {detail}")
        else:
            failed_parts.append(f"{label} failed")

    completed_batches = sum(
        1
        for entry in (meta.get("nodes") or {}).values()
        if str((entry or {}).get("status") or "").lower() == "completed"
    )
    if failed_parts:
        summary = "; ".join(failed_parts[:3])
        if completed_batches:
            return (
                f"Pipeline stopped after {completed_batches} batch(es) completed successfully. "
                f"{summary}"
            )
        return summary

    fb = str(fallback or meta.get("error") or "").strip()
    return fb or "Pipeline run failed"


def rollup_pipeline_overall(nodes_payload: list, run_status: str) -> dict:
    nodes_total = len(nodes_payload)
    nodes_completed = sum(1 for n in nodes_payload if n.get('status') == 'completed')
    nodes_failed = sum(1 for n in nodes_payload if n.get('status') in ('failed', 'timeout'))
    nodes_running = sum(1 for n in nodes_payload if n.get('status') in ('running', 'in_progress'))
    nodes_skipped = sum(1 for n in nodes_payload if n.get('status') == 'skipped')

    tables_completed = 0
    tables_total = 0
    tables_failed = 0
    for node in nodes_payload:
        mig = node.get('migration_status') or {}
        tables = mig.get('tables') or []
        # Skipped nodes contribute their table_count to totals but no completed/failed tables.
        if (node.get('status') or '') == 'skipped' and not tables:
            tables_total += int(node.get('table_count') or 0)
            continue
        tables_total += len(tables) if tables else int(node.get('table_count') or 0)
        for table in tables:
            status_icon = (table.get('status_icon') or table.get('status') or '').lower()
            if status_icon in ('completed', 'success'):
                tables_completed += 1
            elif status_icon in ('failed', 'error'):
                tables_failed += 1

    derived_status = run_status
    if run_status == 'running':
        # Keep the run "running" while nodes or tables are still active (incl. Celery retry).
        if nodes_running > 0:
            derived_status = 'running'
        elif nodes_failed or tables_failed:
            derived_status = 'failed'
        elif nodes_total and nodes_completed + nodes_skipped == nodes_total:
            derived_status = 'completed'

    return {
        'nodes_total': nodes_total,
        'nodes_completed': nodes_completed,
        'nodes_failed': nodes_failed,
        'nodes_running': nodes_running,
        'nodes_skipped': nodes_skipped,
        'tables_completed': tables_completed,
        'tables_total': tables_total,
        'tables_failed': tables_failed,
        'status': derived_status,
    }

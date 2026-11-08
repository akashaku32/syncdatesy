import unittest

from apps.elttool.v1.pipeline_run_helpers import (
    DagCycleError,
    allocate_flow_pod_caps,
    build_batch_dependency_graph,
    compute_node_weights,
    dag_execution_levels,
    dag_level_width_violations,
    node_children_count_violations,
    node_level_index_map,
    pick_ready_node_respecting_level_cap,
    pick_ready_nodes_weighted,
    plan_concurrency,
    plan_parallel_slots,
    plan_run_pod_cap,
    ready_batch_node_ids,
    rollup_pipeline_overall,
    subtree_table_weights,
    task_had_immediate_failure,
    transitive_dependents,
)


class PipelineRunHelperTests(unittest.TestCase):
    def test_task_had_immediate_failure_detects_failed_status(self):
        self.assertTrue(
            task_had_immediate_failure([{"status": "failed", "message": "boom"}])
        )
        self.assertFalse(
            task_had_immediate_failure([{"status": "success", "tables": ["a"]}])
        )

    def test_rollup_pipeline_overall_counts_nodes_and_tables(self):
        nodes_payload = [
            {
                "status": "completed",
                "table_count": 2,
                "migration_status": {
                    "tables": [
                        {"status_icon": "completed"},
                        {"status_icon": "completed"},
                    ]
                },
            },
            {
                "status": "running",
                "table_count": 1,
                "migration_status": {
                    "tables": [{"status_icon": "in_progress"}],
                },
            },
        ]
        overall = rollup_pipeline_overall(nodes_payload, "running")
        self.assertEqual(overall["nodes_completed"], 1)
        self.assertEqual(overall["nodes_running"], 1)
        self.assertEqual(overall["tables_completed"], 2)
        self.assertEqual(overall["tables_total"], 3)

    def test_task_had_immediate_failure_treats_empty_results_as_failure(self):
        self.assertTrue(task_had_immediate_failure([]))
        self.assertTrue(task_had_immediate_failure(None))

    def test_task_had_immediate_failure_detects_error_key(self):
        self.assertTrue(task_had_immediate_failure([{"status": "ok", "error": "boom"}]))

    def test_rollup_stays_running_while_node_active_despite_table_failed(self):
        nodes_payload = [
            {
                "status": "running",
                "table_count": 2,
                "migration_status": {
                    "tables": [
                        {"status_icon": "failed"},
                        {"status_icon": "in_progress"},
                    ],
                },
            },
        ]
        overall = rollup_pipeline_overall(nodes_payload, "running")
        self.assertEqual(overall["status"], "running")
        self.assertEqual(overall["tables_failed"], 1)
        self.assertEqual(overall["nodes_running"], 1)

    def test_rollup_marks_running_as_failed_when_node_failed(self):
        nodes_payload = [
            {"status": "failed", "table_count": 1, "migration_status": {"tables": []}},
        ]
        overall = rollup_pipeline_overall(nodes_payload, "running")
        self.assertEqual(overall["derived_status"] if "derived_status" in overall else overall["status"], "failed")
        self.assertEqual(overall["nodes_failed"], 1)

    def test_dag_execution_levels_two_roots_one_child(self):
        levels = dag_execution_levels(
            [1, 2, 3],
            [(1, 3), (2, 3)],
        )
        self.assertEqual(levels, [[1, 2], [3]])

    def test_dag_execution_levels_linear_chain(self):
        levels = dag_execution_levels([1, 2, 3], [(1, 2), (2, 3)])
        self.assertEqual(levels, [[1], [2], [3]])

    def test_dag_execution_levels_diamond(self):
        levels = dag_execution_levels(
            [1, 2, 3, 4],
            [(1, 2), (1, 3), (2, 4), (3, 4)],
        )
        self.assertEqual(levels, [[1], [2, 3], [4]])

    def test_dag_execution_levels_raises_on_cycle(self):
        with self.assertRaises(DagCycleError):
            dag_execution_levels([1, 2], [(1, 2), (2, 1)])

    def test_dag_execution_levels_raises_on_self_edge(self):
        with self.assertRaises(ValueError):
            dag_execution_levels([1], [(1, 1)])

    def test_ready_batch_node_ids_unlocks_independent_branch(self):
        # batch f=5, pr=7, s=6, cust=14, few=13
        node_ids = [5, 7, 6, 14, 13]
        edges = [(5, 6), (7, 6), (7, 14), (6, 13)]
        parents, _children = build_batch_dependency_graph(node_ids, edges)

        ready = ready_batch_node_ids(
            node_ids,
            parents,
            completed={5, 7, 6},
            in_flight={14},
        )
        self.assertEqual(ready, [13])

        blocked = ready_batch_node_ids(
            node_ids,
            parents,
            completed={5, 7},
            in_flight={6, 14},
        )
        self.assertEqual(blocked, [])

    def test_build_batch_dependency_graph_rejects_invalid_edge(self):
        with self.assertRaises(ValueError):
            build_batch_dependency_graph([1, 2], [(1, 3)])

    def test_rollup_counts_table_failures(self):
        nodes_payload = [
            {
                "status": "completed",
                "table_count": 2,
                "migration_status": {
                    "tables": [
                        {"status_icon": "completed"},
                        {"status_icon": "failed"},
                    ]
                },
            },
        ]
        overall = rollup_pipeline_overall(nodes_payload, "completed")
        self.assertEqual(overall["tables_failed"], 1)
        self.assertEqual(overall["tables_completed"], 1)

    def test_node_level_index_map(self):
        levels = [[1, 2], [3]]
        self.assertEqual(node_level_index_map(levels), {1: 0, 2: 0, 3: 1})

    def test_dag_level_width_violations(self):
        levels = [[1], [2, 3, 4], [5]]
        self.assertEqual(dag_level_width_violations(levels, 2), [(1, 3)])
        self.assertEqual(dag_level_width_violations(levels, 3), [])

    def test_node_children_count_violations(self):
        # Wide level with 4 nodes is OK if each parent has ≤2 children.
        ok_edges = [(1, 2), (1, 3), (4, 5), (4, 6)]
        self.assertEqual(node_children_count_violations(ok_edges, 2), [])
        # One parent with 3 children violates the per-node cap.
        bad_edges = [(1, 2), (1, 3), (1, 4)]
        self.assertEqual(node_children_count_violations(bad_edges, 2), [(1, 3)])

    def test_pick_ready_node_respecting_level_cap(self):
        node_level = {10: 0, 11: 0, 12: 1}
        ready = [10, 11, 12]
        in_flight = {99: 10}
        self.assertEqual(
            pick_ready_node_respecting_level_cap(ready, in_flight, node_level, 2),
            0,
        )
        in_flight_full = {98: 10, 99: 11}
        self.assertEqual(
            pick_ready_node_respecting_level_cap(ready, in_flight_full, node_level, 2),
            2,
        )

    def test_plan_tier_maps(self):
        self.assertEqual(plan_run_pod_cap("Premium"), 4)
        self.assertEqual(plan_run_pod_cap("enterprise"), 6)
        self.assertEqual(plan_run_pod_cap("free"), 2)
        self.assertEqual(plan_run_pod_cap("standard", override=3), 3)
        self.assertEqual(plan_parallel_slots("premium"), 3)
        self.assertEqual(plan_parallel_slots("enterprise"), 4)
        self.assertEqual(plan_parallel_slots("free"), 2)
        self.assertEqual(plan_concurrency("premium"), 4)

    def test_subtree_table_weights_includes_descendants(self):
        # B1=1 → C=2, D=3; B2=4 → E=5; both → K1=6
        node_ids = [1, 2, 3, 4, 5, 6]
        edges = [(1, 2), (1, 3), (4, 5), (2, 6), (3, 6), (5, 6)]
        _parents, children = build_batch_dependency_graph(node_ids, edges)
        tables = {1: 30, 2: 15, 3: 5, 4: 8, 5: 5, 6: 50}
        weights = subtree_table_weights(node_ids, children, tables)
        self.assertEqual(weights[6], 50)
        self.assertEqual(weights[2], 15 + 50)
        self.assertEqual(weights[1], 30 + 15 + 5 + 50)
        self.assertEqual(weights[4], 8 + 5 + 50)

    def test_compute_node_weights_and_weighted_picker(self):
        node_ids = [1, 2, 3, 4]
        edges = [(1, 3), (2, 4)]
        tables = {1: 10, 2: 40, 3: 5, 4: 5}
        weights = compute_node_weights(node_ids, edges, tables)
        self.assertEqual(weights["2"]["tables"], 40)
        self.assertGreater(weights["2"]["subtree"], weights["1"]["subtree"])

        ready = [1, 2]
        picked = pick_ready_nodes_weighted(ready, weights, 1)
        self.assertEqual(picked, [2])

        # Wide level: pick top 2 by weight.
        ready_wide = [1, 2, 3, 4]
        picked2 = pick_ready_nodes_weighted(ready_wide, weights, 2)
        self.assertEqual(picked2[0], 2)
        self.assertEqual(len(picked2), 2)

        self.assertEqual(pick_ready_nodes_weighted([], weights, 2), [])
        self.assertEqual(pick_ready_nodes_weighted(ready, weights, 0), [])

    def test_pick_ready_nodes_weighted_tie_break_lower_id(self):
        weights = {
            "10": {"tables": 5, "subtree": 5, "level": 0},
            "11": {"tables": 5, "subtree": 5, "level": 0},
        }
        picked = pick_ready_nodes_weighted([11, 10], weights, 2)
        self.assertEqual(picked, [10, 11])

    def test_pick_ready_nodes_anti_starvation_after_wait(self):
        # Heavy Batch 1 vs light RETL — after one wait round RETL wins a slot.
        weights = {
            "122": {"tables": 1, "subtree": 1, "level": 3},
            "133": {"tables": 37, "subtree": 37, "level": 4},
        }
        self.assertEqual(
            pick_ready_nodes_weighted([122, 133], weights, 1),
            [133],
        )
        self.assertEqual(
            pick_ready_nodes_weighted(
                [122, 133], weights, 1, wait_rounds={122: 1, 133: 0}
            ),
            [122],
        )

    def test_transitive_dependents(self):
        children = {1: [2, 3], 2: [4], 3: [], 4: [5]}
        self.assertEqual(transitive_dependents({1}, children), {2, 3, 4, 5})
        self.assertEqual(transitive_dependents({2}, children), {4, 5})
        self.assertEqual(transitive_dependents({3}, children), set())

    def test_allocate_flow_pod_caps_proportional_premium(self):
        # Premium budget=4, B1=40 tables / B2=10 tables → 3+1
        caps = allocate_flow_pod_caps({1: 40, 2: 10}, flow_budget=4, concurrency=3)
        self.assertEqual(sum(caps.values()), 4)
        self.assertEqual(caps[1], 3)
        self.assertEqual(caps[2], 1)

    def test_allocate_flow_pod_caps_small_nodes(self):
        caps = allocate_flow_pod_caps({1: 2, 2: 3}, flow_budget=4, concurrency=3)
        self.assertEqual(caps[1], 1)
        self.assertEqual(caps[2], 1)
        self.assertEqual(sum(caps.values()), 2)

    def test_allocate_flow_pod_caps_equal_split(self):
        caps = allocate_flow_pod_caps({1: 30, 2: 30}, flow_budget=4, concurrency=3)
        self.assertEqual(sum(caps.values()), 4)
        self.assertEqual(caps[1], 2)
        self.assertEqual(caps[2], 2)

    def test_allocate_flow_pod_caps_single_node_full_budget(self):
        caps = allocate_flow_pod_caps({1: 50}, flow_budget=4, concurrency=3)
        self.assertEqual(caps[1], 4)

    def test_allocate_flow_pod_caps_single_table_one_pod(self):
        caps = allocate_flow_pod_caps({1: 1}, flow_budget=4, concurrency=3)
        self.assertEqual(caps[1], 1)

    def test_allocate_flow_pod_caps_zero_tables(self):
        caps = allocate_flow_pod_caps({1: 0, 2: 10}, flow_budget=4, concurrency=3)
        self.assertEqual(caps[1], 0)
        self.assertGreaterEqual(caps[2], 1)

    def test_allocate_flow_pod_caps_budget_overflow_prefers_heavy(self):
        # budget=1 with two nodes → only heaviest gets a pod
        caps = allocate_flow_pod_caps({1: 5, 2: 40}, flow_budget=1, concurrency=3)
        self.assertEqual(sum(caps.values()), 1)
        self.assertEqual(caps[2], 1)
        self.assertEqual(caps[1], 0)


class PipelineRunFailureMessageTests(unittest.TestCase):
    def test_prefers_failed_batch_detail_over_generic(self):
        from apps.elttool.v1.pipeline_run_helpers import pipeline_run_failure_message

        meta = {
            "nodes": {
                "1": {"status": "completed", "batch_name": "Batch 1"},
                "2": {
                    "status": "failed",
                    "batch_name": "Batch 2",
                    "error": "Account: invalid field",
                },
            }
        }
        msg = pipeline_run_failure_message(meta, "Migration ended with status: failed")
        self.assertIn("Batch 2", msg)
        self.assertIn("invalid field", msg)
        self.assertIn("1 batch(es) completed", msg)

    def test_skips_generic_migration_ended_stub(self):
        from apps.elttool.v1.pipeline_run_helpers import pipeline_run_failure_message

        meta = {
            "nodes": {
                "2": {
                    "status": "failed",
                    "batch_name": "RETL_BATCH",
                    "error": "Migration ended with status: failed",
                },
            }
        }
        msg = pipeline_run_failure_message(meta, "fallback")
        self.assertIn("RETL_BATCH failed", msg)


if __name__ == "__main__":
    unittest.main()

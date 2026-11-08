import json
import unittest
from unittest.mock import MagicMock, patch

import celery_worker


class FinalizerContextTests(unittest.TestCase):
    def test_store_finalizer_context_writes_redis_payload(self):
        r = MagicMock()
        children = [{"id": "child-a"}, {"id": "child-b"}]
        parent_ctx = {"session": 356, "log_id": 4704}

        celery_worker._store_finalizer_context(
            r,
            job_name="pipeline_run_172_node_13__job",
            connection_id=6,
            is_listing_path=False,
            parent_ctx=parent_ctx,
            children=children,
        )

        r.set.assert_called_once()
        key, payload_json, *_ = r.set.call_args[0]
        self.assertEqual(key, "celery:finalizer-ctx:pipeline_run_172_node_13__job")
        payload = json.loads(payload_json)
        self.assertEqual(payload["connection_id"], 6)
        self.assertEqual(payload["parent_ctx"], parent_ctx)
        self.assertEqual(payload["expected_task_ids"], ["child-a", "child-b"])


if __name__ == "__main__":
    unittest.main()

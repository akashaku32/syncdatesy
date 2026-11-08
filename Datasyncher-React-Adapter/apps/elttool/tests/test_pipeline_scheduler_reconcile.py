import unittest
from unittest.mock import MagicMock, patch

from apps.elttool.services.pipeline_scheduler_reconcile import (
    reconcile_all_pipeline_schedulers,
    reconcile_on_start_enabled,
)


class PipelineSchedulerReconcileTests(unittest.TestCase):
    def test_reconcile_on_start_defaults_enabled(self):
        with patch.dict('os.environ', {}, clear=False):
            self.assertTrue(reconcile_on_start_enabled())

    def test_reconcile_on_start_can_be_disabled(self):
        with patch.dict(
            'os.environ',
            {'PIPELINE_SCHEDULER_RECONCILE_ON_START': 'false'},
            clear=False,
        ):
            self.assertFalse(reconcile_on_start_enabled())

    @patch('apps.elttool.v1.pipeline_views._sync_pipeline_scheduler')
    @patch('apps.elttool.models.Pipeline')
    def test_reconcile_calls_sync_per_pipeline(
        self,
        mock_pipeline_model,
        mock_sync,
    ):
        p1 = MagicMock(id=1)
        p2 = MagicMock(id=2)
        mock_pipeline_model.objects.all.return_value.order_by.return_value.iterator.return_value = [
            p1,
            p2,
        ]

        stats = reconcile_all_pipeline_schedulers()

        self.assertEqual(stats['total'], 2)
        self.assertEqual(stats['synced'], 2)
        mock_sync.assert_any_call(p1)
        mock_sync.assert_any_call(p2)

    @patch('apps.elttool.v1.pipeline_views._sync_pipeline_scheduler')
    @patch('apps.elttool.models.Pipeline')
    def test_dry_run_skips_sync(self, mock_pipeline_model, mock_sync):
        mock_pipeline_model.objects.all.return_value.order_by.return_value.iterator.return_value = [
            MagicMock(id=9),
        ]
        stats = reconcile_all_pipeline_schedulers(dry_run=True)
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['synced'], 0)
        mock_sync.assert_not_called()

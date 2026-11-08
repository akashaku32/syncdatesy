from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import serializers

from apps.elttool.v1.pipeline_views import (
    PipelineSerializer,
    _normalize_timezone_name,
    _parse_pipeline_schedule,
    _patch_touches_schedule,
    _pipeline_root_node_ids,
    _readable_pipeline_schedule,
    _validate_dag,
)


class PipelineValidationTests(SimpleTestCase):
    def test_validate_dag_accepts_linear_graph(self):
        nodes = [SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]
        edges = [
            SimpleNamespace(from_node_id=1, to_node_id=2),
            SimpleNamespace(from_node_id=2, to_node_id=3),
        ]
        topo = _validate_dag(nodes, edges)
        self.assertEqual(topo, [1, 2, 3])

    def test_validate_dag_rejects_cycle(self):
        nodes = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        edges = [
            SimpleNamespace(from_node_id=1, to_node_id=2),
            SimpleNamespace(from_node_id=2, to_node_id=1),
        ]
        with self.assertRaises(serializers.ValidationError):
            _validate_dag(nodes, edges)

    def test_pipeline_root_node_ids(self):
        nodes = [SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]
        edges = [
            SimpleNamespace(from_node_id=1, to_node_id=2),
            SimpleNamespace(from_node_id=2, to_node_id=3),
        ]
        self.assertEqual(_pipeline_root_node_ids(nodes, edges), [1])

    def test_parse_pipeline_schedule_interval(self):
        pipeline = SimpleNamespace(
            schedule_type='interval',
            time_frequency='30',
            schedule_config={},
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'interval')
        self.assertEqual(trigger_args, {'minutes': 30})

    def test_parse_pipeline_schedule_manual(self):
        pipeline = SimpleNamespace(schedule_type='manual', schedule_config={})
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertIsNone(trigger_type)
        self.assertIsNone(trigger_args)

    def test_parse_pipeline_schedule_interval_weeks(self):
        pipeline = SimpleNamespace(
            schedule_type='interval',
            time_frequency='20160',
            schedule_config={'interval_unit': 'week', 'interval_value': 2},
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'interval')
        self.assertEqual(trigger_args, {'weeks': 2})

    def test_parse_pipeline_schedule_weekly_cron_multi_day(self):
        pipeline = SimpleNamespace(
            schedule_type='cron',
            schedule_config={
                'cron_type': 'weekly',
                'hour': 9,
                'minute': 45,
                'days_of_week': ['mon', 'wed', 'fri'],
                'timezone': 'UTC',
            },
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'cron')
        self.assertEqual(trigger_args['day_of_week'], 'mon,wed,fri')

    def test_parse_pipeline_schedule_weekly_cron(self):
        pipeline = SimpleNamespace(
            schedule_type='cron',
            schedule_config={
                'cron_type': 'weekly',
                'hour': 9,
                'minute': 45,
                'day_of_week': 'wed',
                'timezone': 'UTC',
            },
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'cron')
        self.assertEqual(trigger_args['day_of_week'], 'wed')
        self.assertEqual(trigger_args['hour'], 9)
        self.assertEqual(trigger_args['minute'], 45)

    def test_parse_pipeline_schedule_custom_interval_minutes(self):
        pipeline = SimpleNamespace(
            schedule_type='interval',
            time_frequency='42',
            schedule_config={},
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'interval')
        self.assertEqual(trigger_args, {'minutes': 42})

    def test_parse_pipeline_schedule_daily_cron(self):
        pipeline = SimpleNamespace(
            schedule_type='cron',
            schedule_config={
                'cron_type': 'daily',
                'hour': 8,
                'minute': 30,
                'timezone': 'Asia/Kolkata',
            },
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'cron')
        self.assertEqual(trigger_args['hour'], 8)
        self.assertEqual(trigger_args['minute'], 30)
        self.assertEqual(trigger_args['timezone'], 'Asia/Kolkata')
        self.assertNotIn('day_of_week', trigger_args)

    def test_normalize_timezone_name_falls_back_to_utc(self):
        self.assertEqual(_normalize_timezone_name('Asia/Kolkata'), 'Asia/Kolkata')
        self.assertEqual(_normalize_timezone_name('Invalid/Zone'), 'UTC')
        self.assertEqual(_normalize_timezone_name(''), 'UTC')

    def test_parse_pipeline_schedule_weekdays_cron(self):
        pipeline = SimpleNamespace(
            schedule_type='cron',
            schedule_config={
                'cron_type': 'weekdays',
                'hour': 9,
                'minute': 0,
                'timezone': 'UTC',
            },
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'cron')
        self.assertEqual(trigger_args['day_of_week'], 'mon-fri')

    def test_parse_pipeline_schedule_monthly_cron(self):
        pipeline = SimpleNamespace(
            schedule_type='cron',
            schedule_config={
                'cron_type': 'monthly',
                'hour': 7,
                'minute': 15,
                'day': 10,
                'timezone': 'UTC',
            },
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'cron')
        self.assertEqual(trigger_args['day'], 10)
        self.assertEqual(trigger_args['minute'], 15)

    def test_readable_pipeline_schedule_interval_presets(self):
        self.assertEqual(
            _readable_pipeline_schedule(
                SimpleNamespace(
                    schedule_type='interval',
                    time_frequency='360',
                    schedule_config={},
                ),
            ),
            'Every 6 hours',
        )
        self.assertEqual(
            _readable_pipeline_schedule(
                SimpleNamespace(
                    schedule_type='interval',
                    time_frequency='42',
                    schedule_config={},
                ),
            ),
            'Every 42 min',
        )

    def test_readable_pipeline_schedule_manual(self):
        label = _readable_pipeline_schedule(
            SimpleNamespace(schedule_type='manual', schedule_config={}),
        )
        self.assertEqual(label, 'Manual only')

    def test_readable_pipeline_schedule_with_end_date(self):
        from datetime import datetime
        label = _readable_pipeline_schedule(
            SimpleNamespace(
                schedule_type='cron',
                schedule_config={
                    'cron_type': 'weekly',
                    'hour': 16,
                    'minute': 0,
                    'days_of_week': ['fri'],
                    'timezone': 'UTC',
                },
                sync_end_date=datetime(2026, 12, 25, 23, 59, 59),
            ),
        )
        self.assertIn('Weekly (fri)', label)
        self.assertIn('until 25 Dec 2026', label)

    def test_parse_pipeline_schedule_monthly_relative(self):
        pipeline = SimpleNamespace(
            schedule_type='cron',
            schedule_config={
                'cron_type': 'monthly',
                'hour': 16,
                'minute': 0,
                'monthly_mode': 'relative',
                'monthly_week': 'first',
                'monthly_weekday': 'fri',
                'timezone': 'UTC',
            },
        )
        trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
        self.assertEqual(trigger_type, 'cron')
        self.assertEqual(trigger_args['day'], '1st fri')

    def test_readable_pipeline_schedule_monthly_relative(self):
        label = _readable_pipeline_schedule(
            SimpleNamespace(
                schedule_type='cron',
                schedule_config={
                    'cron_type': 'monthly',
                    'hour': 16,
                    'minute': 0,
                    'monthly_mode': 'relative',
                    'monthly_day_cron': '1st fri',
                    'timezone': 'UTC',
                },
            ),
        )
        self.assertIn('Monthly (1st fri)', label)
        label = _readable_pipeline_schedule(
            SimpleNamespace(
                schedule_type='cron',
                schedule_config={
                    'cron_type': 'daily',
                    'hour': 6,
                    'minute': 0,
                    'timezone': 'UTC',
                },
            ),
        )
        self.assertEqual(label, 'Daily 06:00 UTC')

    @patch('apps.elttool.scheduler_client.SchedulerClient.get_job')
    def test_pipeline_serializer_next_run_at_active(self, get_job):
        get_job.return_value = {
            'next_run_time': '2026-07-02T14:30:00+00:00',
        }
        pipeline = SimpleNamespace(id=7, status='active')
        value = PipelineSerializer().get_next_run_at(pipeline)
        self.assertEqual(value, '2026-07-02T14:30:00+00:00')
        get_job.assert_called_once_with('pipeline_7')

    def test_pipeline_serializer_next_run_at_paused(self):
        pipeline = SimpleNamespace(id=7, status='paused')
        value = PipelineSerializer().get_next_run_at(pipeline)
        self.assertIsNone(value)

    def test_pipeline_serializer_next_run_at_schedule_paused(self):
        pipeline = SimpleNamespace(
            id=7,
            status='active',
            schedule_paused=True,
            schedule_type='interval',
        )
        value = PipelineSerializer().get_next_run_at(pipeline)
        self.assertIsNone(value)


class PipelineCreateDefaultsTests(SimpleTestCase):
    @patch('apps.elttool.v1.pipeline_views.PipelineSerializer')
    @patch('apps.elttool.v1.pipeline_views.ensure_start_node')
    @patch('apps.elttool.v1.pipeline_views._sync_pipeline_scheduler')
    @patch('apps.elttool.v1.pipeline_views.Pipeline.objects.create')
    def test_create_pipeline_defaults_to_paused_manual_schedule(
        self,
        mock_create,
        _mock_sync,
        _mock_ensure,
        mock_serializer,
    ):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from apps.elttool.v1.pipeline_views import PipelineListCreateView

        pipeline = SimpleNamespace(id=9)
        mock_create.return_value = pipeline
        mock_serializer.return_value.data = {'id': 9}

        user = SimpleNamespace(
            user_id=1,
            cmp=SimpleNamespace(id=1),
            is_authenticated=True,
            roles=SimpleNamespace(role_name='Administrator'),
        )
        request = APIRequestFactory().post('/api/v1/pipelines/', {'name': 'Flow A'}, format='json')
        force_authenticate(request, user=user)

        response = PipelineListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 201)
        create_kwargs = mock_create.call_args.kwargs
        self.assertTrue(create_kwargs['schedule_paused'])
        self.assertEqual(create_kwargs['schedule_type'], 'manual')


class PipelineSchedulePatchValidationGateTests(SimpleTestCase):
    def test_patch_touches_schedule_on_cadence_fields(self):
        self.assertTrue(
            _patch_touches_schedule({'schedule_type': 'interval', 'time_frequency': '15'}),
        )
        self.assertTrue(_patch_touches_schedule({'schedule_config': {'cron_type': 'daily'}}))

    def test_patch_touches_schedule_when_resuming(self):
        self.assertTrue(_patch_touches_schedule({'schedule_paused': False}))

    def test_patch_touches_schedule_skips_pause_and_unrelated_fields(self):
        self.assertFalse(_patch_touches_schedule({'schedule_paused': True}))
        self.assertFalse(_patch_touches_schedule({'name': 'Renamed'}))

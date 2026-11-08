from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.elttool.services.chunk_limit_resolver import (
    compute_connector_ceiling,
    normalize_plan_name,
    parse_memory_gb,
    resolve_chunk_size_django,
    resolve_default_connection_chunk_count,
    resolve_effective_max_chunk_for_plan,
)


def _plan(**kwargs):
    defaults = dict(
        max_chunk_size=50000,
        chunk_size_pct=Decimal("25.00"),
        memory_limit="8Gi",
        extra_config={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class ChunkLimitResolverTests(SimpleTestCase):
    def test_normalize_plan_name(self):
        self.assertEqual(normalize_plan_name("Standard"), "standard")
        self.assertEqual(normalize_plan_name(None), "free")

    def test_parse_memory_gb(self):
        self.assertEqual(parse_memory_gb("8Gi"), 8.0)
        self.assertEqual(parse_memory_gb("16G"), 16.0)
        self.assertAlmostEqual(parse_memory_gb("512Mi"), 0.5)
        self.assertEqual(parse_memory_gb(""), 8.0)

    def test_connector_ceiling_normal_etl_uses_source(self):
        self.assertEqual(
            compute_connector_ceiling(200000, 50000, is_reverse_etl=False),
            200000,
        )

    def test_connector_ceiling_reverse_etl_uses_dest(self):
        self.assertEqual(
            compute_connector_ceiling(50000, 500, is_reverse_etl=True),
            500,
        )

    def test_chunk_pct_dynamics(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=200000,
            plan_config=_plan(chunk_size_pct=Decimal("25.00")),
            src_max=200000,
            dst_max=50000,
            is_reverse_etl=False,
        )
        self.assertEqual(result.plan_natural_chunk, 50000)
        self.assertEqual(result.effective_chunk, 50000)

    def test_chunk_pct_salesforce_reverse_etl(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=10000,
            plan_config=_plan(chunk_size_pct=Decimal("25.00")),
            src_max=50000,
            dst_max=500,
            is_reverse_etl=True,
        )
        self.assertEqual(result.plan_natural_chunk, 125)
        self.assertEqual(result.effective_chunk, 125)

    def test_chunk_absolute_fallback_no_ceiling(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=0,
            plan_config=_plan(chunk_size_pct=None, max_chunk_size=25000),
            src_max=None,
            dst_max=None,
        )
        self.assertEqual(result.path, "absolute_fallback")
        self.assertEqual(result.effective_chunk, 100)

    def test_chunk_clamped_to_connection(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=30000,
            plan_config=_plan(chunk_size_pct=Decimal("100.00")),
            src_max=200000,
            dst_max=50000,
            is_reverse_etl=False,
        )
        self.assertEqual(result.effective_chunk, 30000)

    def test_chunk_floor_guard(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=40,
            plan_config=_plan(chunk_size_pct=Decimal("25.00")),
            src_max=160,
            is_reverse_etl=False,
        )
        self.assertEqual(result.effective_chunk, 100)

    def test_chunk_override_caps(self):
        override = SimpleNamespace(max_chunk_size=40000)
        result = resolve_chunk_size_django(
            connection_chunk_count=100000,
            plan_config=_plan(chunk_size_pct=Decimal("100.00")),
            src_max=100000,
            is_reverse_etl=False,
            override=override,
        )
        self.assertEqual(result.effective_chunk, 40000)

    @patch("apps.elttool.models.PlanResourceConfig")
    def test_resolve_default_connection_chunk_count(self, mock_prc):
        company = SimpleNamespace(plan="Standard", resource_override=None)
        source = SimpleNamespace(min_count=1, max_count=10000, is_rtl=True)
        dest = SimpleNamespace(min_count=1, max_count=500)
        mock_prc.objects.get.return_value = _plan(
            chunk_size_pct=Decimal("25.00"),
            max_chunk_size=50000,
        )
        chunk = resolve_default_connection_chunk_count(company, source, dest)
        self.assertEqual(chunk, 125)

    def test_effective_max_for_plan(self):
        plan = _plan(chunk_size_pct=Decimal("25.00"), max_chunk_size=50000)
        effective = resolve_effective_max_chunk_for_plan(
            plan, 10000, 200000, is_reverse_etl=False
        )
        self.assertEqual(effective, 10000)

    def test_free_plan_dynamics_default_chunk(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=50000,
            plan_config=_plan(max_chunk_size=50000, chunk_size_pct=Decimal("25.00")),
            src_max=200000,
            dst_max=50000,
            is_reverse_etl=False,
        )
        self.assertEqual(result.plan_natural_chunk, 50000)
        self.assertEqual(result.effective_chunk, 50000)

    def test_connector_ceiling_falls_back_to_connection_chunk(self):
        self.assertEqual(
            compute_connector_ceiling(
                None,
                500,
                is_reverse_etl=False,
                connection_chunk_count=200000,
            ),
            200000,
        )

    def test_enterprise_100pct_dynamics_175k(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=200000,
            plan_config=_plan(
                max_chunk_size=175000,
                chunk_size_pct=Decimal("100.00"),
            ),
            src_max=200000,
            dst_max=50000,
            is_reverse_etl=False,
        )
        self.assertEqual(result.path, "percentage")
        self.assertEqual(result.connector_ceiling, 200000)
        self.assertEqual(result.plan_natural_chunk, 175000)
        self.assertEqual(result.effective_chunk, 175000)

    def test_enterprise_100pct_uses_connection_chunk_when_src_max_null(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=200000,
            plan_config=_plan(
                max_chunk_size=175000,
                chunk_size_pct=Decimal("100.00"),
            ),
            src_max=None,
            dst_max=50000,
            is_reverse_etl=False,
        )
        self.assertEqual(result.path, "percentage")
        self.assertEqual(result.connector_ceiling, 200000)
        self.assertEqual(result.plan_natural_chunk, 175000)
        self.assertEqual(result.effective_chunk, 175000)

    def test_enterprise_plan_dynamics_clamped_to_snowflake_safe_max(self):
        result = resolve_chunk_size_django(
            connection_chunk_count=200000,
            plan_config=_plan(
                max_chunk_size=200000,
                chunk_size_pct=Decimal("100.00"),
            ),
            src_max=200000,
            dst_max=50000,
            is_reverse_etl=False,
        )
        self.assertEqual(result.connector_ceiling, 200000)
        self.assertEqual(result.plan_natural_chunk, 175000)
        self.assertEqual(result.effective_chunk, 175000)

    @patch("apps.elttool.services.chunk_limit_resolver.resolve_default_connection_chunk_count")
    @patch("apps.elttool.models.Connection")
    def test_sync_connection_chunks_on_plan_change(self, mock_conn_cls, mock_resolve):
        from apps.elttool.models import sync_connection_chunks_on_plan_change

        company = SimpleNamespace(cmp_id=1, plan="Enterprise")
        src = SimpleNamespace(min_count=1, max_count=10000)
        dst = SimpleNamespace(min_count=1, max_count=500)
        conn = SimpleNamespace(
            pk=42,
            chunk_count=125,
            src_config=SimpleNamespace(src=src),
            dst_config=SimpleNamespace(dst=dst),
        )
        mock_conn_cls.objects.filter.return_value.select_related.return_value = [conn]
        mock_resolve.return_value = 500

        sync_connection_chunks_on_plan_change(
            sender=None,
            instance=company,
            created=False,
            update_fields=["plan"],
        )

        mock_resolve.assert_called_once_with(company, src, dst)
        mock_conn_cls.objects.filter.return_value.update.assert_called_once_with(
            chunk_count=500,
        )

    @patch("apps.elttool.services.chunk_limit_resolver.resolve_default_connection_chunk_count")
    @patch("apps.elttool.models.Connection")
    def test_sync_skips_when_plan_not_in_update_fields(self, mock_conn_cls, mock_resolve):
        from apps.elttool.models import sync_connection_chunks_on_plan_change

        sync_connection_chunks_on_plan_change(
            sender=None,
            instance=SimpleNamespace(cmp_id=1),
            created=False,
            update_fields=["pod_count"],
        )
        mock_conn_cls.objects.filter.assert_not_called()
        mock_resolve.assert_not_called()

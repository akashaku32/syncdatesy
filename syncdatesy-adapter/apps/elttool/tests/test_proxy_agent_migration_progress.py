"""Tests for proxy agent migration progress ingest."""

import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.elttool.models import (
    Company,
    Connection,
    DestinationConfig,
    DestinationModel,
    Log,
    MigrationRecord,
    Plan,
    ProxyAgent,
    Role,
    SourceConfig,
    SourceModel,
    SourceTableSelection,
    User,
)
from apps.elttool.services.proxy_agent_migration_progress import (
    ProgressAuthError,
    ProgressValidationError,
    apply_progress_event,
    hash_progress_signing_secret,
    ingest_proxy_agent_progress,
    verify_agent_progress_signature,
)
from apps.elttool.v1.proxy_agent_progress_views import (
    InternalProxyAgentMigrationProgressAPIView,
)
from apps.utils.encryption import encrypt_field


@override_settings(INTERNAL_API_KEY="test-internal-key")
class ProxyAgentMigrationProgressTests(TestCase):
    def setUp(self):
        Plan.objects.get_or_create(plan_id=1, defaults={"name": "Free", "pod_count": 2})
        role, _ = Role.objects.get_or_create(role_name="Administrator", defaults={})
        now = timezone.now()
        self.company = Company.objects.create(
            cmp_name="Proxy Progress Co",
            start_date=now,
            end_date=now,
        )
        self.user = User.objects.create(
            company_email="proxy-progress@example.com",
            cmp=self.company,
            first_name="Proxy",
            last_name="User",
            roles=role,
        )
        self.src_model = SourceModel.objects.create(
            name="SAPOData",
            display_name="SAP OData",
        )
        self.dst_model = DestinationModel.objects.create(name="ADLS")
        self.src = SourceConfig.objects.create(
            name="sap",
            cmp=self.company,
            src=self.src_model,
            is_active=True,
        )
        self.dst = DestinationConfig.objects.create(
            name="adls",
            cmp=self.company,
            dst=self.dst_model,
            is_active=True,
        )
        self.connection = Connection.objects.create(
            cmp=self.company,
            src_config=self.src,
            dst_config=self.dst,
            dst_schema="LANDING",
            status="A",
            created_by=self.user,
            updated_by=self.user,
        )
        self.raw_secret = "a" * 64
        self.agent = ProxyAgent.objects.create(
            agent_name="Agent",
            agent_uid=f"agent-{self.company.cmp_id}-deadbeefcafebabe",
            cmp=self.company,
            progress_signing_secret_hash=hash_progress_signing_secret(self.raw_secret),
            progress_signing_secret=encrypt_field(self.raw_secret, self.company.cmp_id),
            created_by=self.user,
        )
        self.session_id = 9001
        self.job_name = "pipeline_run_1_node_2"
        for table in ("EntityA", "EntityB"):
            MigrationRecord.objects.create(
                connection=self.connection,
                migration_session_id=self.session_id,
                table_name=table,
                job_name=self.job_name,
                status="in_progress",
            )
            SourceTableSelection.objects.create(
                connection=self.connection,
                table=table,
                selected=True,
                status="in_progress",
                table_fields={},
            )
        Log.objects.create(
            message="Migration in progress",
            cmp=self.company,
            connection=self.connection,
            user=self.user,
            status="I",
            session=self.session_id,
            job_name=self.job_name,
        )
        self.factory = APIRequestFactory()

    def _sign(self, body: dict):
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(
            self.raw_secret.encode("utf-8"),
            f"{ts}.".encode("utf-8") + raw,
            hashlib.sha256,
        ).hexdigest()
        return raw, ts, sig

    def test_hmac_rejects_bad_signature(self):
        body = {"event": "table_started"}
        raw, ts, _ = self._sign(body)
        with self.assertRaises(ProgressAuthError):
            verify_agent_progress_signature(
                self.agent,
                timestamp_header=ts,
                signature_header="0" * 64,
                raw_body=raw,
            )

    @patch(
        "apps.elttool.services.proxy_agent_migration_progress._trigger_ws",
        lambda *a, **k: None,
    )
    def test_table_completed_then_finalize(self):
        result = None
        for table in ("EntityA", "EntityB"):
            result = apply_progress_event(
                agent=self.agent,
                body={
                    "event": "table_completed",
                    "migration_session_id": self.session_id,
                    "job_name": self.job_name,
                    "connection_id": self.connection.connection_id,
                    "table_name": table,
                    "rows": 10,
                    "user_id": self.user.user_id,
                },
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["overall_status"], "completed")
        self.assertEqual(result["tables_remaining"], 0)
        self.assertFalse(
            MigrationRecord.objects.filter(
                migration_session_id=self.session_id,
                status="in_progress",
            ).exists()
        )
        terminal = Log.objects.filter(
            connection=self.connection,
            job_name=self.job_name,
            migration_record_id__isnull=True,
        ).exclude(status="I")
        self.assertTrue(terminal.exists())
        self.assertEqual(terminal.first().status, "S")

    @patch(
        "apps.elttool.services.proxy_agent_migration_progress._trigger_ws",
        lambda *a, **k: None,
    )
    def test_cross_tenant_rejected(self):
        now = timezone.now()
        other = Company.objects.create(
            cmp_name="Other Co",
            start_date=now,
            end_date=now,
        )
        other_agent = ProxyAgent.objects.create(
            agent_name="OtherAgent",
            agent_uid=f"agent-{other.cmp_id}-1111222233334444",
            cmp=other,
            progress_signing_secret_hash=hash_progress_signing_secret(self.raw_secret),
            progress_signing_secret=encrypt_field(self.raw_secret, other.cmp_id),
        )
        with self.assertRaises(ProgressValidationError):
            apply_progress_event(
                agent=other_agent,
                body={
                    "event": "table_started",
                    "migration_session_id": self.session_id,
                    "job_name": self.job_name,
                    "connection_id": self.connection.connection_id,
                    "table_name": "EntityA",
                },
            )

    @patch(
        "apps.elttool.services.proxy_agent_migration_progress._trigger_ws",
        lambda *a, **k: None,
    )
    def test_internal_view_accepts_signed_request(self):
        body = {
            "event": "table_started",
            "migration_session_id": self.session_id,
            "job_name": self.job_name,
            "connection_id": self.connection.connection_id,
            "table_name": "EntityA",
            "idempotency_key": "idem-1",
        }
        raw, ts, sig = self._sign(body)
        request = self.factory.post(
            "/api/v1/internal/proxy-agent/migration-progress/",
            data=raw,
            content_type="application/json",
            HTTP_X_INTERNAL_KEY="test-internal-key",
            HTTP_X_AGENT_UID=self.agent.agent_uid,
            HTTP_X_AGENT_TIMESTAMP=ts,
            HTTP_X_AGENT_SIGNATURE=sig,
            HTTP_IDEMPOTENCY_KEY="idem-1",
        )
        view = InternalProxyAgentMigrationProgressAPIView.as_view()
        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["accepted"])

    @patch(
        "apps.elttool.services.proxy_agent_migration_progress._trigger_ws",
        lambda *a, **k: None,
    )
    def test_idempotency_returns_cached(self):
        body = {
            "event": "table_progress",
            "migration_session_id": self.session_id,
            "job_name": self.job_name,
            "connection_id": self.connection.connection_id,
            "table_name": "EntityA",
            "rows": 5,
            "idempotency_key": "idem-progress-1",
        }
        raw, ts, sig = self._sign(body)
        first = ingest_proxy_agent_progress(
            agent_uid=self.agent.agent_uid,
            body=body,
            raw_body=raw,
            timestamp_header=ts,
            signature_header=sig,
            idempotency_key="idem-progress-1",
        )
        second = ingest_proxy_agent_progress(
            agent_uid=self.agent.agent_uid,
            body=body,
            raw_body=raw,
            timestamp_header=ts,
            signature_header=sig,
            idempotency_key="idem-progress-1",
        )
        self.assertEqual(first, second)

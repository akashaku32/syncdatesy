from django.test import TestCase
from django.utils import timezone

from apps.elttool.models import (
    Company,
    Connection,
    DestinationConfig,
    DestinationModel,
    MigrationBatch,
    Pipeline,
    PipelineNode,
    Plan,
    Role,
    SourceConfig,
    SourceModel,
    User,
)
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.elttool.services.pipeline_batch_guards import (
    BATCH_IN_PIPELINE_DELETE_MSG,
    CONNECTION_BATCHES_IN_PIPELINE_DELETE_MSG,
    batch_is_in_pipeline,
    connection_has_batches_in_pipeline,
    list_connection_pipeline_batch_usage,
)
from apps.elttool.v1.pipeline_views import PipelineConnectionsView, PipelineNodesView


class PipelineBatchGuardTests(TestCase):
    def setUp(self):
        Plan.objects.get_or_create(plan_id=1, defaults={"name": "Free", "pod_count": 2})
        role, _ = Role.objects.get_or_create(
            role_name="Administrator",
            defaults={},
        )
        now = timezone.now()
        self.company = Company.objects.create(
            cmp_name="Pipeline Guard Co",
            start_date=now,
            end_date=now,
        )
        self.user = User.objects.create(
            company_email="guard@example.com",
            cmp=self.company,
            first_name="Guard",
            last_name="User",
            roles=role,
        )
        self.source_model = SourceModel.objects.create(
            name="MicrosoftDynamics365_FO",
            display_name="Dynamics",
        )
        self.dest_model = DestinationModel.objects.create(name="Databricks")
        self.source_config = SourceConfig.objects.create(
            name="src",
            cmp=self.company,
            src=self.source_model,
            is_active=True,
        )
        self.dest_config = DestinationConfig.objects.create(
            name="dest",
            cmp=self.company,
            dst=self.dest_model,
            is_active=True,
        )
        self.connection = Connection.objects.create(
            cmp=self.company,
            src_config=self.source_config,
            dst_config=self.dest_config,
            dst_schema="LANDING",
            status="A",
            created_by=self.user,
            updated_by=self.user,
        )
        self.batch = MigrationBatch.objects.create(
            connection=self.connection,
            name="Batch 1",
            time_frequency="15",
            execution_order="parallel",
        )
        self.pipeline = Pipeline.objects.create(
            cmp=self.company,
            name="Main Pipeline",
            status="active",
            created_by=self.user,
        )
        PipelineNode.objects.create(
            pipeline=self.pipeline,
            node_kind="start",
            connection=self.connection,
            node_label="Start",
        )
        PipelineNode.objects.create(
            pipeline=self.pipeline,
            node_kind="batch",
            connection=self.connection,
            batch=self.batch,
            node_label="Batch 1",
        )

    def test_batch_in_pipeline_detected(self):
        self.assertTrue(batch_is_in_pipeline(self.batch))
        usage = list_connection_pipeline_batch_usage(self.connection)
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["batch_name"], "Batch 1")
        self.assertEqual(usage[0]["pipeline_name"], "Main Pipeline")
        self.assertTrue(connection_has_batches_in_pipeline(self.connection))

    def test_batch_not_in_pipeline_when_removed_from_canvas(self):
        PipelineNode.objects.filter(batch=self.batch, node_kind="batch").delete()
        self.assertFalse(batch_is_in_pipeline(self.batch))
        self.assertFalse(connection_has_batches_in_pipeline(self.connection))

    def test_guard_messages_are_pipeline_specific(self):
        self.assertIn("Scheduling", BATCH_IN_PIPELINE_DELETE_MSG)
        self.assertIn("Scheduling", CONNECTION_BATCHES_IN_PIPELINE_DELETE_MSG)
        self.assertNotIn("pause", BATCH_IN_PIPELINE_DELETE_MSG.lower())
        self.assertNotIn("active", CONNECTION_BATCHES_IN_PIPELINE_DELETE_MSG.lower())

    def test_pipeline_connections_excludes_deleted_connections(self):
        deleted_connection = Connection.objects.create(
            cmp=self.company,
            src_config=self.source_config,
            dst_config=self.dest_config,
            dst_schema="LANDING",
            status="D",
            created_by=self.user,
            updated_by=self.user,
        )
        MigrationBatch.objects.create(
            connection=deleted_connection,
            name="Deleted Batch",
            time_frequency="15",
            execution_order="parallel",
        )

        factory = APIRequestFactory()
        request = factory.get("/api/v1/pipelines/connections/")
        force_authenticate(request, user=self.user)
        response = PipelineConnectionsView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        connection_ids = {row["connection_id"] for row in response.data["connections"]}
        self.assertIn(self.connection.connection_id, connection_ids)
        self.assertNotIn(deleted_connection.connection_id, connection_ids)

    def test_pipeline_connections_marks_batches_in_use(self):
        factory = APIRequestFactory()
        request = factory.get("/api/v1/pipelines/connections/")
        force_authenticate(request, user=self.user)
        response = PipelineConnectionsView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        conn_row = next(
            row
            for row in response.data["connections"]
            if row["connection_id"] == self.connection.connection_id
        )
        batch_row = next(b for b in conn_row["batches"] if b["id"] == self.batch.id)
        self.assertTrue(batch_row["in_pipeline"])
        self.assertEqual(batch_row["pipeline_id"], self.pipeline.id)
        self.assertEqual(batch_row["pipeline_name"], "Main Pipeline")

    def test_cannot_add_batch_already_used_on_another_pipeline(self):
        other = Pipeline.objects.create(
            cmp=self.company,
            name="Other Pipeline",
            status="active",
            created_by=self.user,
        )
        factory = APIRequestFactory()
        request = factory.post(
            f"/api/v1/pipelines/{other.id}/nodes/",
            {
                "batch_id": self.batch.id,
                "connection_id": self.connection.connection_id,
                "x": 10,
                "y": 20,
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = PipelineNodesView.as_view()(request, pipeline_id=other.id)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Main Pipeline", response.data["error"])
        self.assertEqual(response.data["pipeline_id"], self.pipeline.id)

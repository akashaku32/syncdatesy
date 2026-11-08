"""
Remove per-batch APScheduler jobs (conn_*_batch_*) for batches in pipelines.

Pipeline-only scheduling uses pipeline_{id} jobs; legacy batch jobs are obsolete.
"""

from django.core.management.base import BaseCommand

from apps.elttool.models import MigrationBatch, PipelineNode
from apps.elttool.v1.batch_views import _remove_batch_job


class Command(BaseCommand):
    help = "Remove legacy per-batch scheduler jobs for batches assigned to pipelines."

    def handle(self, *args, **options):
        batch_ids = (
            PipelineNode.objects.values_list('batch_id', flat=True).distinct()
        )
        batches = MigrationBatch.objects.filter(id__in=batch_ids)
        count = 0
        for batch in batches:
            try:
                _remove_batch_job(batch)
                count += 1
                self.stdout.write(f"  cleared legacy job for batch {batch.id} ({batch.name})")
            except Exception as exc:
                self.stderr.write(f"  failed batch {batch.id}: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Done. Processed {count} batch(es)."))

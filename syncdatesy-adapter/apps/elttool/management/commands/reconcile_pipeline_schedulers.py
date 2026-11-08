from django.core.management.base import BaseCommand

from apps.elttool.services.pipeline_scheduler_reconcile import (
    reconcile_all_pipeline_schedulers,
)


class Command(BaseCommand):
    help = (
        'Re-register APScheduler pipeline_* jobs from the pipeline table. '
        'Run after scheduler rollout or when scheduled flows stop firing.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Count pipelines only; do not call the scheduler service.',
        )
        parser.add_argument(
            '--pipeline-id',
            type=int,
            action='append',
            dest='pipeline_ids',
            help='Limit reconcile to one or more pipeline IDs (repeatable).',
        )

    def handle(self, *args, **options):
        stats = reconcile_all_pipeline_schedulers(
            pipeline_ids=options.get('pipeline_ids'),
            dry_run=options['dry_run'],
        )
        if options['dry_run']:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {stats['total']} pipeline(s) would be reconciled."
                )
            )
            return

        if stats['errors']:
            for err in stats['errors']:
                self.stderr.write(
                    f"  pipeline {err['pipeline_id']}: {err['error']}"
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {stats['synced']}/{stats['total']} pipeline(s); "
                f"{len(stats['errors'])} error(s)."
            )
        )
        if stats['errors']:
            raise SystemExit(1)

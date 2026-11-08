"""
Backfill full canvas edge snapshots onto historical PipelineRun rows.

Older runs only stored participating node IDs, so the Flow history view showed
batches without edges. This rebuilds edge_pairs + graph_snapshot from the denser
of published vs live draft topology among each run's participating nodes.

Usage:
  python manage.py backfill_pipeline_run_graph_snapshots --dry-run
  python manage.py backfill_pipeline_run_graph_snapshots
  python manage.py backfill_pipeline_run_graph_snapshots --pipeline-id 11 --force
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.elttool.models import PipelineEdge, PipelineNode, PipelineRun
from apps.elttool.v1.pipeline_graph import get_start_node
from apps.elttool.v1.pipeline_views import (
    _backfill_run_graph_snapshot,
    _pipeline_run_participating_node_ids,
)


class Command(BaseCommand):
    help = "Rebuild missing/sparse historic pipeline-run graph snapshots for Flow layout."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be updated without writing.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rebuild even when a non-sparse snapshot already exists.",
        )
        parser.add_argument(
            "--pipeline-id",
            type=int,
            default=None,
            help="Limit to one pipeline id.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max number of runs to process.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        force = bool(options["force"])
        pipeline_id = options["pipeline_id"]
        limit = options["limit"]

        qs = (
            PipelineRun.objects.select_related("pipeline")
            .order_by("-id")
        )
        if pipeline_id is not None:
            qs = qs.filter(pipeline_id=pipeline_id)
        if limit is not None:
            qs = qs[: max(0, int(limit))]

        scanned = 0
        updated = 0
        skipped = 0

        for run in qs.iterator(chunk_size=50):
            scanned += 1
            pipeline = run.pipeline
            live_nodes = list(
                PipelineNode.objects.filter(pipeline=pipeline)
                .select_related("batch")
                .order_by("order_index")
            )
            live_edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
            meta = run.metadata if isinstance(run.metadata, dict) else {}
            participating = set(
                _pipeline_run_participating_node_ids(
                    meta, pipeline, live_nodes, run_status=run.status
                )
            )
            start = get_start_node(live_nodes)
            start_id = start.id if start else None

            if dry_run:
                from apps.elttool.v1.pipeline_views import (
                    _frozen_run_graph_is_sparse,
                    _infer_legacy_run_edges,
                    _normalize_run_edge_pairs,
                    _filter_edges_to_allowed,
                )

                existing_raw = meta.get("edge_pairs")
                if not existing_raw and isinstance(meta.get("graph_snapshot"), dict):
                    existing_raw = (meta.get("graph_snapshot") or {}).get("edges")
                allowed = set(participating)
                if start_id is not None:
                    allowed.add(int(start_id))
                existing = _filter_edges_to_allowed(
                    _normalize_run_edge_pairs(existing_raw),
                    allowed,
                )
                needs = force or _frozen_run_graph_is_sparse(
                    existing, participating, start_id
                )
                inferred = _infer_legacy_run_edges(
                    meta, pipeline, participating, live_edges, start_id, live_nodes=live_nodes
                )
                if needs and inferred:
                    updated += 1
                    self.stdout.write(
                        f"would update run={run.id} pipeline={pipeline.id} "
                        f"edges={len(existing)}->{len(inferred)} "
                        f"nodes={len(participating)}"
                    )
                else:
                    skipped += 1
                continue

            _meta, _edges, wrote = _backfill_run_graph_snapshot(
                run,
                meta,
                pipeline,
                participating,
                live_edges,
                start_node_id=start_id,
                live_nodes=live_nodes,
                force=force,
            )
            if wrote:
                updated += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"updated run={run.id} pipeline={pipeline.id} "
                        f"edges={len(_edges)} nodes={len(participating)}"
                    )
                )
            else:
                skipped += 1

        self.stdout.write(
            self.style.NOTICE(
                f"done scanned={scanned} updated={updated} skipped={skipped} "
                f"dry_run={dry_run} force={force}"
            )
        )

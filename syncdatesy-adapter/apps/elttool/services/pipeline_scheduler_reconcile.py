"""
Rebuild APScheduler pipeline_* jobs from Django pipeline rows.

Pipeline schedule config lives in the ``pipeline`` table; runnable timers live in
``apscheduler_jobs``. This module re-syncs them (e.g. after scheduler rollout).
"""
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def reconcile_on_start_enabled() -> bool:
    raw = os.environ.get('PIPELINE_SCHEDULER_RECONCILE_ON_START', 'true')
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def reconcile_all_pipeline_schedulers(
    *,
    pipeline_ids: Optional[List[int]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    from apps.elttool.models import Pipeline
    from apps.elttool.v1.pipeline_views import _sync_pipeline_scheduler

    qs = Pipeline.objects.all().order_by('id')
    if pipeline_ids:
        qs = qs.filter(id__in=pipeline_ids)

    stats: Dict[str, Any] = {
        'total': 0,
        'synced': 0,
        'dry_run': dry_run,
        'errors': [],
    }

    for pipeline in qs.iterator():
        stats['total'] += 1
        if dry_run:
            continue
        try:
            _sync_pipeline_scheduler(pipeline)
            stats['synced'] += 1
        except Exception as exc:
            logger.exception(
                'PIPELINE_SCHEDULER_RECONCILE_FAILED | pipeline=%s',
                pipeline.id,
            )
            stats['errors'].append(
                {'pipeline_id': pipeline.id, 'error': str(exc)},
            )

    if not dry_run:
        logger.info(
            'PIPELINE_SCHEDULER_RECONCILE_DONE | total=%s | synced=%s | errors=%s',
            stats['total'],
            stats['synced'],
            len(stats['errors']),
        )
    return stats

"""Email notifications for pipeline flow finish and per-batch completion."""
from __future__ import annotations

import logging
import re
from datetime import timezone as dt_timezone
from typing import Iterable, List, Optional

from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils import timezone

from apps.elttool.models import Pipeline, PipelineNode, PipelineRun
from apps.elttool.services.pipeline_email_templates import (
    build_pipeline_batch_email,
    build_pipeline_flow_email,
    parse_batch_rows_from_run_metadata,
)
from apps.elttool.v1.views import _build_company_email_group_payload

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def normalize_notification_emails(raw_value) -> List[str]:
    if raw_value in (None, ''):
        return []
    if isinstance(raw_value, str):
        candidates = [part.strip() for part in raw_value.split(',')]
    elif isinstance(raw_value, list):
        candidates = [str(item or '').strip() for item in raw_value]
    else:
        raise ValueError('notification_emails must be a list of email strings')

    normalized: List[str] = []
    seen = set()
    for email in candidates:
        if not email:
            continue
        if not _EMAIL_RE.match(email):
            raise ValueError(f'Invalid email address: {email}')
        lowered = email.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(email)
    return normalized


def resolve_pipeline_recipients(pipeline: Pipeline) -> List[str]:
    payload = _build_company_email_group_payload(
        pipeline.cmp,
        pipeline.notification_email_group_ids or [],
    )
    recipients: List[str] = list(payload.get('notification_emails') or [])
    seen = {email.lower() for email in recipients}
    for email in pipeline.notification_emails or []:
        normalized = str(email or '').strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        recipients.append(normalized)
    return recipients


def _notification_state(run: PipelineRun) -> dict:
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    notifications = metadata.get('notifications')
    return notifications if isinstance(notifications, dict) else {}


def _persist_notification_state(run: PipelineRun, notifications: dict) -> None:
    metadata = dict(run.metadata or {})
    metadata['notifications'] = notifications
    run.metadata = metadata
    run.save(update_fields=['metadata'])


def _flow_notification_already_sent(run: PipelineRun) -> bool:
    return bool(_notification_state(run).get('flow_sent'))


def _batch_notification_already_sent(run: PipelineRun, node_id: int) -> bool:
    sent_ids = _notification_state(run).get('batch_sent_node_ids') or []
    return node_id in sent_ids


def _mark_flow_notification_sent(run: PipelineRun) -> None:
    state = dict(_notification_state(run))
    state['flow_sent'] = True
    _persist_notification_state(run, state)


def _mark_batch_notification_sent(run: PipelineRun, node_id: int) -> None:
    state = dict(_notification_state(run))
    sent_ids = list(state.get('batch_sent_node_ids') or [])
    if node_id not in sent_ids:
        sent_ids.append(node_id)
    state['batch_sent_node_ids'] = sent_ids
    _persist_notification_state(run, state)


def _format_duration(started_at: Optional[str], finished_at: Optional[str]) -> str:
    if not started_at or not finished_at:
        return 'N/A'
    try:
        start = timezone.datetime.fromisoformat(started_at.replace('Z', '+00:00'))
        end = timezone.datetime.fromisoformat(finished_at.replace('Z', '+00:00'))
        if timezone.is_naive(start):
            start = timezone.make_aware(start, dt_timezone.utc)
        if timezone.is_naive(end):
            end = timezone.make_aware(end, dt_timezone.utc)
        delta = end - start
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f'{total_seconds}s'
        minutes, seconds = divmod(total_seconds, 60)
        if minutes < 60:
            return f'{minutes}m {seconds}s'
        hours, minutes = divmod(minutes, 60)
        return f'{hours}h {minutes}m'
    except (TypeError, ValueError):
        return 'N/A'


def _send_email(subject: str, text_body: str, html_body: str, recipients: Iterable[str]) -> None:
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or 'noreply@datasyncher.com'
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=list(recipients),
    )
    message.attach_alternative(html_body, 'text/html')
    message.send(fail_silently=False)


def send_pipeline_flow_notification(run: PipelineRun) -> None:
    pipeline = Pipeline.objects.select_related('cmp').get(id=run.pipeline_id)
    run.pipeline = pipeline
    if not pipeline.notify_on_flow_finish:
        return
    if _flow_notification_already_sent(run):
        return

    recipients = resolve_pipeline_recipients(pipeline)
    if not recipients:
        return

    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    error_message = metadata.get('error') if run.status == 'failed' else None
    batch_rows = parse_batch_rows_from_run_metadata(metadata)

    subject, text_body, html_body = build_pipeline_flow_email(
        pipeline_name=pipeline.name or f'Pipeline {pipeline.id}',
        run_id=run.id,
        run_status=run.status or 'unknown',
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_message=error_message,
        batch_rows=batch_rows,
    )

    try:
        _send_email(subject, text_body, html_body, recipients)
        _mark_flow_notification_sent(run)
    except Exception:
        logger.exception('Failed to send pipeline flow notification for run %s', run.id)


def send_pipeline_batch_notification(run: PipelineRun, node: PipelineNode) -> None:
    if not node.notify_on_completion:
        return
    if _batch_notification_already_sent(run, node.id):
        return

    pipeline = Pipeline.objects.select_related('cmp').get(id=run.pipeline_id)
    recipients = resolve_pipeline_recipients(pipeline)
    if not recipients:
        return

    batch_name = node.batch.name if node.batch_id else (node.node_label or f'Node {node.id}')
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    node_meta = (metadata.get('nodes') or {}).get(str(node.id), {})
    table_count = node_meta.get('table_count')
    finished_at = node_meta.get('finished_at')
    started_at = node_meta.get('started_at')
    duration = _format_duration(started_at, finished_at)

    subject, text_body, html_body = build_pipeline_batch_email(
        pipeline_name=pipeline.name or f'Pipeline {pipeline.id}',
        run_id=run.id,
        batch_name=batch_name,
        table_count=table_count,
        duration=duration,
        started_at=started_at,
        finished_at=finished_at,
    )

    try:
        _send_email(subject, text_body, html_body, recipients)
        _mark_batch_notification_sent(run, node.id)
    except Exception:
        logger.exception(
            'Failed to send pipeline batch notification for run %s node %s',
            run.id,
            node.id,
        )

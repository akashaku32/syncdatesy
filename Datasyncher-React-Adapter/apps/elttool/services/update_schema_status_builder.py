from apps.elttool.models import Connection, MigrationRecord
from django.utils.timezone import now


def build_update_schema_status(connection_id, include_celery_status=True, celery_app=None):
    """
    Builds update-schema job status response.
    Output is IDENTICAL to UpdateSchemaStatusAPIView.
    """

    try:
        connection = Connection.objects.get(connection_id=connection_id)
    except Connection.DoesNotExist:
        return None, {
            'message': 'Connection with the given ID does not exist'
        }

    current_job_record = MigrationRecord.objects.filter(
        connection=connection,
        table_name__isnull=True,
        job_name__contains='listing_tables',
        status='in_progress'
    ).order_by('-created_at').first()

    is_in_progress = current_job_record is not None
    current_job = None

    celery_task_status = None
    if (
        include_celery_status
        and current_job_record
        and current_job_record.task_id
        and celery_app
    ):
        try:
            task = celery_app.AsyncResult(current_job_record.task_id)
            celery_task_status = {
                'state': task.state,
                'ready': task.ready(),
                'successful': task.successful() if task.ready() else None,
                'failed': task.failed() if task.ready() else None,
            }
        except Exception as e:
            celery_task_status = {
                'state': 'UNKNOWN',
                'ready': False,
                'successful': None,
                'failed': None,
                'error': str(e)
            }

    if current_job_record:
        current_job = {
            'job_name': current_job_record.job_name,
            'task_id': current_job_record.task_id,
            'status': current_job_record.status,
            'migration_session_id': current_job_record.migration_session_id,
            'created_at': current_job_record.created_at.isoformat() if current_job_record.created_at else None,
            'updated_at': current_job_record.updated_at.isoformat() if current_job_record.updated_at else None,
        }

        if celery_task_status:
            current_job['celery_task_status'] = celery_task_status

    return {
        'is_in_progress': is_in_progress,
        'current_job': current_job,
    }, None

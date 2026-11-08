"""
Django client for interacting with the APScheduler service container.
This replaces direct scheduler access from views.
"""
import os
import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

def get_scheduler_service_url() -> str:
    """Resolve scheduler base URL (supports in-process reconcile on startup)."""
    return (
        getattr(settings, 'SCHEDULER_SERVICE_URL', None)
        or os.environ.get('SCHEDULER_SERVICE_URL')
        or 'http://scheduler-service:5000'
    )


class SchedulerClient:
    """Client for interacting with APScheduler service"""
    
    @staticmethod
    def _make_request(method, endpoint, **kwargs):
        """Make HTTP request to scheduler service"""
        url = f"{get_scheduler_service_url()}{endpoint}"
        try:
            if method == 'GET':
                response = requests.get(url, timeout=10)
            elif method == 'POST':
                response = requests.post(url, json=kwargs.get('json'), timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Scheduler service request failed: {e}")
            raise
    
    @staticmethod
    def add_job(job_id, func_path, trigger_type='interval', trigger_args=None, 
                args=(), kwargs=None, replace_existing=True, max_instances=1):
        """Add a new scheduled job"""
        return SchedulerClient._make_request(
            'POST',
            '/jobs/add',
            json={
                'job_id': job_id,
                'func_path': func_path,
                'trigger_type': trigger_type,
                'trigger_args': trigger_args or {},
                'args': args,
                'kwargs': kwargs or {},
                'replace_existing': replace_existing,
                'max_instances': max_instances
            }
        )
    
    @staticmethod
    def remove_job(job_id):
        """Remove a scheduled job. Missing jobs are already removed."""
        url = f"{get_scheduler_service_url()}/jobs/remove/{job_id}"
        try:
            response = requests.delete(url, timeout=10)
        except requests.exceptions.RequestException as e:
            logger.error(f"Scheduler service request failed: {e}")
            raise

        if response.status_code in (200, 404):
            try:
                return response.json()
            except ValueError:
                return {'status': 'not_found', 'job_id': job_id}

        message = ''
        try:
            message = str((response.json() or {}).get('message') or '')
        except ValueError:
            message = response.text or ''
        if response.status_code == 500 and 'No job by the id' in message:
            logger.info("Scheduler job %s was already absent", job_id)
            return {'status': 'not_found', 'job_id': job_id}

        logger.error(
            "Scheduler service request failed: %s Server Error for url: %s",
            response.status_code,
            url,
        )
        response.raise_for_status()
        return response.json()
    
    @staticmethod
    def get_job(job_id):
        """Get job information. Returns None if job not found (matches APScheduler behavior)"""
        url = f"{get_scheduler_service_url()}/jobs/get/{job_id}"
        try:
            response = requests.get(url, timeout=10)
            # 404 is a valid response when job doesn't exist - return None
            if response.status_code == 404:
                # Try to parse JSON if available, but return None regardless
                try:
                    result = response.json()
                    if result.get('status') == 'not_found':
                        return None
                except:
                    pass
                return None
            
            # For other status codes, raise if error
            response.raise_for_status()
            result = response.json()
            # Return None if job not found (to match APScheduler behavior)
            if result.get('status') == 'not_found':
                return None
            return result.get('job')
        except requests.exceptions.HTTPError as e:
            # If it's a 404, that's expected - job doesn't exist
            if hasattr(e, 'response') and e.response.status_code == 404:
                return None
            # For other HTTP errors, log and raise
            logger.error(f"Scheduler service HTTP error: {e}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Scheduler service request failed: {e}")
            raise
    
    @staticmethod
    def list_jobs():
        """List all scheduled jobs"""
        return SchedulerClient._make_request('GET', '/jobs/list')
    
    @staticmethod
    def reschedule_job(job_id, trigger_type='interval', trigger_args=None):
        """Reschedule an existing job"""
        return SchedulerClient._make_request(
            'POST',
            f'/jobs/reschedule/{job_id}',
            json={
                'trigger_type': trigger_type,
                'trigger_args': trigger_args or {}
            }
        )
    
    @staticmethod
    def health_check():
        """Check scheduler service health"""
        return SchedulerClient._make_request('GET', '/health')


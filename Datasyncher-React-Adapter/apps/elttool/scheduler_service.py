"""
Standalone APScheduler service that runs in a separate container.
This service exposes an HTTP API for Django workers to interact with the scheduler.
"""
import os
import sys
import django
import logging
import json
from datetime import datetime, timezone as datetime_timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

# Setup Django — project root must be on sys.path (not apps/ or apps/elttool/).
# Running `python apps/elttool/scheduler_service.py` puts apps/elttool on sys.path and
# shadows the `apps` package with apps/elttool/apps.py (Django AppConfig module).
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.getenv("DJANGO_SETTINGS_MODULE", "datasyncher.settings.developer"),
)
django.setup()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.events import (
    EVENT_JOB_EXECUTED,
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
)
from django.conf import settings
import redis

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Redis for distributed lock
redis_client = redis.StrictRedis.from_url(settings.REDIS_URL)

# Database URL for APScheduler job store
scheduler_db_url = os.environ.get("SCHEDULER_DB_URL") or os.environ.get("DATABASE_URL")
if not scheduler_db_url:
    raise RuntimeError(
        "Missing SCHEDULER_DB_URL (or DATABASE_URL) for scheduler job store. "
        "Set it via environment (docker `env_file: .env.developer`)."
    )

# Initialize APScheduler with PostgreSQL job store
jobstores = {
    'default': SQLAlchemyJobStore(
        url=scheduler_db_url
    )
}

executors = {
    'default': ThreadPoolExecutor(10)
}

scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors)

# Event listeners
# def job_executed_listener(event):
#     logger.info(f"Job {event.job_id} executed successfully.")
import threading
import itertools

JOB_FIRE_COUNTER = itertools.count(1)
def job_executed_listener(event):
    fire_no = next(JOB_FIRE_COUNTER)
    thread = threading.current_thread()

    logger.warning(
        "🔥 | EVENT=JOB_EXECUTED | fire_no=%s | pid=%s | thread_id=%s | thread_name=%s | "
        "time=%s | job_id=%s | scheduled_run_time=%s\n\n",
        fire_no,
        os.getpid(),
        thread.ident,
        thread.name,
        datetime.utcnow().isoformat(),
        event.job_id,
        event.scheduled_run_time,
    )

def job_error_listener(event):
    logger.error(f"Job {event.job_id} failed: {event.exception}")

def job_missed_listener(event):
    logger.warning(f"Job {event.job_id} missed its scheduled run.")

# Start scheduler
def start_scheduler():
    if not scheduler.running:
        scheduler.add_listener(job_executed_listener, EVENT_JOB_EXECUTED)
        scheduler.add_listener(job_error_listener, EVENT_JOB_ERROR)
        scheduler.add_listener(job_missed_listener, EVENT_JOB_MISSED)
        scheduler.start()
        logger.info("APScheduler started successfully.\n\n")
        
        # Log jobs loaded from database
        jobs = scheduler.get_jobs()
        if jobs:
            logger.info(f"Loaded {len(jobs)} job(s) from database: \n\n")
            for job in jobs:
                next_run = job.next_run_time.isoformat() if job.next_run_time else "Not scheduled"
                logger.info(f"  - Job ID: {job.id}, Next Run: {next_run} | \n\n ")
        else:
            logger.info("No jobs found in database (scheduler ready for new jobs) \n\n")
    else:
        logger.info("APScheduler already running.")

def _parse_schedule_bound(value, timezone_name):
    """Parse a schedule start/end. Naive values use the schedule timezone."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            raw = value.strip().replace('Z', '+00:00')
            if ' ' in raw and 'T' not in raw:
                raw = raw.replace(' ', 'T', 1)
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning("Could not parse schedule bound %s", value)
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed
    tz_name = (timezone_name or 'UTC').strip() or 'UTC'
    try:
        from zoneinfo import ZoneInfo
        return parsed.replace(tzinfo=ZoneInfo(tz_name))
    except Exception:
        logger.warning("Unknown schedule timezone %s; using UTC", timezone_name)
        return parsed.replace(tzinfo=datetime_timezone.utc)


# HTTP Request Handler
class SchedulerRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        if path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {
                'status': 'healthy',
                'scheduler_running': scheduler.running,
                'job_count': len(scheduler.get_jobs())
            }
            self.wfile.write(json.dumps(response).encode())
        
        elif path.startswith('/jobs/get/'):
            job_id = path.split('/jobs/get/')[1]
            try:
                job = scheduler.get_job(job_id)
                if job:
                    response = {
                        'status': 'success',
                        'job': {
                            'id': job.id,
                            'name': job.name,
                            'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                            'trigger': str(job.trigger)
                        }
                    }
                    self.send_response(200)
                else:
                    response = {'status': 'not_found'}
                    self.send_response(404)
                
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            except Exception as e:
                logger.error(f"Error getting job: {e}", exc_info=True)
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode())
        
        elif path == '/jobs/list':
            try:
                jobs = []
                for job in scheduler.get_jobs():
                    jobs.append({
                        'id': job.id,
                        'name': job.name,
                        'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                        'trigger': str(job.trigger)
                    })
                response = {'status': 'success', 'jobs': jobs, 'count': len(jobs)}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            except Exception as e:
                logger.error(f"Error listing jobs: {e}", exc_info=True)
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode())
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        """Handle POST requests"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        if path == '/jobs/add':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))

                logger.warning(
                    "🧨 JOB_ADD_REQUEST | pid=%s | time=%s | raw_payload=%s | \n\n",
                    os.getpid(),
                    datetime.utcnow().isoformat(),
                    data
                )

                
                job_id = data.get('job_id')
                func_path = data.get('func_path')  # e.g., 'apps.elttool.v1.views.task'
                trigger_type = data.get('trigger_type', 'interval')
                trigger_args = data.get('trigger_args') or {}
                job_args = data.get('args', ())
                job_kwargs = data.get('kwargs', {})
                # replace_existing = data.get('replace_existing', True)
                # max_instances = data.get('max_instances', 1)
                max_instances = 1
                replace_existing = True 
                
                # Import the function dynamically
                module_path, func_name = func_path.rsplit('.', 1)
                module = __import__(module_path, fromlist=[func_name])
                func = getattr(module, func_name)
                
                # Extract start_date / end_date from trigger_args if present.
                # The UI sends an On date end as a naive local time (YYYY-MM-DDT23:59:59).
                schedule_tz = trigger_args.get('timezone') or 'UTC'
                start_date = _parse_schedule_bound(trigger_args.pop('start_date', None), schedule_tz)
                end_date = _parse_schedule_bound(trigger_args.pop('end_date', None), schedule_tz)
                
                # Add job to scheduler
                add_job_kwargs = {
                    'id': job_id,
                    'args': job_args,
                    'kwargs': job_kwargs,
                    'replace_existing': replace_existing,
                    'max_instances': max_instances,
                    'coalesce': True,
                    'misfire_grace_time': 300
                }
                if start_date:
                    add_job_kwargs['start_date'] = start_date
                if end_date:
                    add_job_kwargs['end_date'] = end_date
                logger.warning(
                    "🧨 ADD_JOB | pid=%s | time=%s | job_id=%s | func=%s | trigger=%s | max_instances=%s | replace_existing=%s \n\n",
                    os.getpid(),
                    datetime.utcnow().isoformat(),
                    job_id,
                    func_path,
                    trigger_type,
                    max_instances,
                    replace_existing
                )

                scheduler.add_job(
                    func,
                    trigger_type,
                    **add_job_kwargs,
                    **trigger_args
                )
                
                logger.info(f"Job {job_id} added successfully.\n\n")
                response = {'status': 'success', 'job_id': job_id}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            except Exception as e:
                logger.error(f"Error adding job: {e} \n\n", exc_info=True)
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode())
        
        elif path.startswith('/jobs/reschedule/'):
            job_id = path.split('/jobs/reschedule/')[1]
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                trigger_type = data.get('trigger_type', 'interval')
                trigger_args = data.get('trigger_args', {})
                
                job = scheduler.get_job(job_id)
                if not job:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'not_found'}).encode())
                    return
                
                job.reschedule(trigger=trigger_type, **trigger_args)
                logger.info(f"Job {job_id} rescheduled successfully.")
                response = {'status': 'success', 'job_id': job_id}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            except Exception as e:
                logger.error(f"Error rescheduling job: {e}", exc_info=True)
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode())
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_DELETE(self):
        """Handle DELETE requests"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        if path.startswith('/jobs/remove/'):
            job_id = path.split('/jobs/remove/')[1]
            try:
                scheduler.remove_job(job_id)
                logger.info(f"Job {job_id} removed successfully.")
                response = {'status': 'success', 'job_id': job_id}
                self.send_response(200)
            except JobLookupError:
                # Save/pause calls remove before a job exists. That is already gone.
                logger.info("Job %s was not registered; remove is a no-op.", job_id)
                response = {'status': 'not_found', 'job_id': job_id}
                self.send_response(200)
            except Exception as e:
                logger.error(f"Error removing job: {e}", exc_info=True)
                self.send_response(500)
                response = {'status': 'error', 'message': str(e)}
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))

def run_server(port=5000):
    """Run the HTTP server"""
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, SchedulerRequestHandler)
    logger.info(f"Scheduler HTTP API server started on port {port} \n\n")
    httpd.serve_forever()


def _run_startup_pipeline_reconcile(port=5000):
    """After HTTP API is up, rebuild pipeline_* jobs from the pipeline table."""
    from apps.elttool.services.pipeline_scheduler_reconcile import (
        reconcile_all_pipeline_schedulers,
        reconcile_on_start_enabled,
    )

    if not reconcile_on_start_enabled():
        logger.info(
            "PIPELINE_SCHEDULER_RECONCILE_SKIPPED | "
            "PIPELINE_SCHEDULER_RECONCILE_ON_START is disabled"
        )
        return

    import time
    from django.conf import settings as django_settings

    deadline = time.monotonic() + 30
    local_url = f'http://127.0.0.1:{port}'
    os.environ['SCHEDULER_SERVICE_URL'] = local_url
    django_settings.SCHEDULER_SERVICE_URL = local_url

    while time.monotonic() < deadline:
        try:
            import urllib.request
            with urllib.request.urlopen(f'{local_url}/health', timeout=2) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.25)
    else:
        logger.error(
            "PIPELINE_SCHEDULER_RECONCILE_ABORTED | scheduler health check timed out"
        )
        return

    stats = reconcile_all_pipeline_schedulers()
    logger.info("PIPELINE_SCHEDULER_RECONCILE_STARTUP | stats=%s", stats)


if __name__ == '__main__':


    logger.warning(
        "🟢 SCHEDULER_PROCESS_START | pid=%s | time=%s | \n\n",
        os.getpid(),
        datetime.utcnow().isoformat()
    )

    # Start scheduler
    start_scheduler()
    
    # Start HTTP server in a separate thread
    server_thread = threading.Thread(target=run_server, args=(5000,), daemon=False)
    server_thread.start()

    reconcile_thread = threading.Thread(
        target=_run_startup_pipeline_reconcile,
        args=(5000,),
        daemon=True,
        name='pipeline-scheduler-reconcile',
    )
    reconcile_thread.start()

    # Keep main thread alive
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down scheduler service...")
        scheduler.shutdown()
        sys.exit(0)


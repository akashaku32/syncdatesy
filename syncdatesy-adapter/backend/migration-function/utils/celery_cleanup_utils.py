"""
Celery Job Cleanup Utilities

This module provides utilities to clean up completed celery jobs to prevent
stuck job scenarios where completed migrations still appear as active.

Usage:
    from utils.celery_cleanup_utils import handle_migration_completion
    
    # Call after successful migration completion
    cleanup_result = handle_migration_completion(
        connection_name="ATI_D365_PROD",
        job_name="ATI_D365_PROD_migration_job", 
        completion_message="Migration completed successfully in 231.04 seconds."
    )

Features:
- Automatic cleanup of stuck celery jobs after migration completion
- Redis-based cleanup tracking and monitoring
- Support for connection-specific job cleanup
- Comprehensive logging and error handling
"""

import time
import json
import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from celery import Celery, current_task
import redis

# Configure logger
logger = logging.getLogger(__name__)


def _current_celery_task_id() -> Optional[str]:
    """Task id of the running Celery task, if any."""
    try:
        req = getattr(current_task, "request", None)
        if req and getattr(req, "id", None):
            return str(req.id)
    except Exception:
        pass
    return None

# Celery app instance (configured to match project settings)
def get_celery_app():
    """Get configured Celery app instance"""
    celery_app = Celery('migration_tasks')
    
    # Get Redis connection from environment or use defaults
    redis_host = os.environ.get("REDIS_HOST", "redis")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    redis_db = int(os.environ.get("REDIS_DB", "0"))
    
    celery_app.conf.update(
        broker_url=f'redis://{redis_host}:{redis_port}/{redis_db}',
        result_backend=f'redis://{redis_host}:{redis_port}/{redis_db}',
        task_default_queue='default',
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=10,
        broker_connection_retry=True,
        broker_connection_max_retries=3,
        task_track_started=True,
        task_serializer='json',
        result_serializer='json',
        accept_content=['json'],
        result_expires=3600,
    )
    
    return celery_app

def cleanup_completed_migration_job(connection_name: str, job_name: str = None) -> Dict:
    """
    Clean up completed migration job from celery queue.
    
    This should be called when migration completes successfully to prevent
    stuck job scenarios where completed jobs still appear as active.
    
    Args:
        connection_name: Name of the connection that completed migration
        job_name: Specific job name to clean up (optional, will clean all if not provided)
    
    Returns:
        Dict with cleanup results
    """
    logger.info(f"🧹 CLEANUP: Starting job cleanup for {connection_name}")
    
    try:
        celery_app = get_celery_app()
        
        # Find active jobs for this connection
        active_jobs = _find_active_jobs_for_connection(celery_app, connection_name, job_name)
        
        if not active_jobs:
            logger.info(f"✅ CLEANUP: No active jobs found for {connection_name}")
            return {
                "status": "success",
                "message": "No cleanup needed - no active jobs found",
                "cleaned_jobs": []
            }
        
        # Print detailed information about found jobs
        print(f"\n{'='*80}")
        print(f"🔍 CLEANUP: Found {len(active_jobs)} active/stuck job(s) for {connection_name}:")
        print(f"{'='*80}")
        for idx, job in enumerate(active_jobs, 1):
            print(f"   Job {idx}:")
            print(f"      - Job Name: {job['job_name']}")
            print(f"      - Task ID: {job['task_id']}")
            print(f"      - Worker: {job['worker']}")
            print(f"      - Task Type: {job['task_name']}")
        print(f"{'='*80}\n")
        
        logger.info(f"🔍 CLEANUP: Found {len(active_jobs)} active/stuck job(s) for {connection_name}:")
        for idx, job in enumerate(active_jobs, 1):
            logger.info(f"   Job {idx}: {job['job_name']} (Task: {job['task_id']}, Worker: {job['worker']})")
        
        # Clean up the jobs — never revoke the task that is running this cleanup.
        current_task_id = _current_celery_task_id()
        cleaned_jobs = []
        for job in active_jobs:
            if current_task_id and job.get("task_id") == current_task_id:
                logger.info(
                    "SKIP_CLEANUP_CURRENT_TASK | task_id=%s | job=%s",
                    current_task_id,
                    job.get("job_name"),
                )
                continue
            success = _revoke_celery_task(celery_app, job['task_id'], job['job_name'])
            if success:
                cleaned_jobs.append(job)
                logger.info(f"🗑️  CLEANUP: Revoked task {job['task_id']} ({job['job_name']})")
        
        # Store cleanup record in Redis for monitoring
        _store_cleanup_record(connection_name, cleaned_jobs)
        
        # Print cleanup summary
        print(f"\n✅ CLEANUP COMPLETED: Successfully cleaned {len(cleaned_jobs)} job(s) for {connection_name}\n")
        logger.info(f"✅ CLEANUP: Successfully cleaned {len(cleaned_jobs)} jobs for {connection_name}")
        
        return {
            "status": "success",
            "message": f"Successfully cleaned up {len(cleaned_jobs)} jobs",
            "cleaned_jobs": cleaned_jobs
        }
        
    except Exception as e:
        logger.error(f"❌ CLEANUP: Failed to cleanup jobs for {connection_name}: {str(e)}")
        return {
            "status": "error",
            "message": f"Cleanup failed: {str(e)}",
            "cleaned_jobs": []
        }

def _find_active_jobs_for_connection(celery_app: Celery, connection_name: str, specific_job_name: str = None) -> List[Dict]:
    """Find active celery jobs for a connection"""
    try:
        inspect = celery_app.control.inspect()
        inspect.timeout = 5.0
        
        # Get active tasks
        active_tasks = inspect.active() or {}
        
        # Sanitize connection name to match job naming convention
        # Also keep raw connection name to match old job names that weren't sanitized
        safe_connection_name = re.sub(r'\W+', '_', connection_name.strip())
        raw_connection_name = connection_name  # Keep original with any leading/trailing spaces
        
        matching_jobs = []
        
        for worker, tasks in active_tasks.items():
            if not tasks:
                continue
                
            for task in tasks:
                # Check job_name in task arguments
                task_args = task.get('args', [])
                task_kwargs = task.get('kwargs', {})
                
                # Extract job_name (4th argument in call_function)
                job_name = None
                if len(task_args) >= 4:
                    job_name = task_args[3]  # job_name is 4th argument
                elif task_kwargs.get('job_name'):
                    job_name = task_kwargs['job_name']
                
                # Check if this job matches our connection (try both sanitized and raw names)
                if job_name and (job_name.startswith(safe_connection_name) or job_name.startswith(raw_connection_name)):
                    # When a job just completed, skip it and clean up other stuck jobs
                    # for the same connection (do not target the completing job itself).
                    if specific_job_name and job_name == specific_job_name:
                        continue

                    matching_jobs.append({
                        'job_name': job_name,
                        'task_id': task.get('id'),
                        'worker': worker,
                        'task_name': task.get('name')
                    })
        
        return matching_jobs
        
    except Exception as e:
        logger.error(f"Error finding active jobs: {e}")
        return []

def _revoke_celery_task(celery_app: Celery, task_id: str, job_name: str) -> bool:
    """Revoke a specific celery task"""
    try:
        # Revoke the task (terminate=True for immediate termination)
        celery_app.control.revoke(task_id, terminate=True)
        print(f"   🗑️  Revoked: {job_name} (Task ID: {task_id})")
        logger.info(f"🛑 REVOKED: Task {task_id} ({job_name})")
        return True
    except Exception as e:
        print(f"   ❌ Failed to revoke: {job_name} (Task ID: {task_id}): {e}")
        logger.error(f"Failed to revoke task {task_id}: {e}")
        return False

def _store_cleanup_record(connection_name: str, cleaned_jobs: List[Dict]):
    """Store cleanup record in Redis for monitoring purposes"""
    try:
        # Get Redis connection settings
        redis_host = os.environ.get("REDIS_HOST", "redis")
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        
        r = redis.Redis(host=redis_host, port=redis_port, db=2)  # Use separate DB for cleanup records
        
        cleanup_record = {
            'connection_name': connection_name,
            'timestamp': time.time(),
            'cleaned_jobs_count': len(cleaned_jobs),
            'cleaned_jobs': cleaned_jobs
        }
        
        # Store with 7-day expiration
        r.setex(f"cleanup:{connection_name}:{int(time.time())}", 
                604800,  # 7 days
                json.dumps(cleanup_record))
                
    except Exception as e:
        logger.warning(f"Failed to store cleanup record: {e}")

def check_recent_completions(connection_name: str, hours_back: int = 1) -> bool:
    """
    Check if there were recent migration completions that might indicate 
    stuck jobs should be cleaned
    """
    try:
        redis_host = os.environ.get("REDIS_HOST", "redis")
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        
        r = redis.Redis(host=redis_host, port=redis_port, db=2)
        
        # Look for cleanup records in the last N hours
        current_time = time.time()
        hours_back_timestamp = current_time - (hours_back * 3600)
        
        # Get all cleanup keys for this connection
        cleanup_keys = r.keys(f"cleanup:{connection_name}:*")
        
        recent_cleanups = 0
        for key in cleanup_keys:
            # Extract timestamp from key
            timestamp_str = key.decode().split(':')[-1]
            try:
                key_timestamp = float(timestamp_str)
                if key_timestamp >= hours_back_timestamp:
                    recent_cleanups += 1
            except ValueError:
                continue
        
        return recent_cleanups > 0
        
    except Exception as e:
        logger.error(f"Error checking recent completions: {e}")
        return False

def handle_migration_completion(connection_name: str, job_name: str = None, completion_message: str = None) -> Dict:
    """
    Main function to call when a migration completes.
    This handles both logging and cleanup.
    
    Args:
        connection_name: Name of the connection that completed
        job_name: Optional specific job name to clean up
        completion_message: Optional completion message for logging
    
    Returns:
        Dict with cleanup results
    """
    logger.info(f"🎉 MIGRATION COMPLETED: {connection_name}")
    print(f"⏱️ Inside handle_migration_completion at {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")
    if completion_message:
        logger.info(f"📊 COMPLETION MESSAGE: {completion_message}")
    
    # Clean up celery jobs
    print(f"⏱️ Starting celery cleanup at {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")
    cleanup_result = cleanup_completed_migration_job(connection_name, job_name)
    print(f"⏱️ Celery cleanup completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")
    
    if cleanup_result['status'] == 'success':
        logger.info(f"✅ COMPLETION HANDLER: Successfully handled completion for {connection_name}")
        if cleanup_result['cleaned_jobs']:
            logger.info(f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} celery jobs")
    else:
        logger.warning(f"⚠️ COMPLETION HANDLER: Cleanup had issues for {connection_name}: {cleanup_result['message']}")
    
    return cleanup_result

def get_active_migration_jobs(connection_name: str = None) -> Dict:
    """
    Get information about currently active migration jobs.
    Useful for debugging stuck job issues.
    
    Args:
        connection_name: Optional connection name to filter jobs
    
    Returns:
        Dict with active job information
    """
    try:
        celery_app = get_celery_app()
        inspect = celery_app.control.inspect()
        inspect.timeout = 5.0
        
        # Get active tasks
        active_tasks = inspect.active() or {}
        
        migration_jobs = []
        
        for worker, tasks in active_tasks.items():
            if not tasks:
                continue
                
            for task in tasks:
                task_args = task.get('args', [])
                task_kwargs = task.get('kwargs', {})
                
                # Extract job_name
                job_name = None
                if len(task_args) >= 4:
                    job_name = task_args[3]
                elif task_kwargs.get('job_name'):
                    job_name = task_kwargs['job_name']
                
                # Filter by connection name if provided
                if connection_name:
                    # Try both sanitized and raw connection names for matching
                    safe_connection_name = re.sub(r'\W+', '_', connection_name.strip())
                    raw_connection_name = connection_name
                    if not (job_name and (job_name.startswith(safe_connection_name) or job_name.startswith(raw_connection_name))):
                        continue
                
                migration_jobs.append({
                    'job_name': job_name,
                    'task_id': task.get('id'),
                    'worker': worker,
                    'task_name': task.get('name'),
                    'time_start': task.get('time_start')
                })
        
        # Print detailed information about active jobs
        if migration_jobs:
            filter_msg = f" for '{connection_name}'" if connection_name else ""
            print(f"\n{'='*80}")
            print(f"📊 ACTIVE JOBS: Found {len(migration_jobs)} active migration job(s){filter_msg}:")
            print(f"{'='*80}")
            for idx, job in enumerate(migration_jobs, 1):
                print(f"   Job {idx}:")
                print(f"      - Job Name: {job['job_name']}")
                print(f"      - Task ID: {job['task_id']}")
                print(f"      - Worker: {job['worker']}")
                print(f"      - Task Type: {job['task_name']}")
                if job.get('time_start'):
                    print(f"      - Started: {job['time_start']}")
            print(f"{'='*80}\n")
            
            logger.info(f"📊 ACTIVE JOBS: Found {len(migration_jobs)} active migration job(s){filter_msg}:")
            for idx, job in enumerate(migration_jobs, 1):
                logger.info(f"   Job {idx}: {job['job_name']} (Task: {job['task_id']}, Worker: {job['worker']})")
        else:
            filter_msg = f" for '{connection_name}'" if connection_name else ""
            print(f"✅ ACTIVE JOBS: No active migration jobs found{filter_msg}")
            logger.info(f"✅ ACTIVE JOBS: No active migration jobs found{filter_msg}")
        
        return {
            "status": "success",
            "active_jobs": migration_jobs,
            "total_count": len(migration_jobs)
        }
        
    except Exception as e:
        logger.error(f"Error getting active migration jobs: {e}")
        return {
            "status": "error",
            "message": str(e),
            "active_jobs": []
        }

def force_cleanup_stuck_jobs(connection_name: str, max_age_hours: int = 24) -> Dict:
    """
    Force cleanup of jobs that have been running for too long.
    Use this as a last resort for stuck jobs.
    
    Args:
        connection_name: Name of the connection to clean up
        max_age_hours: Maximum age in hours before considering a job stuck
    
    Returns:
        Dict with cleanup results
    """
    logger.warning(f"🚨 FORCE CLEANUP: Starting force cleanup for {connection_name}")
    
    try:
        celery_app = get_celery_app()
        inspect = celery_app.control.inspect()
        inspect.timeout = 5.0
        
        active_tasks = inspect.active() or {}
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        stuck_jobs = []
        # Try both sanitized and raw connection names for matching
        safe_connection_name = re.sub(r'\W+', '_', connection_name.strip())
        raw_connection_name = connection_name
        
        for worker, tasks in active_tasks.items():
            if not tasks:
                continue
                
            for task in tasks:
                task_args = task.get('args', [])
                task_kwargs = task.get('kwargs', {})
                
                job_name = None
                if len(task_args) >= 4:
                    job_name = task_args[3]
                elif task_kwargs.get('job_name'):
                    job_name = task_kwargs['job_name']
                
                # Match both sanitized and raw connection names (for backward compatibility)
                if job_name and (job_name.startswith(safe_connection_name) or job_name.startswith(raw_connection_name)):
                    # Check task age
                    time_start = task.get('time_start')
                    if time_start and (current_time - time_start) > max_age_seconds:
                        stuck_jobs.append({
                            'job_name': job_name,
                            'task_id': task.get('id'),
                            'worker': worker,
                            'age_hours': (current_time - time_start) / 3600
                        })
        
        # Print detailed information about stuck jobs found
        if stuck_jobs:
            print(f"\n{'='*80}")
            print(f"⚠️ FORCE CLEANUP: Found {len(stuck_jobs)} stuck job(s) older than {max_age_hours}h for {connection_name}:")
            print(f"{'='*80}")
            for idx, job in enumerate(stuck_jobs, 1):
                print(f"   Stuck Job {idx}:")
                print(f"      - Job Name: {job['job_name']}")
                print(f"      - Task ID: {job['task_id']}")
                print(f"      - Worker: {job['worker']}")
                print(f"      - Age: {job['age_hours']:.1f} hours")
            print(f"{'='*80}\n")
            
            logger.warning(f"⚠️ FORCE CLEANUP: Found {len(stuck_jobs)} stuck job(s) older than {max_age_hours}h for {connection_name}:")
            for idx, job in enumerate(stuck_jobs, 1):
                logger.warning(f"   Stuck Job {idx}: {job['job_name']} (Age: {job['age_hours']:.1f}h)")
        else:
            print(f"✅ FORCE CLEANUP: No stuck jobs found for {connection_name} (max age: {max_age_hours}h)")
            logger.info(f"✅ FORCE CLEANUP: No stuck jobs found for {connection_name} (max age: {max_age_hours}h)")
        
        # Force revoke stuck jobs
        cleaned_jobs = []
        for job in stuck_jobs:
            success = _revoke_celery_task(celery_app, job['task_id'], job['job_name'])
            if success:
                cleaned_jobs.append(job)
                logger.warning(f"🗑️  FORCE REVOKED: Task {job['task_id']} (Age: {job['age_hours']:.1f}h)")
        
        # Store cleanup record
        if cleaned_jobs:
            _store_cleanup_record(connection_name, cleaned_jobs)
        
        return {
            "status": "success",
            "message": f"Force cleaned {len(cleaned_jobs)} stuck jobs",
            "cleaned_jobs": cleaned_jobs
        }
        
    except Exception as e:
        logger.error(f"❌ FORCE CLEANUP: Failed for {connection_name}: {str(e)}")
        return {
            "status": "error",
            "message": f"Force cleanup failed: {str(e)}",
            "cleaned_jobs": []
        }

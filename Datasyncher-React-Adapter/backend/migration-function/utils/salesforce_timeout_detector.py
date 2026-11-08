"""
Enhanced Salesforce Timeout Detection using Bulk API Job Monitoring
Integrates with the migration process to detect truly stuck operations vs active Salesforce processing.
"""

import time
import threading
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from utils.salesforce_bulk_monitor import poll_batch_status

class SalesforceJobMonitor:
    """
    Monitor Salesforce bulk jobs during migration to detect stuck vs active operations.
    """
    
    def __init__(self, instance_url: str, access_token: str):
        self.instance_url = instance_url
        self.access_token = access_token
        self.active_jobs = {}  # Track job_id -> batch_id mappings
        self.monitoring_active = True
        
    def register_job(self, operation_type: str, job_id: str, batch_id: str, table_name: str):
        """Register a Salesforce bulk job for monitoring."""
        job_key = f"{operation_type}_{table_name}"
        self.active_jobs[job_key] = {
            "job_id": job_id,
            "batch_id": batch_id,
            "table_name": table_name,
            "operation_type": operation_type,
            "start_time": datetime.now(timezone.utc)
        }
        print(f"📝 Registered Salesforce job for monitoring: {job_key} (Job: {job_id}, Batch: {batch_id})")
    
    def check_job_activity(self, table_name: str, timeout_hours: int = 2) -> Tuple[bool, str]:
        """
        Check if Salesforce jobs for a table are actively working or stuck.
        
        Returns:
            Tuple of (is_active, status_message)
        """
        print(f"🔍 Checking Salesforce job activity for table '{table_name}'")
        
        # Find jobs for this table
        table_jobs = {k: v for k, v in self.active_jobs.items() if v["table_name"] == table_name}
        
        if not table_jobs:
            print(f"⚠️ No Salesforce jobs found for table '{table_name}' - cannot verify activity")
            return False, "No Salesforce jobs to monitor"
        
        all_jobs_status = []
        any_job_active = False
        
        for job_key, job_info in table_jobs.items():
            job_id = job_info["job_id"]
            batch_id = job_info["batch_id"]
            operation_type = job_info["operation_type"]
            
            print(f"🔍 Checking {operation_type} job: {job_id}/{batch_id}")
            
            try:
                # Check if this specific job is active
                is_active, status_info = check_salesforce_job_activity(
                    instance_url=self.instance_url,
                    access_token=self.access_token,
                    job_id=job_id,
                    batch_id=batch_id,
                    timeout_hours=timeout_hours
                )
                
                state = status_info.get("state", "Unknown")
                status = status_info.get("status", "Unknown")
                
                job_status = f"{operation_type}: {state}"
                all_jobs_status.append(job_status)
                
                if is_active or state == "InProgress":
                    any_job_active = True
                    print(f"🔄 ACTIVE: {operation_type} job is still processing")
                elif state in ["Completed", "Failed"]:
                    print(f"✅ FINISHED: {operation_type} job completed with state: {state}")
                else:
                    print(f"❓ UNKNOWN: {operation_type} job state: {state}")
                    
            except Exception as e:
                print(f"❌ Error checking {operation_type} job: {e}")
                all_jobs_status.append(f"{operation_type}: Error")
        
        status_summary = ", ".join(all_jobs_status)
        
        if any_job_active:
            return True, f"Salesforce actively processing: {status_summary}"
        else:
            return False, f"Salesforce jobs completed/stuck: {status_summary}"
    
    def cleanup_completed_jobs(self, table_name: str):
        """Remove completed jobs from monitoring."""
        jobs_to_remove = [k for k, v in self.active_jobs.items() if v["table_name"] == table_name]
        for job_key in jobs_to_remove:
            del self.active_jobs[job_key]
            print(f"🧹 Removed completed job from monitoring: {job_key}")


def enhanced_process_timeout_with_salesforce_monitoring(
    process, 
    table_name: str, 
    timeout_seconds: int,
    salesforce_monitor: Optional[SalesforceJobMonitor] = None
) -> Tuple[bool, str]:
    """
    Enhanced process timeout detection using Salesforce job monitoring.
    
    Args:
        process: The multiprocessing.Process to monitor
        table_name: Name of the table being processed
        timeout_seconds: Timeout in seconds
        salesforce_monitor: SalesforceJobMonitor instance
        
    Returns:
        Tuple of (should_kill_process, reason)
    """
    
    if not process.is_alive():
        return False, "Process already completed"
    
    # If no Salesforce monitoring available, use simple timeout
    if not salesforce_monitor:
        return True, f"Process timeout after {timeout_seconds}s (no Salesforce monitoring)"
    
    # Check if Salesforce is actively working
    try:
        is_active, status_message = salesforce_monitor.check_job_activity(
            table_name=table_name,
            timeout_hours=timeout_seconds / 3600
        )
        
        if is_active:
            print(f"🔄 SALESFORCE ACTIVE: Table '{table_name}' - {status_message}")
            print(f"⏳ Extending timeout - Salesforce is actively processing")
            return False, f"Salesforce actively working: {status_message}"
        else:
            print(f"🚨 SALESFORCE INACTIVE: Table '{table_name}' - {status_message}")
            print(f"🔪 Process should be killed - Salesforce not actively working")
            return True, f"Salesforce not active: {status_message}"
            
    except Exception as e:
        print(f"❌ Error checking Salesforce activity for table '{table_name}': {e}")
        return True, f"Salesforce monitoring error: {str(e)}"


# Integration example for the migration service
def integrate_salesforce_monitoring_example():
    """
    Example of how to integrate Salesforce monitoring into the migration process.
    """
    
    # In the migration process, create a monitor
    salesforce_monitor = SalesforceJobMonitor(
        instance_url="https://yourorg.salesforce.com",
        access_token="your_access_token"
    )
    
    # During bulk operations, register jobs
    # (This would be integrated into bulk_upsert_to_salesforce function)
    """
    if insert_response and hasattr(insert_response, 'job_id'):
        salesforce_monitor.register_job(
            operation_type="insert",
            job_id=insert_response.job_id,
            batch_id=insert_response.batch_id,
            table_name=table_name
        )
    """
    
    # During process monitoring, check Salesforce activity
    """
    # In the process monitoring loop:
    should_kill, reason = enhanced_process_timeout_with_salesforce_monitoring(
        process=process,
        table_name=table_name,
        timeout_seconds=7200,  # 2 hours
        salesforce_monitor=salesforce_monitor
    )
    
    if should_kill:
        print(f"🔪 Killing process: {reason}")
        process.terminate()
    else:
        print(f"⏳ Keeping process alive: {reason}")
    """
    
    print("📋 Integration example completed")


if __name__ == "__main__":
    print("🧪 Testing Salesforce timeout detection")
    integrate_salesforce_monitoring_example()

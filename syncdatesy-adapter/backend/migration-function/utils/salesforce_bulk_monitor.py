"""
Salesforce Bulk API 1.0 Job/Batch Status Monitoring
Uses requests library to poll Salesforce job status and detect stuck operations.
"""

import requests
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
import logging

# Set up logging
logger = logging.getLogger(__name__)

def poll_batch_status(
    instance_url: str,
    access_token: str,
    job_id: str,
    batch_id: str,
    api_version: str = "57.0",
    poll_interval: int = 30,
    max_poll_time: int = 7200  # 2 hours default
) -> Dict[str, str]:
    """
    Poll Salesforce Bulk API 1.0 batch status until completion or timeout.
    
    Args:
        instance_url: Salesforce instance URL (e.g., https://yourorg.salesforce.com)
        access_token: Valid Salesforce session ID/access token
        job_id: Salesforce bulk job ID
        batch_id: Salesforce batch ID within the job
        api_version: Salesforce API version (default: 57.0)
        poll_interval: Polling interval in seconds (default: 30)
        max_poll_time: Maximum polling time in seconds (default: 7200 = 2 hours)
    
    Returns:
        Dict containing final batch status details
    """
    
    # Construct the API endpoint
    endpoint = f"{instance_url.rstrip('/')}/services/async/{api_version}/job/{job_id}/batch/{batch_id}"
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/xml',
        'Accept': 'application/xml'
    }
    
    start_time = time.time()
    poll_count = 0
    
    print(f"🔍 SALESFORCE BULK API POLLING STARTED")
    print(f"   📡 Endpoint: {endpoint}")
    print(f"   ⏱️  Poll interval: {poll_interval}s")
    print(f"   ⏰ Max poll time: {max_poll_time}s ({max_poll_time/3600:.1f} hours)")
    print(f"   🆔 Job ID: {job_id}")
    print(f"   🆔 Batch ID: {batch_id}")
    
    while True:
        poll_count += 1
        elapsed_time = time.time() - start_time
        
        try:
            print(f"🔄 POLL #{poll_count}: Checking batch status (elapsed: {elapsed_time:.1f}s)")
            
            # Make GET request to Salesforce Bulk API
            response = requests.get(endpoint, headers=headers, timeout=60)
            
            if response.status_code != 200:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                print(f"❌ SALESFORCE API ERROR: {error_msg}")
                return {
                    "status": "ERROR",
                    "error": error_msg,
                    "elapsed_time": elapsed_time,
                    "poll_count": poll_count
                }
            
            # Parse XML response
            batch_info = parse_batch_xml_response(response.text)
            
            if not batch_info:
                print(f"❌ Failed to parse XML response")
                return {
                    "status": "ERROR", 
                    "error": "Failed to parse XML response",
                    "elapsed_time": elapsed_time,
                    "poll_count": poll_count
                }
            
            # Extract key information
            state = batch_info.get("state", "Unknown")
            records_processed = batch_info.get("numberRecordsProcessed", "0")
            records_failed = batch_info.get("numberRecordsFailed", "0")
            created_date = batch_info.get("createdDate", "Unknown")
            
            print(f"📊 BATCH STATUS:")
            print(f"   🔄 State: {state}")
            print(f"   ✅ Records Processed: {records_processed}")
            print(f"   ❌ Records Failed: {records_failed}")
            print(f"   📅 Created: {created_date}")
            
            # Check if batch is complete
            if state in ["Completed", "Failed", "NotProcessed"]:
                print(f"🎯 BATCH COMPLETED: Final state = {state}")
                batch_info["elapsed_time"] = elapsed_time
                batch_info["poll_count"] = poll_count
                batch_info["status"] = "SUCCESS"
                return batch_info
            
            elif state == "InProgress":
                print(f"🔄 BATCH IN PROGRESS: Salesforce is actively processing...")
                print(f"   ⏱️  Running for: {elapsed_time:.1f}s")
                print(f"   📈 Progress: {records_processed} processed, {records_failed} failed")
                
            elif state == "Queued":
                print(f"📋 BATCH QUEUED: Waiting for Salesforce to start processing...")
                
            else:
                print(f"❓ UNKNOWN STATE: {state}")
            
            # Check timeout
            if elapsed_time >= max_poll_time:
                print(f"⏰ POLLING TIMEOUT: Exceeded {max_poll_time}s ({max_poll_time/3600:.1f} hours)")
                print(f"🚨 SALESFORCE JOB APPEARS STUCK: State={state}, Processed={records_processed}")
                
                batch_info["status"] = "TIMEOUT"
                batch_info["elapsed_time"] = elapsed_time
                batch_info["poll_count"] = poll_count
                batch_info["timeout_reason"] = f"Polling exceeded {max_poll_time}s with state={state}"
                return batch_info
            
            # Wait before next poll
            print(f"⏳ Waiting {poll_interval}s before next poll...")
            time.sleep(poll_interval)
            
        except requests.exceptions.Timeout:
            print(f"⏰ HTTP TIMEOUT: Request to Salesforce timed out")
            return {
                "status": "ERROR",
                "error": "HTTP request timeout",
                "elapsed_time": elapsed_time,
                "poll_count": poll_count
            }
            
        except requests.exceptions.RequestException as e:
            print(f"🌐 NETWORK ERROR: {str(e)}")
            return {
                "status": "ERROR", 
                "error": f"Network error: {str(e)}",
                "elapsed_time": elapsed_time,
                "poll_count": poll_count
            }
            
        except Exception as e:
            print(f"❌ UNEXPECTED ERROR: {str(e)}")
            return {
                "status": "ERROR",
                "error": f"Unexpected error: {str(e)}",
                "elapsed_time": elapsed_time,
                "poll_count": poll_count
            }


def parse_batch_xml_response(xml_content: str) -> Optional[Dict[str, str]]:
    """
    Parse Salesforce Bulk API XML response and extract batch information.
    
    Args:
        xml_content: XML response from Salesforce Bulk API
        
    Returns:
        Dict containing batch information or None if parsing fails
    """
    try:
        # Parse XML
        root = ET.fromstring(xml_content)
        
        # Define namespace (Salesforce uses specific namespace)
        namespace = {'sf': 'http://www.force.com/2009/06/async/dataload'}
        
        # Extract batch information
        batch_info = {}
        
        # Find batchInfo element
        batch_element = root.find('.//sf:batchInfo', namespace)
        if batch_element is None:
            # Try without namespace
            batch_element = root.find('.//batchInfo')
        
        if batch_element is not None:
            # Extract all relevant fields
            fields = [
                'id', 'jobId', 'state', 'createdDate', 'systemModstamp',
                'numberBatchesQueued', 'numberBatchesInProgress', 'numberBatchesCompleted',
                'numberBatchesFailed', 'numberBatchesTotal', 'numberRecordsProcessed',
                'numberRecordsFailed', 'numberRetries', 'apiVersion'
            ]
            
            for field in fields:
                element = batch_element.find(f'.//{field}') or batch_element.find(f'.//sf:{field}', namespace)
                if element is not None and element.text:
                    batch_info[field] = element.text.strip()
            
            # Map common field names
            if 'id' in batch_info:
                batch_info['batchId'] = batch_info['id']
                
            print(f"📋 PARSED BATCH INFO: {len(batch_info)} fields extracted")
            return batch_info
        else:
            print(f"❌ No batchInfo element found in XML response")
            return None
            
    except ET.ParseError as e:
        print(f"❌ XML PARSE ERROR: {str(e)}")
        print(f"📄 Raw XML content: {xml_content[:500]}...")
        return None
    except Exception as e:
        print(f"❌ PARSE ERROR: {str(e)}")
        return None


def check_salesforce_job_activity(
    instance_url: str,
    access_token: str,
    job_id: str,
    batch_id: str,
    timeout_hours: int = 2
) -> Tuple[bool, Dict[str, str]]:
    """
    Check if a Salesforce job is actively working or stuck.
    
    Args:
        instance_url: Salesforce instance URL
        access_token: Valid Salesforce session ID/access token
        job_id: Salesforce bulk job ID
        batch_id: Salesforce batch ID
        timeout_hours: Hours to wait before considering job stuck
        
    Returns:
        Tuple of (is_active, status_info)
        - is_active: True if job is actively working, False if stuck/completed
        - status_info: Dict with detailed status information
    """
    
    print(f"🔍 CHECKING SALESFORCE JOB ACTIVITY:")
    print(f"   🆔 Job ID: {job_id}")
    print(f"   🆔 Batch ID: {batch_id}")
    print(f"   ⏰ Timeout: {timeout_hours} hours")
    
    # Poll for status with timeout
    result = poll_batch_status(
        instance_url=instance_url,
        access_token=access_token,
        job_id=job_id,
        batch_id=batch_id,
        max_poll_time=timeout_hours * 3600,  # Convert hours to seconds
        poll_interval=30
    )
    
    # Determine if job is actively working
    state = result.get("state", "Unknown")
    status = result.get("status", "Unknown")
    
    if status == "TIMEOUT":
        print(f"🚨 SALESFORCE JOB STUCK: No progress after {timeout_hours} hours")
        return False, result
    elif status == "ERROR":
        print(f"❌ SALESFORCE JOB ERROR: {result.get('error', 'Unknown error')}")
        return False, result
    elif state in ["Completed", "Failed", "NotProcessed"]:
        print(f"✅ SALESFORCE JOB FINISHED: State = {state}")
        return False, result
    elif state == "InProgress":
        print(f"🔄 SALESFORCE JOB ACTIVE: Still processing")
        return True, result
    else:
        print(f"❓ SALESFORCE JOB UNKNOWN STATE: {state}")
        return False, result


def monitor_salesforce_bulk_operation(
    instance_url: str,
    access_token: str,
    job_id: str,
    batch_id: str,
    table_name: str,
    connection_id: int,
    job_name: str,
    migration_session_id: int
) -> bool:
    """
    Monitor a Salesforce bulk operation and update database status accordingly.
    
    Args:
        instance_url: Salesforce instance URL
        access_token: Salesforce access token
        job_id: Salesforce job ID
        batch_id: Salesforce batch ID
        table_name: Table being processed
        connection_id: Database connection ID
        job_name: Migration job name
        migration_session_id: Migration session ID
        
    Returns:
        bool: True if operation completed successfully, False if failed/stuck
    """
    
    print(f"🔍 MONITORING SALESFORCE BULK OPERATION:")
    print(f"   📋 Table: {table_name}")
    print(f"   🆔 Job ID: {job_id}")
    print(f"   🆔 Batch ID: {batch_id}")
    
    try:
        # Check if job is actively working or stuck
        is_active, status_info = check_salesforce_job_activity(
            instance_url=instance_url,
            access_token=access_token,
            job_id=job_id,
            batch_id=batch_id,
            timeout_hours=2
        )
        
        final_state = status_info.get("state", "Unknown")
        
        if final_state == "Completed":
            print(f"✅ SALESFORCE OPERATION COMPLETED: Table '{table_name}'")
            
            # Update table status to completed
            from services.salesforce.client import update_table_status_sf_to_snowflake
            update_table_status_sf_to_snowflake(connection_id, table_name, "completed", job_name, migration_session_id)
            
            return True
            
        elif final_state == "Failed":
            print(f"❌ SALESFORCE OPERATION FAILED: Table '{table_name}'")
            
            # Update table status to failed
            from services.salesforce.client import update_table_status_sf_to_snowflake
            update_table_status_sf_to_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
            
            return False
            
        elif status_info.get("status") == "TIMEOUT":
            print(f"🚨 SALESFORCE OPERATION STUCK: Table '{table_name}' - No progress after 2 hours")
            
            # Update table status to failed due to timeout
            from services.salesforce.client import update_table_status_sf_to_snowflake
            update_table_status_sf_to_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
            
            return False
            
        else:
            print(f"❓ SALESFORCE OPERATION UNKNOWN STATE: Table '{table_name}' - State: {final_state}")
            return False
            
    except Exception as e:
        print(f"❌ ERROR MONITORING SALESFORCE OPERATION: {str(e)}")
        return False


# Example usage and testing
if __name__ == "__main__":
    # Test configuration
    INSTANCE_URL = "https://yourorg.salesforce.com"
    ACCESS_TOKEN = "your_access_token_here"
    JOB_ID = "7501234567890123"
    BATCH_ID = "7511234567890123"
    
    print("🧪 TESTING SALESFORCE BULK API POLLING")
    
    # Test 1: Basic polling
    print("\n1️⃣ Testing basic batch status polling:")
    result = poll_batch_status(
        instance_url=INSTANCE_URL,
        access_token=ACCESS_TOKEN,
        job_id=JOB_ID,
        batch_id=BATCH_ID,
        max_poll_time=300  # 5 minutes for testing
    )
    
    print(f"📊 POLLING RESULT:")
    for key, value in result.items():
        print(f"   {key}: {value}")
    
    # Test 2: Job activity check
    print("\n2️⃣ Testing job activity check:")
    is_active, status_info = check_salesforce_job_activity(
        instance_url=INSTANCE_URL,
        access_token=ACCESS_TOKEN,
        job_id=JOB_ID,
        batch_id=BATCH_ID,
        timeout_hours=1  # 1 hour for testing
    )
    
    print(f"📊 ACTIVITY CHECK RESULT:")
    print(f"   Is Active: {is_active}")
    print(f"   Status Info: {status_info}")
    
    # Test 3: Full monitoring
    print("\n3️⃣ Testing full operation monitoring:")
    success = monitor_salesforce_bulk_operation(
        instance_url=INSTANCE_URL,
        access_token=ACCESS_TOKEN,
        job_id=JOB_ID,
        batch_id=BATCH_ID,
        table_name="Account",
        connection_id=123,
        job_name="test_job",
        migration_session_id=456
    )
    
    print(f"📊 MONITORING RESULT: {'Success' if success else 'Failed'}")

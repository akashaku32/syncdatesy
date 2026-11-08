"""
Salesforce Progress-Based Operation Monitor

This module provides adaptive timeout and progress monitoring for Salesforce operations,
especially for Bulk API 1.0 fallback operations that don't have traditional job IDs.

Features:
- Progress-based stuck detection (not just time-based)
- Adaptive timeouts based on dataset size
- Support for operations with and without Salesforce job IDs
- Heartbeat logging for long-running operations
- Smart differentiation between stuck vs. legitimately slow operations

Usage:
    from utils.salesforce_progress_monitor import (
        create_progress_monitor, 
        get_progress_monitor,
        calculate_adaptive_timeouts
    )
    
    # Create monitor for 80 million records
    monitor = create_progress_monitor("operation_123", estimated_records=80_000_000)
    
    # Update progress regularly
    progress_info = monitor.update_progress(current_records=1_000_000)
    
    # Check if operation should continue
    should_continue, reason = monitor.should_continue()
"""

import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, Tuple
from dataclasses import dataclass

# Set up logging
logger = logging.getLogger(__name__)


class PartialMigrationTimeoutException(Exception):
    """
    Exception raised when operation times out but made significant progress.
    Contains metadata about what was successfully migrated.
    
    This is a "successful failure" - the operation didn't complete fully,
    but valuable data was migrated and should be tracked.
    """
    def __init__(self, message: str, records_migrated: int, total_expected: int, elapsed_seconds: int):
        super().__init__(message)
        self.records_migrated = records_migrated
        self.total_expected = total_expected
        self.elapsed_seconds = elapsed_seconds
        self.progress_percent = (records_migrated / total_expected * 100) if total_expected > 0 else 0
        self.is_near_complete = self.progress_percent >= 90.0
    
    def __str__(self):
        return (
            f"{super().__str__()} - "
            f"{self.records_migrated:,}/{self.total_expected:,} records "
            f"({self.progress_percent:.1f}%) in {self.elapsed_seconds}s"
        )


@dataclass
class ProgressMetrics:
    """Container for progress tracking metrics."""
    total_records: int = 0
    last_record_time: float = 0
    start_time: float = 0
    last_progress_log: float = 0
    estimated_total: int = 0


def calculate_adaptive_timeouts(estimated_records: int, base_rate_per_sec: float = 50.0) -> Dict[str, int]:
    """
    Calculate adaptive timeouts based on estimated dataset size.
    
    This function creates smart timeouts that scale with dataset size:
    - Small datasets (< 1M): Min 30 minutes, reasonable buffer
    - Large datasets (> 50M): Up to 12 hours, scaled appropriately
    - Progress timeout: 5% of expected time (5-30 minutes max)
    
    Args:
        estimated_records: Estimated number of records to process
        base_rate_per_sec: Conservative estimate of records per second (default: 50 for Bulk API 1.0)
    
    Returns:
        dict: Configuration dictionary with:
            - total_timeout: Maximum time for entire operation (seconds)
            - progress_timeout: Time without progress before considering stuck (seconds)
            - heartbeat_interval: How often to log progress (seconds)
            - expected_duration: Expected time to complete (seconds)
    
    Example:
        >>> timeouts = calculate_adaptive_timeouts(80_000_000)
        >>> print(f"Total: {timeouts['total_timeout']/3600:.1f}h")
        Total: 10.7h
    """
    # Calculate expected duration based on conservative rate
    expected_seconds = estimated_records / base_rate_per_sec
    
    # Total timeout: 4x expected time, min 30 min, max 12 hours
    # This allows for slowdowns while still preventing infinite hangs
    total_timeout = max(1800, min(43200, int(expected_seconds * 4)))
    
    # Progress timeout: Time without ANY progress before considering stuck
    # 5% of expected time, minimum 5 minutes, maximum 30 minutes
    # This allows for API throttling and temporary network issues
    progress_timeout = min(1800, max(300, int(expected_seconds * 0.05)))
    
    # Heartbeat interval: How often to log progress
    # 1% of expected time, minimum 30 seconds, maximum 2 minutes
    # Keeps logs manageable while providing visibility
    heartbeat_interval = min(120, max(30, int(expected_seconds * 0.01)))
    
    return {
        "total_timeout": total_timeout,
        "progress_timeout": progress_timeout,
        "heartbeat_interval": heartbeat_interval,
        "expected_duration": int(expected_seconds)
    }


class ProgressBasedMonitor:
    """
    Monitor operations based on actual progress, not just elapsed time.
    
    This monitor distinguishes between:
    1. Active operations (making progress, just large datasets) ✅
    2. Stuck operations (no progress, truly hung) ❌
    
    Key Features:
    - Adaptive timeouts scaled to dataset size
    - Progress-based stuck detection
    - Thread-safe for concurrent monitoring
    - Detailed progress metrics and reporting
    """
    
    def __init__(self, operation_id: str, estimated_records: int, table_name: str = "unknown"):
        """
        Initialize progress monitor.
        
        Args:
            operation_id: Unique identifier for this operation
            estimated_records: Estimated total records to process
            table_name: Name of table being processed (for logging)
        """
        self.operation_id = operation_id
        self.table_name = table_name
        self.estimated_records = max(1, estimated_records)  # Avoid division by zero
        
        # Initialize metrics
        self.metrics = ProgressMetrics(
            start_time=time.time(),
            last_record_time=time.time(),
            last_progress_log=time.time(),
            estimated_total=estimated_records
        )
        
        # Calculate adaptive timeouts
        self.timeouts = calculate_adaptive_timeouts(estimated_records)
        
        # Status tracking
        self.is_stuck = False
        self.stuck_reason = None
        self._lock = threading.Lock()
        
        logger.info(
            f"🚀 Progress monitor created for '{table_name}' (ID: {operation_id})\n"
            f"   📊 Estimated records: {estimated_records:,}\n"
            f"   ⏰ Total timeout: {self.timeouts['total_timeout']/3600:.1f}h\n"
            f"   🔍 Progress timeout: {self.timeouts['progress_timeout']/60:.1f}min\n"
            f"   💓 Heartbeat interval: {self.timeouts['heartbeat_interval']}s"
        )
    
    def update_progress(self, current_records: int) -> Dict[str, Any]:
        """
        Update progress and check for stuck operations.
        
        This method should be called regularly (e.g., every 1000 records or every minute)
        to update the progress state and check if the operation is stuck.
        
        Args:
            current_records: Current number of records processed
            
        Returns:
            dict: Progress information including:
                - status: "active" or "stuck"
                - progress_percent: Percentage complete
                - overall_rate: Records per second
                - estimated_remaining_seconds: Time left (if calculable)
                - time_since_last_progress: Seconds since last progress
        """
        with self._lock:
            current_time = time.time()
            
            # Calculate progress delta
            records_delta = current_records - self.metrics.total_records
            
            # Update metrics if we have progress
            if records_delta > 0:
                self.metrics.last_record_time = current_time
                self.metrics.total_records = current_records
                self.is_stuck = False  # Reset stuck flag when progress is made
            
            # Check if stuck (no progress for too long)
            time_since_progress = current_time - self.metrics.last_record_time
            if time_since_progress > self.timeouts["progress_timeout"]:
                self.is_stuck = True
                self.stuck_reason = (
                    f"No progress for {time_since_progress:.1f}s "
                    f"(limit: {self.timeouts['progress_timeout']}s = {self.timeouts['progress_timeout']/60:.1f}min)"
                )
            
            # Calculate progress metrics
            elapsed_total = current_time - self.metrics.start_time
            overall_rate = current_records / elapsed_total if elapsed_total > 0 else 0
            progress_percent = (current_records / self.estimated_records * 100) if self.estimated_records > 0 else 0
            
            # Estimate remaining time
            if overall_rate > 0:
                remaining_records = max(0, self.estimated_records - current_records)
                estimated_remaining_seconds = remaining_records / overall_rate
            else:
                estimated_remaining_seconds = float('inf')
            
            return {
                "operation_id": self.operation_id,
                "table_name": self.table_name,
                "status": "stuck" if self.is_stuck else "active",
                "stuck_reason": self.stuck_reason,
                "current_records": current_records,
                "estimated_records": self.estimated_records,
                "progress_percent": round(progress_percent, 1),
                "elapsed_seconds": int(elapsed_total),
                "overall_rate": round(overall_rate, 1),
                "estimated_remaining_seconds": int(estimated_remaining_seconds) if estimated_remaining_seconds != float('inf') else None,
                "time_since_last_progress": int(time_since_progress),
                "timeouts": self.timeouts,
                "is_within_total_timeout": elapsed_total < self.timeouts["total_timeout"]
            }
    
    def should_continue(self) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Check if operation should continue or be terminated.
        
        Enhanced with intelligent completion logic:
        - Stuck detection: 30 min no progress → Hard stop
        - ETA-based completion: Can finish within 1h → Continue
        - Grace period: ≥90% complete → Extended time
        - Total timeout: Too slow to complete → Graceful stop
        
        Returns:
            Tuple[bool, str, Dict]: (should_continue, reason, metadata)
                - should_continue: True if operation should keep running
                - reason: Human-readable explanation
                - metadata: Additional context for termination decisions
        """
        with self._lock:
            current_time = time.time()
            elapsed_total = current_time - self.metrics.start_time
            
            # ══════════════════════════════════════════════════════
            # TIER 1: Stuck Detection (Highest Priority)
            # ══════════════════════════════════════════════════════
            # If truly stuck (no progress), stop immediately
            if self.is_stuck:
                return False, f"STUCK: {self.stuck_reason}", {
                    "termination_type": "stuck",
                    "records_migrated": self.metrics.total_records,
                    "estimated_total": self.estimated_records,
                    "should_flush_buffer": False  # Hard stop - don't trust data
                }
            
            # ══════════════════════════════════════════════════════
            # TIER 2: Smart Completion Logic (When Timeout Reached)
            # ══════════════════════════════════════════════════════
            if elapsed_total > self.timeouts["total_timeout"]:
                hours = self.timeouts["total_timeout"] / 3600
                progress_percent = (self.metrics.total_records / self.estimated_records * 100) if self.estimated_records > 0 else 0
                overall_rate = self.metrics.total_records / elapsed_total if elapsed_total > 0 else 0
                
                # Calculate ETA to completion
                remaining_records = max(0, self.estimated_records - self.metrics.total_records)
                eta_seconds = remaining_records / overall_rate if overall_rate > 0 else float('inf')
                eta_hours = eta_seconds / 3600
                
                # ────────────────────────────────────────────────────
                # Decision 1: Can complete within 1 hour?
                # ────────────────────────────────────────────────────
                if eta_seconds <= 3600:  # 1 hour
                    eta_minutes = eta_seconds / 60
                    return True, f"COMPLETING: {progress_percent:.1f}% done, ETA {eta_minutes:.1f} min - continuing to finish", {
                        "extending_for_completion": True,
                        "estimated_minutes_remaining": eta_minutes,
                        "progress_percent": progress_percent
                    }
                
                # ────────────────────────────────────────────────────
                # Decision 2: Can complete within 2 hours AND ≥80% done?
                # ────────────────────────────────────────────────────
                elif eta_seconds <= 7200 and progress_percent >= 80.0:
                    eta_hours_rounded = eta_seconds / 3600
                    return True, f"NEAR_COMPLETE: {progress_percent:.1f}% done, ETA {eta_hours_rounded:.1f}h - worth completing", {
                        "extending_for_completion": True,
                        "estimated_hours_remaining": eta_hours_rounded,
                        "progress_percent": progress_percent
                    }
                
                # ────────────────────────────────────────────────────
                # Decision 3: ≥90% complete? (Grace period)
                # ────────────────────────────────────────────────────
                elif progress_percent >= 90.0:
                    grace_period = min(1800, self.timeouts["total_timeout"] * 0.1)
                    
                    if elapsed_total < (self.timeouts["total_timeout"] + grace_period):
                        grace_remaining = int((self.timeouts["total_timeout"] + grace_period) - elapsed_total)
                        return True, f"GRACE_PERIOD: {progress_percent:.1f}% complete, {grace_remaining}s grace remaining", {
                            "in_grace_period": True,
                            "progress_percent": progress_percent,
                            "estimated_hours_remaining": eta_hours
                        }
                
                # ────────────────────────────────────────────────────
                # Decision 4: Cannot complete in reasonable time
                # ────────────────────────────────────────────────────
                return False, f"TOTAL_TIMEOUT: Operation exceeded {hours:.1f}h, ETA {eta_hours:.1f}h more - stopping to save resources", {
                    "termination_type": "total_timeout",
                    "records_migrated": self.metrics.total_records,
                    "estimated_total": self.estimated_records,
                    "progress_percent": progress_percent,
                    "elapsed_seconds": int(elapsed_total),
                    "estimated_hours_to_complete": eta_hours,
                    "should_flush_buffer": True,  # Graceful stop - flush buffer
                    "is_near_complete": progress_percent >= 90.0
                }
            
            # ══════════════════════════════════════════════════════
            # Operation is healthy - continue normally
            # ══════════════════════════════════════════════════════
            time_since_progress = current_time - self.metrics.last_record_time
            return True, f"ACTIVE: Making progress (last progress {time_since_progress:.1f}s ago)", {}
    
    def should_log_heartbeat(self) -> bool:
        """Check if it's time to log a heartbeat."""
        with self._lock:
            current_time = time.time()
            time_since_log = current_time - self.metrics.last_progress_log
            
            if time_since_log >= self.timeouts["heartbeat_interval"]:
                self.metrics.last_progress_log = current_time
                return True
            return False
    
    def get_progress_summary(self) -> str:
        """Get a human-readable progress summary."""
        progress_info = self.update_progress(self.metrics.total_records)
        
        summary = (
            f"Progress for '{self.table_name}': "
            f"{progress_info['current_records']:,}/{progress_info['estimated_records']:,} records "
            f"({progress_info['progress_percent']:.1f}%) - "
            f"Rate: {progress_info['overall_rate']:.1f}/sec - "
            f"Elapsed: {progress_info['elapsed_seconds']}s"
        )
        
        if progress_info["estimated_remaining_seconds"]:
            remaining_hours = progress_info["estimated_remaining_seconds"] / 3600
            summary += f" - Est. remaining: {remaining_hours:.1f}h"
        
        return summary


# Global registry for active monitors
_active_monitors: Dict[str, ProgressBasedMonitor] = {}
_registry_lock = threading.Lock()


def create_progress_monitor(operation_id: str, estimated_records: int, table_name: str = "unknown") -> ProgressBasedMonitor:
    """
    Create and register a progress monitor.
    
    Args:
        operation_id: Unique identifier for this operation
        estimated_records: Estimated total records to process
        table_name: Name of table being processed
        
    Returns:
        ProgressBasedMonitor: Newly created monitor instance
    """
    with _registry_lock:
        monitor = ProgressBasedMonitor(operation_id, estimated_records, table_name)
        _active_monitors[operation_id] = monitor
        logger.info(f"📝 Registered progress monitor: {operation_id} for table '{table_name}'")
        return monitor


def get_progress_monitor(operation_id: str) -> Optional[ProgressBasedMonitor]:
    """
    Get existing progress monitor by operation ID.
    
    Args:
        operation_id: Operation identifier
        
    Returns:
        ProgressBasedMonitor or None if not found
    """
    with _registry_lock:
        return _active_monitors.get(operation_id)


def cleanup_progress_monitor(operation_id: str) -> bool:
    """
    Remove progress monitor from registry and cleanup.
    
    Args:
        operation_id: Operation identifier
        
    Returns:
        bool: True if monitor was found and removed
    """
    with _registry_lock:
        monitor = _active_monitors.pop(operation_id, None)
        if monitor:
            logger.info(f"🧹 Cleaned up progress monitor: {operation_id} for table '{monitor.table_name}'")
            return True
        return False


def get_all_active_monitors() -> Dict[str, ProgressBasedMonitor]:
    """Get dictionary of all active monitors."""
    with _registry_lock:
        return dict(_active_monitors)


def get_monitor_stats() -> Dict[str, Any]:
    """Get statistics about all active monitors."""
    with _registry_lock:
        stats = {
            "total_active_monitors": len(_active_monitors),
            "monitors": []
        }
        
        for operation_id, monitor in _active_monitors.items():
            progress_info = monitor.update_progress(monitor.metrics.total_records)
            stats["monitors"].append({
                "operation_id": operation_id,
                "table_name": monitor.table_name,
                "status": progress_info["status"],
                "progress_percent": progress_info["progress_percent"],
                "elapsed_seconds": progress_info["elapsed_seconds"],
                "current_records": progress_info["current_records"]
            })
        
        return stats


# Utility function for logging progress in a standardized way
def log_progress_heartbeat(monitor: ProgressBasedMonitor, logger_instance=None) -> None:
    """
    Log a standardized progress heartbeat if interval has passed.
    
    Args:
        monitor: ProgressBasedMonitor instance
        logger_instance: Optional logger (uses module logger if not provided)
    """
    if monitor.should_log_heartbeat():
        log = logger_instance or logger
        summary = monitor.get_progress_summary()
        log.info(f"💓 {summary}")


# Example usage and testing
if __name__ == "__main__":
    print("🧪 Testing Salesforce Progress Monitor")
    
    # Test 1: Small dataset (1M records)
    print("\n1️⃣ Testing small dataset (1M records):")
    timeouts_small = calculate_adaptive_timeouts(1_000_000)
    print(f"   Total timeout: {timeouts_small['total_timeout']/3600:.1f}h")
    print(f"   Progress timeout: {timeouts_small['progress_timeout']/60:.1f}min")
    print(f"   Expected duration: {timeouts_small['expected_duration']/60:.1f}min")
    
    # Test 2: Large dataset (80M records)
    print("\n2️⃣ Testing large dataset (80M records):")
    timeouts_large = calculate_adaptive_timeouts(80_000_000)
    print(f"   Total timeout: {timeouts_large['total_timeout']/3600:.1f}h")
    print(f"   Progress timeout: {timeouts_large['progress_timeout']/60:.1f}min")
    print(f"   Expected duration: {timeouts_large['expected_duration']/3600:.1f}h")
    
    # Test 3: Progress monitor
    print("\n3️⃣ Testing progress monitor:")
    monitor = create_progress_monitor("test_op_1", 10000, "TestTable")
    
    # Simulate progress
    for i in range(5):
        time.sleep(0.1)
        progress_info = monitor.update_progress(i * 2000)
        should_continue, reason = monitor.should_continue()
        print(f"   Iteration {i+1}: {progress_info['current_records']:,} records, Status: {reason}")
    
    # Cleanup
    cleanup_progress_monitor("test_op_1")
    
    print("\n✅ Tests completed")


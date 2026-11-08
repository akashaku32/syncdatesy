import functools
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

# Thread-local storage to collect timing records for the current thread/operation
_thread_local = threading.local()


def _get_records_list():
    if not hasattr(_thread_local, "records"):
        _thread_local.records = []
    return _thread_local.records


def increment_db_connection_count():
    """Increment the count of DB connections established in the current thread."""
    if not hasattr(_thread_local, "db_conn_count"):
        _thread_local.db_conn_count = 0
    _thread_local.db_conn_count += 1


def get_db_connection_count() -> int:
    """Return the total DB connections established in the current thread."""
    return getattr(_thread_local, "db_conn_count", 0)


def reset_timing_records():
    """Clear all collected timing records and counters for the current thread."""
    _thread_local.records = []
    _thread_local.db_conn_count = 0


def get_timing_records():
    """Return all collected timing records for the current thread."""
    return list(_get_records_list())


def log_execution_time(func_or_name=None):
    """
    Decorator to calculate and log start time, end time, and total execution time of a function.
    Also records the timing entry into thread-local storage for printing summary tables.
    """
    def decorator(func):
        func_name = func_or_name if isinstance(func_or_name, str) else func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_dt = datetime.now()
            start_time = time.perf_counter()
            start_str = start_dt.strftime("%H:%M:%S.%f")[:-3]
            logger.info(f"[PERF_TIMING] ENTERED '{func_name}' at {start_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            
            records = _get_records_list()
            try:
                result = func(*args, **kwargs)
                end_time = time.perf_counter()
                end_dt = datetime.now()
                duration = end_time - start_time
                end_str = end_dt.strftime("%H:%M:%S.%f")[:-3]
                
                records.append({
                    "function_name": func_name,
                    "start_time": start_str,
                    "end_time": end_str,
                    "duration_seconds": duration,
                    "status": "SUCCESS"
                })
                
                logger.info(
                    f"[PERF_TIMING] COMPLETED '{func_name}' in {duration:.4f}s "
                    f"(start: {start_str}, end: {end_str})"
                )
                return result
            except Exception as e:
                end_time = time.perf_counter()
                end_dt = datetime.now()
                duration = end_time - start_time
                end_str = end_dt.strftime("%H:%M:%S.%f")[:-3]
                
                records.append({
                    "function_name": func_name,
                    "start_time": start_str,
                    "end_time": end_str,
                    "duration_seconds": duration,
                    "status": f"FAILED ({type(e).__name__})"
                })
                
                logger.error(
                    f"[PERF_TIMING] FAILED '{func_name}' after {duration:.4f}s with error: {str(e)} "
                    f"(start: {start_str}, end: {end_str})"
                )
                raise

        return wrapper

    if callable(func_or_name):
        return decorator(func_or_name)
    return decorator


@contextmanager
def time_block(block_name: str):
    """Context manager to measure and log execution time of a specific code block."""
    start_dt = datetime.now()
    start_time = time.perf_counter()
    start_str = start_dt.strftime("%H:%M:%S.%f")[:-3]
    logger.info(f"[PERF_TIMING] ENTERED BLOCK '{block_name}' at {start_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    records = _get_records_list()
    try:
        yield
        end_time = time.perf_counter()
        end_dt = datetime.now()
        duration = end_time - start_time
        end_str = end_dt.strftime("%H:%M:%S.%f")[:-3]
        records.append({
            "function_name": f"BLOCK: {block_name}",
            "start_time": start_str,
            "end_time": end_str,
            "duration_seconds": duration,
            "status": "SUCCESS"
        })
        logger.info(
            f"[PERF_TIMING] COMPLETED BLOCK '{block_name}' in {duration:.4f}s "
            f"(start: {start_str}, end: {end_str})"
        )
    except Exception as e:
        end_time = time.perf_counter()
        end_dt = datetime.now()
        duration = end_time - start_time
        end_str = end_dt.strftime("%H:%M:%S.%f")[:-3]
        records.append({
            "function_name": f"BLOCK: {block_name}",
            "start_time": start_str,
            "end_time": end_str,
            "duration_seconds": duration,
            "status": f"FAILED ({type(e).__name__})"
        })
        logger.error(
            f"[PERF_TIMING] FAILED BLOCK '{block_name}' after {duration:.4f}s with error: {str(e)} "
            f"(start: {start_str}, end: {end_str})"
        )
        raise


def print_timing_summary_table(title: str = "PERFORMANCE TIMING SUMMARY") -> str:
    """
    Format all recorded function timing entries into an ASCII table and log it via logger.info.
    Returns the formatted string.
    """
    records = get_timing_records()
    db_conns = get_db_connection_count()
    if not records:
        msg = f"[{title}] No execution timing records captured. DB connections established: {db_conns}"
        logger.info(msg)
        return msg

    c_func = max(len("Function Name"), max(len(r["function_name"]) for r in records))
    c_start = 12
    c_end = 12
    c_dur = 14
    c_status = max(len("Status"), max(len(r["status"]) for r in records))

    table_width = c_func + c_start + c_dur + c_end + c_status + 16
    divider = "=" * table_width
    sub_divider = "-" * table_width

    lines = [
        "",
        divider,
        f" {title.center(table_width - 2)} ",
        divider,
        f"| {'Function Name':<{c_func}} | {'Start Time':<{c_start}} | {'End Time':<{c_end}} | {'Duration (s)':<{c_dur}} | {'Status':<{c_status}} |",
        sub_divider,
    ]

    total_duration = 0.0
    for r in records:
        dur_str = f"{r['duration_seconds']:.4f}s"
        total_duration += r["duration_seconds"]
        lines.append(
            f"| {r['function_name']:<{c_func}} | {r['start_time']:<{c_start}} | {r['end_time']:<{c_end}} | {dur_str:<{c_dur}} | {r['status']:<{c_status}} |"
        )

    lines.append(sub_divider)
    summary_line = f"TOTAL FUNCTIONS: {len(records)} | DB CONNS ESTABLISHED: {db_conns} | SUM PROFILED TIME: {total_duration:.4f}s"
    lines.append(f"| {summary_line:<{table_width - 4}} |")
    lines.append(divider)
    lines.append("")

    formatted_table = "\n".join(lines)
    logger.info(formatted_table)
    return formatted_table

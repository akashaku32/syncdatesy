"""
Shared retry / exponential backoff helpers.

Mirrors the Salesforce Bulk / GCS upload pattern (for attempt + 2**attempt sleep)
so Databricks volume uploads and similar I/O can reuse the same behavior.
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, Iterable, Optional, Type, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

_DEFAULT_RETRYABLE_EXC: tuple[Type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
)


class RetryableHTTPError(Exception):
    """Raised when an HTTP status should trigger retry_with_exponential_backoff."""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = int(status_code)
        super().__init__(message or f"HTTP {self.status_code}")


def retry_with_exponential_backoff(
    *,
    max_retries: int = 5,
    initial_wait: float = 1.0,
    max_wait: float = 30.0,
    retryable_exceptions: Optional[Iterable[Type[BaseException]]] = None,
    retryable_status_codes: Optional[Iterable[int]] = None,
    log_prefix: str = "retry",
):
    """
    Decorator: retry on transient exceptions with exponential backoff.

    Same shape as the Salesforce GCS ``upload_to_bucket`` loop:
    ``wait = min(max_wait, initial_wait * 2 ** attempt)``.
    """
    retry_exc = tuple(retryable_exceptions) if retryable_exceptions else _DEFAULT_RETRYABLE_EXC
    status_codes = set(retryable_status_codes or (408, 429, 500, 502, 503, 504))

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: Optional[BaseException] = None
            for attempt in range(max_retries):
                try:
                    return fn(*args, **kwargs)
                except RetryableHTTPError as exc:
                    if exc.status_code not in status_codes:
                        raise
                    last_exc = exc
                except retry_exc as exc:
                    last_exc = exc

                if attempt >= max_retries - 1:
                    break
                wait = min(max_wait, initial_wait * (2 ** attempt))
                logger.warning(
                    "%s | attempt=%s/%s sleep_s=%.1f err=%s",
                    log_prefix,
                    attempt + 1,
                    max_retries,
                    wait,
                    last_exc,
                )
                time.sleep(wait)
            assert last_exc is not None
            raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator

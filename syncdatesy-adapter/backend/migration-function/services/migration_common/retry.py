"""Retry helpers for migration I/O and API calls."""

from __future__ import annotations

import random
import time
from typing import Callable, Tuple, Type, TypeVar

T = TypeVar("T")


def retry_with_exponential_backoff(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
) -> T:
    """
    Call ``fn`` up to ``retries + 1`` times with exponential backoff between failures.

    Delay for attempt *i* (0-based after a failure) is
    ``min(max_delay, base_delay * (2 ** i))`` with a small jitter fraction.
    """
    if retries < 0:
        raise ValueError("retries must be >= 0")
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except exceptions as exc:
            last_exc = exc
            if attempt >= retries:
                break
            delay = min(max_delay, base_delay * (2**attempt))
            delay *= 0.5 + random.random() * 0.5
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc

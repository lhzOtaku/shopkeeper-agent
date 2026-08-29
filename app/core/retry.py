"""Small async retry helpers for external service calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.log import logger

T = TypeVar("T")


async def async_retry(
    operation_name: str,
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    timeout_seconds: float = 60,
    base_delay_seconds: float = 0.5,
) -> T:
    """Run one async operation with timeout and bounded retries."""

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(call(), timeout=timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.warning(
                f"{operation_name} failed on attempt {attempt}/{attempts}, "
                f"retrying in {delay:.1f}s: {exc}"
            )
            await asyncio.sleep(delay)

    assert last_error is not None
    logger.error(f"{operation_name} failed after {attempts} attempts: {last_error}")
    raise last_error

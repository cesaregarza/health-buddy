"""Global request pacing and bounded retry policy."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import TypeVar

from aiohttp import ClientError
from asyncsleepiq import (  # type: ignore[attr-defined]
    SleepIQLoginException,
    SleepIQTimeoutException,
)

T = TypeVar("T")


def status_code(exc: BaseException) -> int | None:
    for attribute in ("code", "status", "status_code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, SleepIQLoginException):
        return False
    code = status_code(exc)
    if code is not None:
        return code == 429 or 500 <= code <= 599
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            TimeoutError,
            SleepIQTimeoutException,
            ClientError,
        ),
    ):
        return True
    return False


class RequestExecutor:
    """Serialize high-level sleep-data requests and retry transient failures."""

    def __init__(
        self,
        *,
        delay_seconds: float,
        max_retries: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._delay_seconds = delay_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._random = random_value
        self._last_started_at: float | None = None
        self.attempts = 0

    async def _pace(self) -> None:
        if self._last_started_at is not None:
            remaining = self._delay_seconds - (self._clock() - self._last_started_at)
            if remaining > 0:
                await self._sleep(remaining)
        self._last_started_at = self._clock()

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        for retry_number in range(self._max_retries + 1):
            await self._pace()
            self.attempts += 1
            try:
                return await operation()
            except Exception as exc:
                if retry_number >= self._max_retries or not is_transient(exc):
                    raise
                base = min(30.0, max(1.0, self._delay_seconds) * (2**retry_number))
                jitter = base * 0.25 * self._random()
                await self._sleep(base + jitter)
        raise RuntimeError("retry loop exited unexpectedly")  # pragma: no cover

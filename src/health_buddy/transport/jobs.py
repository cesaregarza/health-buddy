"""Bounded service handoff whose jobs outlive a disconnected HTTP waiter."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TypeVar

import anyio

from health_buddy.transport.limits import EnvelopeError, Limits

T = TypeVar("T")


class Jobs:
    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.slots = asyncio.Semaphore(limits.service_jobs)
        self.threads = anyio.CapacityLimiter(limits.service_jobs)
        self.running: set[asyncio.Task[object]] = set()
        self.closed = False

    async def call(self, function: Callable[[], T], *, deadline: float) -> T:
        if self.closed:
            raise EnvelopeError(503, "service_unavailable")
        remaining = min(self.limits.admission_seconds, deadline - time.monotonic())
        if remaining <= 0:
            raise EnvelopeError(503, "admission_timeout")
        try:
            await asyncio.wait_for(self.slots.acquire(), remaining)
        except TimeoutError as exc:
            raise EnvelopeError(503, "service_busy") from exc
        if self.closed or time.monotonic() >= deadline:
            self.slots.release()
            raise EnvelopeError(503, "admission_timeout")

        async def invoke() -> T:
            return await anyio.to_thread.run_sync(
                function,
                abandon_on_cancel=False,
                limiter=self.threads,
            )

        task = asyncio.create_task(invoke())
        self.running.add(task)

        def finished(done: asyncio.Task[T]) -> None:
            self.running.discard(done)
            self.slots.release()
            # Retrieve a late failure even when its HTTP waiter has gone. Never
            # log arbitrary exception values, which may include private input.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                max(0.001, deadline - time.monotonic()),
            )
        except TimeoutError as exc:
            # The callable still owns its slot. Core's deadline decides whether
            # admission was possible; a durable decision can still finish.
            raise EnvelopeError(503, "outcome_unknown") from exc

    async def close(self) -> None:
        self.closed = True
        if self.running:
            # No new jobs after shutdown begins. The maintained supervisor has
            # an outer stop bound; journal recovery handles process termination.
            await asyncio.wait(self.running, timeout=self.limits.shutdown_seconds)

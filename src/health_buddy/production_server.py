"""Maintained HTTP/1 server with one serving worker and explicit resource bounds."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from granian import Granian
from granian.constants import HTTPModes, Interfaces, Loops, RuntimeModes, TaskImpl
from granian.http import HTTP1Settings
from starlette.types import ASGIApp

from .service_api import Operations
from .transport import create_app


def _load(operations_factory: Callable[[], Operations], development: bool) -> ASGIApp:
    # Granian calls this inside the serving child, before ASGI lifespan startup.
    return create_app(operations_factory(), development=development)


def serve(
    operations_factory: Callable[[], Operations],
    *,
    port: int = 8791,
    development: bool = False,
) -> None:
    """Serve loopback; real remote identity/proxy configuration belongs to 1067."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("HTTP port must be between 1 and 65535")
    runtime = Granian(
        target="health_buddy.transport",
        address="127.0.0.1",
        port=port,
        interface=Interfaces.ASGI,
        workers=1,
        blocking_threads=1,
        runtime_threads=1,
        runtime_blocking_threads=1,
        runtime_mode=RuntimeModes.st,
        loop=Loops.asyncio,
        task_impl=TaskImpl.asyncio,
        http=HTTPModes.http1,
        websockets=False,
        backlog=128,
        backpressure=16,
        http1_settings=HTTP1Settings(
            header_read_timeout=5000,
            keep_alive=False,
            max_buffer_size=16 * 1024,
            pipeline_flush=False,
        ),
        log_enabled=False,
        log_access=False,
        metrics_enabled=False,
        reload=False,
        respawn_failed_workers=False,
        workers_kill_timeout=30,
        env_files=(),
        static_path_mount=(),
        url_path_prefix=None,
    )
    runtime.serve(
        target_loader=partial(_load, operations_factory, development),
        wrap_loader=False,
    )

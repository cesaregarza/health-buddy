"""Maintained HTTP/1 server with one serving worker and explicit resource bounds."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from granian.constants import HTTPModes, Interfaces, Loops, RuntimeModes, TaskImpl
from granian.http import HTTP1Settings
from starlette.types import ASGIApp

from .runtime_listener import ListenerLease, listener_lease
from .security_api import IngressConfig, Runtime
from .service_api import Operations
from .transport import create_app
from .transport_ingress import VerifiedSocket, prepare_socket

if TYPE_CHECKING:
    # Concrete model for tested CPython3.12/GIL; no free-threaded qualification.
    from granian.server.mp import MPServer as Granian
else:
    # Preserve the pinned package's MPServer/MTServer runtime selection.
    from granian import Granian


class _OwnedSocketServer(Granian):
    """Pinned2.8.3 hook: upstream cleanup otherwise unlinks any current path."""

    owned_socket: VerifiedSocket | None = None
    listener_lease: ListenerLease | None = None

    def _unlink_pidfile(self) -> None:
        # This launcher never enables pid_file. Do not copy its subsystem or
        # invoke upstream's unconditional UDS unlink on a replaced pathname.
        if self.pid_file is not None:
            raise ValueError("unsupported_pid_file")
        owned = self.owned_socket
        if owned is None:
            return
        try:
            current = VerifiedSocket.capture(owned.path)
        except (OSError, ValueError):
            return
        if current == owned:
            if self.listener_lease is not None:
                self.listener_lease.cleanup(owned)
            else:
                owned.path.unlink()


def _load(
    factory: Callable[[], Operations | Runtime],
    development: bool,
    ingress: IngressConfig | None,
    socket_evidence: list[VerifiedSocket],
) -> ASGIApp:
    # Only values cross the child boundary. Stores and authority open here.
    value = factory()
    if isinstance(value, Runtime):
        if ingress is None or value.ingress != ingress:
            raise ValueError("ingress_configuration_changed")
        private = (
            socket_evidence[0]
            if ingress.mode == "tailscale-uds" and len(socket_evidence) == 1
            else None
        )
        if ingress.mode == "tailscale-uds" and (
            private is None
            or VerifiedSocket.capture(Path(ingress.socket_path)) != private
        ):
            raise ValueError("private_ingress_changed")
        return create_app(
            runtime=value, development=development, private_socket=private
        )
    if ingress is not None:
        raise ValueError("security_runtime_required")
    return create_app(value, development=development)


def _serve(
    operations_factory: Callable[[], Operations | Runtime],
    *,
    ingress: IngressConfig | None = None,
    port: int = 8791,
    development: bool = False,
    lease: ListenerLease | None = None,
) -> None:
    """Serve one loopback or private UDS listener; never configure a proxy."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("HTTP port must be between 1 and 65535")
    socket_path = (
        Path(ingress.socket_path)
        if ingress is not None and ingress.mode == "tailscale-uds"
        else None
    )
    if socket_path is not None:
        if development:
            raise ValueError("development_requires_loopback")
        prepare_socket(socket_path)
    runtime = _OwnedSocketServer(
        target="health_buddy.transport",
        address="127.0.0.1",
        port=port,
        uds=socket_path,
        uds_permissions=0o600 if socket_path is not None else None,
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
    runtime.listener_lease = lease
    socket_evidence: list[VerifiedSocket] = []
    if socket_path is not None:

        def capture_socket() -> None:
            runtime.owned_socket = VerifiedSocket.capture(socket_path)
            if lease is not None:
                lease.capture(runtime.owned_socket)
            socket_evidence.append(runtime.owned_socket)

        runtime.on_startup(capture_socket)
    # No permissive creation window: directory already0700 and umask applies
    # before Granian binds. Child checks0600/inode before ASGI accepts work.
    previous_umask = os.umask(0o077) if socket_path is not None else None
    try:
        runtime.serve(
            target_loader=partial(
                _load, operations_factory, development, ingress, socket_evidence
            ),
            wrap_loader=False,
        )
    finally:
        if previous_umask is not None:
            os.umask(previous_umask)


def serve(
    operations_factory: Callable[[], Operations | Runtime],
    *,
    ingress: IngressConfig | None = None,
    port: int = 8791,
    development: bool = False,
    _listener_fault: Callable[[str], None] | None = None,
) -> None:
    """Keep the shared managed-listener lock through supervisor shutdown."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("HTTP port must be between 1 and 65535")
    if development and ingress is not None and ingress.mode == "tailscale-uds":
        raise ValueError("development_requires_loopback")
    socket_path = (
        Path(ingress.socket_path)
        if ingress is not None and ingress.mode == "tailscale-uds"
        else None
    )
    with listener_lease(socket_path, _listener_fault) as lease:
        _serve(
            operations_factory,
            ingress=ingress,
            port=port,
            development=development,
            lease=lease,
        )

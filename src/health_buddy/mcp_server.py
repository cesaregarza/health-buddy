"""Dedicated stdio entrypoint; claim descriptors before importing the SDK."""

from __future__ import annotations

import sys

# Disable writes before importing any package from the verified source bundle.
sys.dont_write_bytecode = True

# ruff: noqa: E402
import fcntl
import logging
import os
from importlib import import_module
from pathlib import Path

DEPENDENCY_IMPORTS = (
    ("opentelemetry.trace", ("NoOpTracerProvider", "set_tracer_provider")),
    ("anyio", ("run",)),
    ("httpx2", ("AsyncClient",)),
    ("mcp_types.jsonrpc", ("JSONRPCError", "jsonrpc_message_adapter")),
    ("mcp.server.lowlevel", ("Server",)),
    ("mcp.server.context", ("ServerRequestContext",)),
    ("mcp.shared.message", ("ServerMessageMetadata", "SessionMessage")),
    ("health_buddy.mcp.runtime", ("run",)),
)


def _disable_tracing() -> None:
    # The pinned SDK obtains a tracer during import. Never load an inherited
    # provider/exporter entrypoint, including during the dependency probe.
    for key in tuple(os.environ):
        if key.startswith("OTEL_"):
            os.environ.pop(key)
    from opentelemetry.trace import (
        NoOpTracerProvider,
        get_tracer_provider,
        set_tracer_provider,
    )

    provider = NoOpTracerProvider()
    set_tracer_provider(provider)
    if get_tracer_provider() is not provider:
        raise RuntimeError("isolated_tracer_required")


def dependency_status() -> int:
    """Fixed exit codes only: no settings, credential, API or client activity."""
    for index, (name, attributes) in enumerate(DEPENDENCY_IMPORTS):
        try:
            module = import_module(name)
            for attribute in attributes:
                getattr(module, attribute)
            if index == 0:
                _disable_tracing()
        except Exception:
            # Never return exception text or a dependency-controlled module name.
            return 10 + index
    return 0


def main() -> int:
    read_fd = write_fd = -1
    try:
        read_fd = fcntl.fcntl(0, fcntl.F_DUPFD_CLOEXEC, 3)
        write_fd = fcntl.fcntl(1, fcntl.F_DUPFD_CLOEXEC, 3)
        with open(os.devnull, "r+b", buffering=0) as sink:
            for descriptor in (0, 1, 2):
                os.dup2(sink.fileno(), descriptor)
        logging.disable(sys.maxsize)
        if sys.argv[1:] == ["--check-dependencies"]:
            return dependency_status()
        # Deliberate fixed-argument CLI: no secret values in argv or diagnostics.
        if len(sys.argv) != 3 or sys.argv[1] != "--settings":
            return 2
        _disable_tracing()
        import anyio

        from health_buddy.mcp.runtime import run
        from health_buddy.mcp.settings import Settings
        from health_buddy.mcp.tools import ToolService

        settings = Settings.read(Path(sys.argv[2]))
        anyio.run(run, ToolService(settings), read_fd, write_fd)
        return 0
    except (Exception, KeyboardInterrupt):
        # Neither SDK diagnostics nor private settings/credentials reach stderr.
        return 1
    finally:
        for descriptor in (read_fd, write_fd):
            if descriptor >= 0:
                os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())

"""Dedicated stdio entrypoint; claim descriptors before importing the SDK."""

from __future__ import annotations

import fcntl
import logging
import os
import sys
from pathlib import Path


def main() -> int:
    read_fd = write_fd = -1
    try:
        read_fd = fcntl.fcntl(0, fcntl.F_DUPFD_CLOEXEC, 3)
        write_fd = fcntl.fcntl(1, fcntl.F_DUPFD_CLOEXEC, 3)
        with open(os.devnull, "r+b", buffering=0) as sink:
            for descriptor in (0, 1, 2):
                os.dup2(sink.fileno(), descriptor)
        # Deliberate fixed-argument CLI: no secret values in argv or diagnostics.
        if len(sys.argv) != 3 or sys.argv[1] != "--settings":
            return 2
        logging.disable(sys.maxsize)
        # The pinned SDK obtains a tracer during import, before middleware can
        # be cleared. Never load an inherited provider/exporter entrypoint.
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
            # A process preloaded with another provider is not this isolated
            # adapter environment; do not run and hope that it stays dormant.
            return 1
        import anyio

        from health_buddy.mcp_runtime import run
        from health_buddy.mcp_settings import Settings
        from health_buddy.mcp_tools import ToolService

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

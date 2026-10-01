"""Instrumented real entrypoint: canaries prove stdout and telemetry isolation."""

import builtins
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from opentelemetry import trace

settings = Path(sys.argv[2])
origin = urlsplit(json.loads(settings.read_bytes())["origin"])
marker = settings.parent / "unexpected-activity"
observed = settings.parent / "mcp-runtime-import-observed"


def forbidden(*args, **kwargs):
    marker.write_text("unexpected telemetry activity")
    raise RuntimeError("synthetic-private-telemetry-canary")


trace._load_provider = forbidden
trace.NoOpTracer.start_as_current_span = forbidden


def audit(event, arguments):
    if event == "socket.connect":
        address = arguments[1]
        if address != ("127.0.0.1", origin.port):
            marker.write_text("unexpected network activity")
            raise RuntimeError("synthetic-private-network-canary")


sys.addaudithook(audit)
original = builtins.__import__


def importing(name, *args, **kwargs):
    module = original(name, *args, **kwargs)
    if name == "health_buddy.mcp_runtime":
        observed.write_text("observed")
        # This executes after the product claims descriptors, during real import.
        os.write(1, b"synthetic-private-stdout-canary\n")
        print("synthetic-private-print-canary", flush=True)
        os.write(2, b"synthetic-private-stderr-canary\n")
    return module


builtins.__import__ = importing
# Instrumentation must precede importing the actual entrypoint.
from health_buddy.mcp_server import main  # noqa: E402

raise SystemExit(main())

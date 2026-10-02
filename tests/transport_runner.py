"""Queue-only real-server launcher using fabricated probes or a temp workspace."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from functools import partial
from pathlib import Path

from health_buddy.core.service_api import Principal, Response
from health_buddy.transport.server import serve


class ProbeOperations:
    """No health storage. Exercises transport/process behavior, not durability."""

    def __init__(self, *, fail_startup=False):
        self.writes = 0
        if fail_startup:
            raise RuntimeError("Synthetic factory startup failure")

    def preflight(self, principal, operation):
        if principal != Principal("local-development-owner"):
            return Response(403, b'{"error":{"code":"forbidden"},"meta":{}}')
        return None

    def execute(self, principal, request):
        if request.operation in ("workouts.write", "healthkit.ingest"):
            if isinstance(request.payload, dict) and request.payload.get("probeDelay"):
                time.sleep(0.2)
            self.writes += 1
        body = json.dumps(
            {
                "data": {
                    "pid": os.getpid(),
                    "writes": self.writes,
                    "operation": request.operation,
                    "payload": request.payload,
                },
                "meta": {},
            },
            separators=(",", ":"),
        ).encode()
        headers = ()
        if request.operation == "healthkit.ingest" and request.identity is not None:
            headers = (
                ("X-Installation-ID", request.identity.installation_id),
                ("X-Dataset-ID", request.identity.dataset_id),
                ("X-Restore-Epoch", request.identity.restore_epoch),
            )
        return Response(200, body, headers)


def publish_then_build(evidence, port, operations):
    """Runs in Granian's serving child, before its listener binds the port."""
    pending = evidence.with_name(evidence.name + ".pending")
    pending.write_text(json.dumps({"factoryPid": os.getpid(), "port": port}))
    pending.replace(evidence)
    return operations()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--fail-startup", action="store_true")
    args = parser.parse_args()
    if args.workspace is not None:
        from health_buddy.core.operations import open_service

        operations = partial(open_service, args.workspace, development=args.development)
    else:
        operations = partial(ProbeOperations, fail_startup=args.fail_startup)
    # Held until the server exits, so no other bind or connect is handed this
    # port; Granian's listener sets SO_REUSEADDR and still binds it.
    with socket.socket() as reservation:
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        factory = partial(publish_then_build, args.evidence, port, operations)
        serve(factory, port=port, development=args.development)


if __name__ == "__main__":
    main()

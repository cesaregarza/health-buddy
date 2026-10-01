"""Queue-only real-server launcher using fabricated probes or a temp workspace."""

from __future__ import annotations

import argparse
import json
import os
import time
from functools import partial
from pathlib import Path

from health_buddy.production_server import serve
from health_buddy.core.service_api import Principal, Response


class ProbeOperations:
    """No health storage. Exercises transport/process behavior, not durability."""

    def __init__(self, evidence, *, fail_startup=False):
        self.writes = 0
        evidence.write_text(json.dumps({"factoryPid": os.getpid()}))
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--fail-startup", action="store_true")
    args = parser.parse_args()
    if args.workspace is not None:
        from health_buddy.core.operations import open_service

        factory = partial(open_service, args.workspace, development=args.development)
    elif args.evidence is not None:
        factory = partial(
            ProbeOperations, args.evidence, fail_startup=args.fail_startup
        )
    else:
        parser.error("Select one synthetic workspace or probe evidence file")
    serve(factory, port=args.port, development=args.development)


if __name__ == "__main__":
    main()

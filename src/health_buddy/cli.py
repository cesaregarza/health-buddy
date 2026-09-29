"""Canonical local CLI; development authority requires an explicit selection."""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from pathlib import Path

from .app import App
from .config import ConfigError
from .domain import decode
from .legacy_store import StoreError
from .loggers import FIELDS
from .operations import open_service
from .service_api import ServiceError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--development",
        action="store_true",
        help="Explicit local owner authority; never remote authentication",
    )
    parser.add_argument(
        "--new-write",
        action="store_true",
        help="Start a new intentionally repeated action; pending actions must be resolved first",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("render")
    commands.add_parser("status")
    plan = commands.add_parser("plan")
    plan.add_argument("--file", type=Path, required=True)
    context = commands.add_parser("context")
    context.add_argument("--scopes", default="all")
    context.add_argument("--days", default=30, type=int)
    context.add_argument("--ask", default="")
    server = commands.add_parser("serve")
    server.add_argument("--port", default=8791, type=int)
    log = commands.add_parser("log")
    log.add_argument("kind", choices=(*FIELDS, "workout"))
    log.add_argument("arguments", nargs=argparse.REMAINDER)
    pending = commands.add_parser("pending")
    pending.add_argument(
        "action", choices=("show", "retry", "discard"), nargs="?", default="show"
    )
    pending.add_argument(
        "--acknowledge-possible-save",
        action="store_true",
        help="Discard retry state knowing the original write may already have saved; this does not undo it",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .production_server import serve

            # Construct the service inside Granian's child, never in this
            # supervisor before the factory crosses its process boundary.
            serve(
                partial(open_service, args.workspace, development=args.development),
                port=args.port,
                development=args.development,
            )
            return 0
        if args.command == "init":
            open_service(args.workspace)
            print(
                "Private canonical workspace ready. Optional sources stay explicitly configured."
            )
            return 0
        app = (
            App.development(args.workspace) if args.development else App(args.workspace)
        )
        if args.command == "render":
            output = app.config.storage("cache") / "index.html"
            app.write_html(output)
            print(output)
        elif args.command == "status":
            print(json.dumps(app.snapshot()["sources"], indent=2))
        elif args.command == "context":
            print(app.context(args.scopes, args.days, args.ask), end="")
        elif args.command == "plan":
            print(json.dumps(app.set_plan(args.file, new_write=args.new_write)))
        elif args.command == "log":
            if args.kind == "workout":
                if args.arguments:
                    raise ServiceError(422, "workout_requires_json_stdin")
                raw = sys.stdin.read(65537)
                if len(raw.encode("utf-8")) > 65536:
                    raise ServiceError(413, "request_too_large")
                payload = decode(raw, limit=65536)
                if not isinstance(payload, dict):
                    raise ServiceError(422, "invalid_request")
                result = app.workout(payload, new_write=args.new_write)
            else:
                result = app.log_record(
                    args.kind, args.arguments, new_write=args.new_write
                )
            print(json.dumps(result))
        elif args.command == "pending":
            if args.action == "retry":
                result = app.workflow.retry()
            elif args.action == "discard":
                result = app.workflow.discard(
                    acknowledge_possible_save=args.acknowledge_possible_save
                )
            else:
                result = app.workflow.inspect()
            print(json.dumps(result))
    except ServiceError as exc:
        # Safe protocol code only. Pending inspection never dumps payloads;
        # retry success prints the ordinary verified canonical receipt.
        print(f"Health Buddy: {exc.code} (HTTP {exc.status}).", file=sys.stderr)
        return 2
    except (ConfigError, StoreError, OSError, ValueError, RuntimeError):
        print(
            "Health Buddy could not open or update this workspace. Existing records and retry state were preserved.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

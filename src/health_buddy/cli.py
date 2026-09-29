"""Portable source-bundle entrypoint for a private local development workspace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app import App
from .config import ConfigError
from .legacy_store import StoreError
from .server import server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("render")
    commands.add_parser("status")
    plan = commands.add_parser("plan")
    plan.add_argument("--file", type=Path, required=True)
    context = commands.add_parser("context")
    context.add_argument("--scopes", default="all")
    context.add_argument("--days", default=30, type=int)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", default=8791, type=int)
    log = commands.add_parser("log")
    log.add_argument("kind", choices=("measurement", "intake", "workout"))
    log.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        app = App(args.workspace)
        if args.command == "init":
            print(
                "Private workspace ready. Optional integrations are controlled by config.json."
            )
        elif args.command == "render":
            output = app.config.storage("cache") / "index.html"
            app.write_html(output)
            print(output)
        elif args.command == "status":
            print(json.dumps(app.snapshot()["sources"], indent=2))
        elif args.command == "context":
            print(app.context(args.scopes, args.days), end="")
        elif args.command == "plan":
            print(json.dumps(app.set_plan(args.file)))
        elif args.command == "serve":
            with server(app, args.port) as http:
                print(
                    f"Local development only: http://127.0.0.1:{http.server_port}",
                    flush=True,
                )
                http.serve_forever()
        elif args.command == "log":
            if args.kind == "workout":
                if args.arguments:
                    raise ValueError("Workout input must be one JSON object on stdin")
                raw = sys.stdin.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Workout input is too large")
                result = app.workout(json.loads(raw))
            else:
                result = app.log_record(args.kind, args.arguments)
            print(json.dumps(result))
    except (ConfigError, StoreError) as exc:
        print(f"Health Buddy: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError):
        print(
            "Health Buddy could not open or update this workspace. Review configuration and source status; existing records were preserved.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line administration for the private HealthKit receiver."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from health_ingest.server import create_server
from health_ingest.storage import HealthRepository

DEFAULT_DATABASE = Path(".local/data/healthkit.db")


def _database_path(value: str | None) -> Path:
    configured = value or os.environ.get("HEALTH_INGEST_SQLITE_PATH")
    return Path(configured) if configured else DEFAULT_DATABASE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        help=(
            "SQLite path (default: HEALTH_INGEST_SQLITE_PATH or "
            ".local/data/healthkit.db)"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="create or validate the database schema")

    issue = subparsers.add_parser("issue-device", help="issue one app installation")
    issue.add_argument("--device-id", required=True, help="installation UUID")
    issue.add_argument("--label", required=True, help="human-readable device label")

    revoke = subparsers.add_parser("revoke-device", help="revoke one installation")
    revoke.add_argument("--device-id", required=True, help="installation UUID")

    serve = subparsers.add_parser("serve", help="start the HTTP receiver")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8787, type=int)

    export = subparsers.add_parser(
        "export-daily", help="regenerate the compact daily aggregate CSV"
    )
    export.add_argument("--output", default="data/apple_health_daily.csv", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    repository = HealthRepository(_database_path(namespace.database))
    repository.migrate()

    if namespace.command == "migrate":
        print(json.dumps({"database": str(repository.database_path), "status": "ok"}))
        return 0
    if namespace.command == "issue-device":
        token = repository.issue_device(namespace.device_id, namespace.label)
        print(
            json.dumps(
                {
                    "deviceId": namespace.device_id,
                    "token": token,
                    "warning": "Store this token in iOS Keychain; it is shown once.",
                }
            )
        )
        return 0
    if namespace.command == "revoke-device":
        revoked = repository.revoke_device(namespace.device_id)
        print(json.dumps({"deviceId": namespace.device_id, "revoked": revoked}))
        return 0 if revoked else 1
    if namespace.command == "export-daily":
        count = repository.export_daily(namespace.output)
        print(json.dumps({"output": str(namespace.output), "records": count}))
        return 0
    if namespace.command == "serve":
        logging.basicConfig(
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            level=logging.INFO,
        )
        server = create_server(namespace.host, namespace.port, repository)
        logging.getLogger("health_ingest").info(
            "listening host=%s port=%d database=%s",
            namespace.host,
            namespace.port,
            repository.database_path,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    raise AssertionError(f"unsupported command: {namespace.command}")


if __name__ == "__main__":
    raise SystemExit(main())

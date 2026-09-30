"""Explicit native data-only import maintenance, never an agent health tool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .legacy_import import export_measurements, import_measurements
from .service_api import ServiceError


def add_commands(commands: Any) -> None:
    command = commands.add_parser("legacy-import")
    sub = command.add_subparsers(dest="import_action", required=True)
    export = sub.add_parser("export-measurements")
    export.add_argument("--source-csv", type=Path, required=True)
    export.add_argument("--source-id", required=True)
    export.add_argument("--expected-source-sha256", required=True)
    export.add_argument("--snapshot", type=Path, required=True)
    adopt = sub.add_parser("adopt-measurements")
    adopt.add_argument("--snapshot", type=Path, required=True)
    adopt.add_argument("--expected-snapshot-sha256", required=True)


def handle(args: argparse.Namespace) -> int:
    if args.development or args.credential_file is not None:
        raise ServiceError(422, "import_requires_native_owner_maintenance")
    if args.import_action == "export-measurements":
        result = export_measurements(
            args.source_csv,
            args.snapshot,
            source_id=args.source_id,
            expected_source_sha256=args.expected_source_sha256,
        )
    else:
        result = import_measurements(
            args.workspace,
            args.snapshot,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
        )
    print(json.dumps(result, indent=2))
    return 0

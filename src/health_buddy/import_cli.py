"""Explicit native data-only import maintenance, never an agent health tool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .legacy_import import export_measurements, import_measurements
from .legacy_manual_canary import INPUTS, export_manual_canary, import_manual_canary
from .legacy_receiver_import import export_receiver, import_receiver
from .legacy_workout_import import export_workouts, import_workouts
from .runtime_manifest import native_directory
from .security import SecurityAuthority
from .security_api import BearerProof
from .security_runtime import open_runtime, read_credential
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

    export_workout = sub.add_parser("export-workouts")
    export_workout.add_argument("--sessions-csv", type=Path, required=True)
    export_workout.add_argument("--sets-csv", type=Path, required=True)
    export_workout.add_argument("--source-id", required=True)
    export_workout.add_argument("--expected-sessions-sha256", required=True)
    export_workout.add_argument("--expected-sets-sha256", required=True)
    export_workout.add_argument("--snapshot", type=Path, required=True)
    adopt_workout = sub.add_parser("adopt-workouts")
    adopt_workout.add_argument("--snapshot", type=Path, required=True)
    adopt_workout.add_argument("--expected-snapshot-sha256", required=True)

    combined = sub.add_parser("export-manual-canary")
    for name, suffix in (
        ("measurements", "snapshot"),
        ("workouts", "snapshot"),
        ("intake", "csv"),
        ("plan", "json"),
        ("preferences", "json"),
    ):
        combined.add_argument("--" + name + "-" + suffix, type=Path, required=True)
        combined.add_argument("--expected-" + name + "-sha256", required=True)
    combined.add_argument("--source-revision", required=True)
    combined.add_argument("--snapshot", type=Path, required=True)
    adopt_combined = sub.add_parser("adopt-manual-canary")
    adopt_combined.add_argument("--snapshot", type=Path, required=True)
    adopt_combined.add_argument("--expected-snapshot-sha256", required=True)

    receiver = sub.add_parser("export-receiver")
    receiver.add_argument("--source-db", type=Path, required=True)
    receiver.add_argument("--expected-source-sha256", required=True)
    receiver.add_argument("--mapping-file", type=Path, required=True)
    receiver.add_argument("--expected-mapping-sha256", required=True)
    receiver.add_argument("--confirm-quiesced", action="store_true")
    receiver.add_argument("--snapshot", type=Path, required=True)
    adopt_receiver = sub.add_parser("adopt-receiver")
    adopt_receiver.add_argument("--snapshot", type=Path, required=True)
    adopt_receiver.add_argument("--expected-snapshot-sha256", required=True)

    admit = sub.add_parser("admit-device")
    admit.add_argument("--device-id", required=True)
    admit.add_argument("--expected-snapshot-sha256", required=True)
    admit.add_argument("--name", required=True)


def handle(args: argparse.Namespace) -> int:
    if args.import_action == "admit-device":
        if args.development or args.credential_file is None:
            raise ServiceError(422, "import_admission_requires_owner_credential_file")
        native_directory(args.workspace)
        native_directory(args.credential_file.parent)
        runtime = open_runtime(args.workspace)
        if not isinstance(runtime.security, SecurityAuthority):
            raise ServiceError(503, "import_admission_unavailable")
        owner = runtime.security.authenticate(
            BearerProof(read_credential(args.credential_file))
        )
        reply = runtime.security.admit_imported_device(
            owner.principal,
            identity=runtime.operations.journal.state().identity,
            device_id=args.device_id,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
            name=args.name,
        )
        print(json.dumps(reply.data, indent=2))
        return 0
    if args.development or args.credential_file is not None:
        raise ServiceError(422, "import_requires_native_owner_maintenance")
    if args.import_action == "export-receiver":
        result = export_receiver(
            args.source_db,
            args.mapping_file,
            args.snapshot,
            expected_source_sha256=args.expected_source_sha256,
            expected_mapping_sha256=args.expected_mapping_sha256,
            confirm_quiesced=args.confirm_quiesced,
        )
    elif args.import_action == "adopt-receiver":
        result = import_receiver(
            args.workspace,
            args.snapshot,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
        )
    elif args.import_action == "export-manual-canary":
        selected = {
            "measurements": args.measurements_snapshot,
            "workouts": args.workouts_snapshot,
            "intake": args.intake_csv,
            "plan": args.plan_json,
            "preferences": args.preferences_json,
        }
        hashes = {
            name: getattr(args, "expected_" + name + "_sha256") for name in INPUTS
        }
        result = export_manual_canary(
            selected,
            args.snapshot,
            reviewed_hashes=hashes,
            source_revision=args.source_revision,
        )
    elif args.import_action == "adopt-manual-canary":
        result = import_manual_canary(
            args.workspace,
            args.snapshot,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
        )
    elif args.import_action == "export-measurements":
        result = export_measurements(
            args.source_csv,
            args.snapshot,
            source_id=args.source_id,
            expected_source_sha256=args.expected_source_sha256,
        )
    elif args.import_action == "export-workouts":
        result = export_workouts(
            args.sessions_csv,
            args.sets_csv,
            args.snapshot,
            source_id=args.source_id,
            expected_sessions_sha256=args.expected_sessions_sha256,
            expected_sets_sha256=args.expected_sets_sha256,
        )
    elif args.import_action == "adopt-workouts":
        result = import_workouts(
            args.workspace,
            args.snapshot,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
        )
    else:
        result = import_measurements(
            args.workspace,
            args.snapshot,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
        )
    print(json.dumps(result, indent=2))
    return 0

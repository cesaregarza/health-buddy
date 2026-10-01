"""Finite native backup maintenance commands, separate from HTTP health tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from health_buddy.backup import create, private_path, restore
from health_buddy.backup_archive import verified
from health_buddy.backup_crypto import MAX_ARCHIVE_BYTES, keygen, read_key, unseal
from health_buddy.core.files import read_file
from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import ServiceError
from health_buddy.security_runtime import open_runtime, read_credential


def add_commands(commands: Any) -> None:
    backup = commands.add_parser("backup")
    sub = backup.add_subparsers(dest="backup_action", required=True)
    key = sub.add_parser("keygen")
    key.add_argument("--key-file", type=Path, required=True)
    for action in ("create", "verify", "restore"):
        command = sub.add_parser(action)
        command.add_argument("--key-file", type=Path, required=True)
        command.add_argument("--archive", type=Path, required=True)
        if action == "create":
            command.add_argument("--confirm-quiesced", action="store_true")
        elif action == "restore":
            command.add_argument("--confirm-revoke-all", action="store_true")


def handle(args: argparse.Namespace) -> int:
    if args.development:
        raise ServiceError(422, "backup_requires_explicit_owner_mode")
    action = args.backup_action
    if action != "create" and args.credential_file is not None:
        raise ServiceError(
            422, "native_backup_maintenance_does_not_use_health_credential"
        )
    if action == "keygen":
        keygen(private_path(args.key_file))
        result = {"created": True, "key": "private_file_never_stdout"}
    elif action == "create":
        if args.credential_file is None:
            raise ServiceError(401, "explicit_owner_credential_file_required")
        runtime = open_runtime(args.workspace)
        owner = runtime.security.authenticate(
            BearerProof(read_credential(args.credential_file))
        )
        result = create(
            runtime,
            owner.principal,
            args.archive,
            args.key_file,
            confirm_quiesced=args.confirm_quiesced,
        )
    elif action == "restore":
        result = restore(
            args.workspace,
            args.archive,
            args.key_file,
            confirm_revoke_all=args.confirm_revoke_all,
        )
    else:
        raw = read_file(private_path(args.archive), MAX_ARCHIVE_BYTES + 256)
        manifest, files = verified(unseal(raw, read_key(private_path(args.key_file))))
        result = {
            "schemaVersion": 1,
            "verified": True,
            "files": len(files),
            "dataRevision": manifest["dataRevision"],
        }
    print(json.dumps(result, indent=2))
    return 0

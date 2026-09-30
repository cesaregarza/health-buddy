"""Explicit owner upgrade staging, outside ordinary health tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .security_api import BearerProof
from .security_runtime import open_runtime, read_credential
from .service_api import ServiceError
from .upgrade import stage


def add_commands(commands: Any) -> None:
    upgrade = commands.add_parser("upgrade")
    sub = upgrade.add_subparsers(dest="upgrade_action", required=True)
    command = sub.add_parser("stage")
    command.add_argument("--manifest", type=Path, required=True)
    command.add_argument("--manifest-sha256", required=True)
    command.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    command.add_argument("--archive", type=Path, required=True)
    command.add_argument("--key-file", type=Path, required=True)
    command.add_argument("--candidate", type=Path, required=True)
    command.add_argument("--confirm-quiesced", action="store_true")


def handle(args: argparse.Namespace) -> int:
    if args.development or args.credential_file is None:
        raise ServiceError(401, "upgrade_requires_explicit_owner_credential")
    runtime = open_runtime(args.workspace)
    owner = runtime.security.authenticate(
        BearerProof(read_credential(args.credential_file))
    )
    result = stage(
        runtime,
        owner.principal,
        args.manifest,
        args.manifest_sha256,
        args.architecture,
        args.archive,
        args.key_file,
        args.candidate,
        confirm_quiesced=args.confirm_quiesced,
    )
    print(json.dumps(result, indent=2))
    return 0

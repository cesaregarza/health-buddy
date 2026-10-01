"""Explicit owner upgrade staging, outside ordinary health tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from health_buddy.security_api import BearerProof
from health_buddy.security_runtime import open_runtime, read_credential
from health_buddy.service_api import ServiceError
from health_buddy.upgrade import stage
from health_buddy.upgrade_activation import activate


def add_commands(commands: Any) -> None:
    upgrade = commands.add_parser("upgrade")
    sub = upgrade.add_subparsers(dest="upgrade_action", required=True)
    for action in ("stage", "activate", "rollback", "recover"):
        command = sub.add_parser(action)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--manifest-sha256", required=True)
        command.add_argument(
            "--architecture", choices=("amd64", "arm64"), required=True
        )
        command.add_argument("--candidate", type=Path, required=True)
        command.add_argument("--confirm-quiesced", action="store_true")
        if action == "stage":
            command.add_argument("--archive", type=Path, required=True)
            command.add_argument("--key-file", type=Path, required=True)
        else:
            command.add_argument("--previous-manifest", type=Path, required=True)
            command.add_argument("--previous-sha256", required=True)
            command.add_argument("--runtime-env", type=Path, required=True)
            command.add_argument("--docker", type=Path, required=True)
            command.add_argument("--project", required=True)
            command.add_argument("--uid", type=int, required=True)
            command.add_argument("--gid", type=int, required=True)


def handle(args: argparse.Namespace) -> int:
    if args.development or args.credential_file is None:
        raise ServiceError(401, "upgrade_requires_explicit_owner_credential")
    runtime = open_runtime(args.workspace)
    owner = runtime.security.authenticate(
        BearerProof(read_credential(args.credential_file))
    )
    if args.upgrade_action == "stage":
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
    else:
        result = activate(
            runtime,
            owner.principal,
            args.candidate,
            args.manifest,
            args.manifest_sha256,
            args.architecture,
            args.previous_manifest,
            args.previous_sha256,
            args.runtime_env,
            args.docker,
            args.project,
            args.uid,
            args.gid,
            confirm_quiesced=args.confirm_quiesced,
            rollback=args.upgrade_action in {"rollback", "recover"},
            recover=args.upgrade_action == "recover",
        )
    print(json.dumps(result, indent=2))
    return 0

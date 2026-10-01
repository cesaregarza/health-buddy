"""Explicit native extension maintenance, separate from health-data credentials."""

from __future__ import annotations

import argparse
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from health_buddy.client.workflow import decoded
from health_buddy.core.config import load
from health_buddy.core.domain import decode
from health_buddy.core.extension_api import PrepareConnector
from health_buddy.core.files import read_file
from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import JSON, Request, ServiceError
from health_buddy.extension.install import install
from health_buddy.extension.jobs import run_event
from health_buddy.extension.personal_workspace import describe
from health_buddy.extension.prepare import prepare
from health_buddy.extension.registry import Registry, status_json
from health_buddy.security.runtime import open_runtime, read_credential


def add_commands(commands: Any) -> None:
    workspace = commands.add_parser("workspace")
    workspace.add_argument("action", choices=("describe",))
    workspace.add_argument("--json", action="store_true")
    workspace.add_argument("--upstream-base")
    extension = commands.add_parser("extension")
    sub = extension.add_subparsers(dest="extension_action", required=True)
    added = sub.add_parser("install")
    origin = added.add_mutually_exclusive_group(required=True)
    origin.add_argument("--from", dest="source", type=Path)
    origin.add_argument(
        "--example", choices=("local.weekly-mass", "local.water-import")
    )
    sub.add_parser("inspect")
    compatible = sub.add_parser("compatibility")
    compatible.add_argument("--extension-api", type=int, default=1)
    compatible.add_argument("--upstream-base")
    enable = sub.add_parser("enable")
    enable.add_argument("--id", required=True)
    enable.add_argument("--source-id", action="append", required=True)
    enable.add_argument("--secret-reference", action="append", default=[])
    enable.add_argument("--approve-egress", action="append", default=[])
    disable = sub.add_parser("disable")
    disable.add_argument("--id", required=True)
    revert = sub.add_parser("revert")
    revert.add_argument("--id", required=True)
    revert.add_argument("--review", required=True)
    prepared = sub.add_parser("prepare")
    prepared.add_argument("--id", required=True)
    prepared.add_argument("--source-id", required=True)
    prepared.add_argument("--credential-reference", required=True)
    prepared.add_argument("--rotate-existing", action="store_true")
    job = sub.add_parser("run")
    job.add_argument("--id", required=True)
    job.add_argument("--event-file", type=Path, required=True)
    metric = sub.add_parser("preview")
    metric.add_argument("--id", required=True)
    metric.add_argument("--source-id")
    metric.add_argument("--from", dest="from_time")
    metric.add_argument("--to", dest="to_time")


def handle(args: argparse.Namespace) -> int:
    config = load(args.workspace.expanduser().resolve())
    if args.development:
        raise ServiceError(422, "extension_requires_explicit_native_or_scoped_mode")
    if args.command == "workspace":
        print(json.dumps(describe(config, upstream_base=args.upstream_base), indent=2))
        return 0
    action = args.extension_action
    registry = Registry(config)
    result: dict[str, JSON]
    if action not in {"prepare", "run", "preview"} and args.credential_file is not None:
        raise ServiceError(422, "native_maintenance_does_not_use_health_credential")
    if action == "install":
        source = args.source or Path(
            str(files("health_buddy").joinpath("reference_extensions", args.example))
        )
        result = {"installed": install(config, source), "enabled": False}
    elif action == "inspect":
        result = {"items": [status_json(item) for item in registry.inspect()]}
    elif action == "compatibility":
        result = {
            "items": [
                status_json(item)
                for item in registry.compatibility(extension_api=args.extension_api)
            ],
            "workspace": describe(config, upstream_base=args.upstream_base),
        }
    elif action == "enable":
        refs = {}
        for binding in args.secret_reference:
            if "=" not in binding:
                raise ServiceError(422, "invalid_secret_reference_binding")
            key, path = binding.split("=", 1)
            if key in refs:
                raise ServiceError(422, "invalid_secret_reference_binding")
            refs[key] = path
        result = status_json(
            registry.enable(
                args.id,
                source_ids=tuple(args.source_id),
                secret_references=refs,
                approved_egress=tuple(args.approve_egress),
            )
        )
    elif action == "disable":
        result = status_json(registry.disable(args.id))
    elif action == "revert":
        result = status_json(registry.revert(args.id, args.review))
    else:
        if args.credential_file is None:
            raise ServiceError(401, "explicit_credential_file_required")
        proof = BearerProof(read_credential(args.credential_file))
        runtime = open_runtime(config.root)
        if action == "prepare":
            result = prepare(
                config,
                runtime,
                proof,
                PrepareConnector(
                    args.id,
                    args.source_id,
                    args.credential_reference,
                    args.rotate_existing,
                ),
            )
        elif action == "run":
            event = decode(read_file(args.event_file, 65_536), limit=65_536)
            if not isinstance(event, dict):
                raise ServiceError(422, "invalid_extension_event")
            result = run_event(config, runtime, proof, args.id, event)
        else:
            admitted = runtime.security.authenticate(proof)
            query = {
                key: value
                for key, value in {
                    "sourceId": args.source_id,
                    "from": args.from_time,
                    "to": args.to_time,
                }.items()
                if value is not None
            }
            result = decoded(
                runtime.operations.execute(
                    admitted.principal,
                    Request("extensions.read", resource_id=args.id, query=query),
                )
            )
    print(json.dumps(result, indent=2))
    return 0

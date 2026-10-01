"""Canonical local CLI; development authority requires an explicit selection."""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from pathlib import Path

from health_buddy.app import App
from health_buddy.core.config import ConfigError, load
from health_buddy.core.domain import decode
from health_buddy.core.git_store import StoreError
from health_buddy.core.loggers import FIELDS
from health_buddy.core.operations import open_service
from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import ServiceError
from health_buddy.security_runtime import open_runtime, read_credential, setup_security


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--credential-file",
        type=Path,
        help="Explicit private owner/agent bearer file; never a token argument",
    )
    parser.add_argument(
        "--development",
        action="store_true",
        help="Explicit local owner authority; never remote authentication",
    )
    parser.add_argument(
        "--new-write",
        action="store_true",
        help=(
            "Start a new intentionally repeated action; "
            "pending actions must be resolved first"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    from health_buddy.extension_cli import add_commands

    add_commands(commands)
    from health_buddy.backup_cli import add_commands as add_backup_commands

    add_backup_commands(commands)
    from health_buddy.upgrade_cli import add_commands as add_upgrade_commands

    add_upgrade_commands(commands)
    from health_buddy.import_cli import add_commands as add_import_commands

    add_import_commands(commands)
    commands.add_parser("init")
    commands.add_parser("render")
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--port", type=int, choices=range(1, 65536), metavar="PORT")
    commands.add_parser("support-bundle")
    security = commands.add_parser("security")
    security_commands = security.add_subparsers(dest="security_command", required=True)
    bootstrap = security_commands.add_parser("bootstrap")
    handoff = bootstrap.add_mutually_exclusive_group(required=True)
    handoff.add_argument("--proof-file", type=Path)
    handoff.add_argument("--owner-token-file", type=Path)
    recovery = security_commands.add_parser("recover")
    recovery.add_argument("--owner-token-file", type=Path, required=True)
    recovery.add_argument("--confirm-revoke-all", action="store_true")
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
        help=(
            "Discard retry state knowing the original write may already have saved; "
            "this does not undo it"
        ),
    )
    args = parser.parse_args(argv)
    try:
        if args.development and args.credential_file is not None:
            raise ServiceError(422, "development_cannot_use_credentials")
        if args.command in {"status", "doctor", "support-bundle"}:
            from health_buddy.operator_diagnostics import (
                finding,
                human,
                report,
                runtime_details,
                support_summary,
            )

            if args.command == "status" and not (
                args.development or args.credential_file is not None
            ):
                raise ServiceError(401, "explicit_credential_file_required")
            # Inspect local facts before opening canonical runtime. A broken
            # config must still produce an actionable JSON diagnostic.
            result = report(
                args.workspace.expanduser().resolve(), port=getattr(args, "port", None)
            )
            if (
                result["diagnostics"]
                and result["diagnostics"][0]["code"] == "config_invalid"
            ):
                pass
            elif args.development or args.credential_file is not None:
                try:
                    app = (
                        App.development(args.workspace)
                        if args.development
                        else App.authenticated(
                            args.workspace,
                            proof=BearerProof(read_credential(args.credential_file)),
                        )
                    )
                    runtime_details(result, app)
                except (
                    ConfigError,
                    StoreError,
                    OSError,
                    ValueError,
                    RuntimeError,
                    ServiceError,
                ) as exc:
                    code = (
                        "authorization_partial"
                        if isinstance(exc, ServiceError) and exc.status in {401, 403}
                        else "runtime_unavailable"
                    )
                    result["diagnostics"].append(finding(code, "error"))
            if args.command == "support-bundle":
                print(json.dumps(support_summary(result), indent=2))
            elif args.json:
                print(json.dumps(result, indent=2))
            else:
                print(human(result))
            return (
                2
                if any(item["severity"] == "error" for item in result["diagnostics"])
                else 0
            )
        if args.command == "legacy-import":
            from health_buddy.import_cli import handle as handle_import

            return handle_import(args)
        if args.command == "upgrade":
            from health_buddy.upgrade_cli import handle as handle_upgrade

            return handle_upgrade(args)
        if args.command == "backup":
            from health_buddy.backup_cli import handle as handle_backup

            return handle_backup(args)
        if args.command in {"workspace", "extension"}:
            from health_buddy.extension_cli import handle

            return handle(args)
        if args.command == "security":
            if args.development or args.credential_file is not None:
                raise ServiceError(422, "security_setup_requires_os_owner")
            recover = args.security_command == "recover"
            native_owner = not recover and args.owner_token_file is not None
            setup_security(
                args.workspace,
                args.owner_token_file if recover or native_owner else args.proof_file,
                recover=recover,
                confirm_revoke_all=recover and args.confirm_revoke_all,
                owner_token=native_owner,
            )
            print(
                "Private security handoff created. Keep the file private; "
                "its contents are not recoverable from HTTP replies."
            )
            return 0
        if args.command == "serve":
            from health_buddy.production_server import serve

            # Construct the service inside Granian's child, never in this
            # supervisor before the factory crosses its process boundary.
            serve(
                partial(open_runtime, args.workspace, development=args.development),
                ingress=load(args.workspace.expanduser().resolve()).ingress(),
                port=args.port,
                development=args.development,
            )
            return 0
        if args.command == "init":
            open_service(args.workspace)
            print(
                "Private canonical workspace ready. "
                "Optional sources stay explicitly configured."
            )
            return 0
        if args.development:
            app = App.development(args.workspace)
        elif args.credential_file is not None:
            app = App.authenticated(
                args.workspace, proof=BearerProof(read_credential(args.credential_file))
            )
        else:
            raise ServiceError(401, "explicit_credential_file_required")
        if args.command == "render":
            output = app.config.storage("cache") / "index.html"
            app.write_html(output)
            print(output)
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
            "Health Buddy could not open or update this workspace. "
            "Existing records and retry state were preserved.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

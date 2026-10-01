"""Supported image entrypoints; source is replaceable, the owner workspace is not."""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import sys
import time
from dataclasses import asdict
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from health_buddy.cli import main as canonical_cli
from health_buddy.core import config
from health_buddy.core.operations import open_service
from health_buddy.core.security_api import Runtime
from health_buddy.core.service_api import ServiceError
from health_buddy.core.workspace import create_file
from health_buddy.production_server import serve
from health_buddy.runtime.manifest import (
    ManifestError,
    native_directory,
    verify_source_identity,
)
from health_buddy.security.runtime import open_runtime

SOURCE = Path("/opt/health-buddy/source")
MANIFEST = Path("/opt/health-buddy/release/source-manifest.json")
WORKSPACE = Path("/workspace")


def private_workspace(root: Path) -> None:
    native_directory(root)
    details = root.lstat()
    if (
        os.geteuid() == 0
        or os.getegid() == 0
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise ManifestError("runtime_requires_existing_private_nonroot_workspace")


def managed_ingress(root: Path) -> config.Config:
    private_workspace(root)
    settings = config.load(root)
    ingress = settings.ingress()
    if (
        ingress.mode != "tailscale-uds"
        or settings.values["security"]["socketPath"] != "security/runtime/http.sock"
        or ingress.external_origin is None
    ):
        raise ManifestError("packaged_api_requires_explicit_managed_uds")
    return settings


def open_packaged(root: Path) -> Runtime:
    # Called in Granian's serving child, once. Ordinary discovery reads the DTO.
    managed_ingress(root)
    identity = verify_source_identity(SOURCE, MANIFEST)
    return open_runtime(root, release_identity=identity)


def initialize_packaged(root: Path, origin: str, subject: str) -> None:
    private_workspace(root)
    if next(root.iterdir(), None) is not None:
        raise ManifestError("packaged_initialization_requires_empty_workspace")
    values = config.defaults()
    values["security"].update(
        {
            "ingress": "tailscale-uds",
            "externalOrigin": origin,
            "ownerSubject": subject,
            "socketPath": "security/runtime/http.sock",
        }
    )
    config.validate(values, root)
    if not create_file(root / "config.json", json.dumps(values, indent=2) + "\n"):
        raise ManifestError("packaged_configuration_already_exists")
    open_service(root)


def health(root: Path) -> bool:
    """Finite local readiness request, no health credential or source-tree scan."""
    settings = managed_ingress(root)
    ingress = settings.ingress()
    assert ingress.external_origin is not None
    authority = urlsplit(ingress.external_origin).netloc
    request = (
        "GET /readyz HTTP/1.1\r\nHost: localhost\r\n"
        f"X-Forwarded-Host: {authority}\r\nX-Forwarded-Proto: https\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    deadline = time.monotonic() + 1.5
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1.5)
        connection.connect(str(settings.path("security/runtime/http.sock")))
        connection.sendall(request)
        response = bytearray()
        while len(response) <= 8192:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            connection.settimeout(remaining)
            chunk = connection.recv(min(4096, 8193 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
        if len(response) > 8192:
            return False
    header, separator, body = bytes(response).partition(b"\r\n\r\n")
    return (
        bool(separator)
        and header.split(b"\r\n", 1)[0] == b"HTTP/1.1 200 OK"
        and body == b'{"status":"ready"}'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api")
    sub.add_parser("health")
    sub.add_parser("info")
    fresh = sub.add_parser("init")
    fresh.add_argument("--external-origin", required=True)
    fresh.add_argument("--owner-subject", required=True)
    cli = sub.add_parser("cli")
    cli.add_argument("arguments", nargs=argparse.REMAINDER)
    job = sub.add_parser("job")
    job.add_argument("--id", required=True)
    job.add_argument("--event-file", type=Path, required=True)
    job.add_argument("--credential-file", type=Path, required=True)
    supplied = list(sys.argv[1:] if argv is None else argv)
    # The native wrapper has no options after its cli command; canonical global
    # options belong to that remainder. Normalize the optional explicit delimiter.
    position = 0
    while position < len(supplied):
        if supplied[position] == "--workspace":
            position += 2
        elif supplied[position].startswith("--workspace="):
            position += 1
        else:
            break
    if supplied[position : position + 1] == ["cli"]:
        position += 1
        if supplied[position : position + 1] != ["--"]:
            supplied.insert(position, "--")
    args = parser.parse_args(supplied)
    os.umask(0o077)
    try:
        private_workspace(args.workspace)
        if args.command == "health":
            return 0 if health(args.workspace) else 1
        if args.command == "api":
            settings = managed_ingress(args.workspace)
            serve(partial(open_packaged, args.workspace), ingress=settings.ingress())
            return 0
        identity = verify_source_identity(SOURCE, MANIFEST)
        if args.command == "info":
            print(json.dumps(asdict(identity), sort_keys=True))
        elif args.command == "init":
            initialize_packaged(
                args.workspace, args.external_origin, args.owner_subject
            )
            print(
                "Private workspace initialized; security bootstrap and "
                "host HTTPS proxy setup remain explicit."
            )
        elif args.command == "cli":
            arguments = (
                args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            )
            if any(
                item == "--workspace"
                or item.startswith("--workspace=")
                or item == "--development"
                for item in arguments
            ):
                raise ManifestError(
                    "packaged_cli_workspace_or_development_override_refused"
                )
            return canonical_cli(["--workspace", str(args.workspace), *arguments])
        elif args.command == "job":
            for path in (args.event_file, args.credential_file):
                if (
                    not path.is_absolute()
                    or not path.is_relative_to(args.workspace)
                    or ".." in path.parts
                ):
                    raise ManifestError("job_inputs_must_be_inside_owner_workspace")
                # Reject a linked ancestor before any input reader or canonical
                # dispatch can probe a descendant outside the owner workspace.
                native_directory(path.parent)
            return canonical_cli(
                [
                    "--workspace",
                    str(args.workspace),
                    "--credential-file",
                    str(args.credential_file),
                    "extension",
                    "run",
                    "--id",
                    args.id,
                    "--event-file",
                    str(args.event_file),
                ]
            )
    except (
        ManifestError,
        ServiceError,
        config.ConfigError,
        OSError,
        ValueError,
        RuntimeError,
    ):
        print(
            "packaged_runtime_unavailable; inspect private configuration "
            "and exact artifact evidence",
            file=sys.stderr,
        )
        return 1
    return 0

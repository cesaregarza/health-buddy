#!/usr/bin/env python3
"""Explicit source, pinned-input and Docker-archive packaging operations."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from health_buddy.runtime.artifact import containerd_store_diagnostic
from health_buddy.runtime.bundle import _write, create_bundle
from health_buddy.runtime.context import create_context
from health_buddy.runtime.inputs import fetch_inputs, load_inputs, verify_inputs
from health_buddy.runtime.manifest import (
    MAX_BYTES,
    ManifestError,
    file_digest,
    inventory,
    verify_source_identity,
)
from health_buddy.runtime.release import create_release, inspect_image, load_release

BUNDLE_ASSET = "health-buddy-bundle.tar"


def write_bundle_asset(bundle: Path, artifacts: Path) -> None:
    """Add the asset a fresh host installs from, and list it in SHA256SUMS.

    The archive holds one top-level bundle/ directory with source/ and
    release/{source.tar,source-manifest.json}. Owner, group and mtime stay
    zero, so the same verified bundle always packs to the same bytes.
    """
    verify_source_identity(bundle / "source", bundle / "release/source-manifest.json")
    files = {
        PurePosixPath("release", name): file_digest(
            bundle / "release" / name, MAX_BYTES
        )
        for name in ("source-manifest.json", "source.tar")
    }
    for item in inventory(bundle / "source"):
        files[PurePosixPath("source", str(item["path"]))] = (
            int(item["bytes"]),
            str(item["sha256"]),
        )
    folders = {folder for name in files for folder in name.parents}
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in sorted(folders | files.keys(), key=lambda item: item.parts):
            info = tarfile.TarInfo(str(PurePosixPath("bundle", entry)))
            if entry in folders:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755 if entry.parts else 0o700
                archive.addfile(info)
                continue
            size, digest = files[entry]
            path = bundle / entry
            info.size, info.mode = size, path.lstat().st_mode & 0o777
            archive.addfile(info, io.BytesIO(_verified(path, size, digest)))
    raw = packed.getvalue()
    _write(artifacts / BUNDLE_ASSET, raw)
    line = f"{hashlib.sha256(raw).hexdigest()}  {BUNDLE_ASSET}\n"
    descriptor = os.open(
        artifacts / "SHA256SUMS", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
    )
    with os.fdopen(descriptor, "ab") as sums:
        sums.write(line.encode("ascii"))
        sums.flush()
        os.fsync(sums.fileno())


def _verified(path: Path, size: int, digest: str) -> bytes:
    """The file's bytes, read once and checked against the verified inventory."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise ManifestError("bundle_changed")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("bundle")
    bundle.add_argument("--repository", type=Path, required=True)
    bundle.add_argument("--revision", required=True)
    bundle.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-source")
    verify.add_argument("--source", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    for name in ("fetch-inputs", "verify-inputs"):
        item = commands.add_parser(name)
        item.add_argument("--lock", type=Path, required=True)
        item.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
        item.add_argument("--directory", type=Path, required=True)
    context = commands.add_parser("context")
    context.add_argument("--bundle", type=Path, required=True)
    context.add_argument("--downloads", type=Path, required=True)
    context.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    context.add_argument("--output", type=Path, required=True)
    image = commands.add_parser("verify-image")
    image.add_argument("--bundle", type=Path, required=True)
    image.add_argument("--archive", type=Path, required=True)
    image.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    release = commands.add_parser("manifest")
    release.add_argument("--bundle", type=Path, required=True)
    release.add_argument("--artifacts", type=Path, required=True)
    load = commands.add_parser("load")
    load.add_argument("--manifest", type=Path, required=True)
    load.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    load.add_argument("--workspace", type=Path, required=True)
    load.add_argument("--output-env", type=Path, required=True)
    load.add_argument("--uid", type=int, required=True)
    load.add_argument("--gid", type=int, required=True)
    load.add_argument("--docker", type=Path, default=Path("/usr/bin/docker"))
    args = parser.parse_args(argv)
    try:
        if args.command == "bundle":
            create_bundle(args.repository, args.revision, args.output)
        elif args.command == "verify-source":
            verify_source_identity(args.source, args.manifest)
        elif args.command in {"fetch-inputs", "verify-inputs"}:
            inputs = load_inputs(args.lock, args.architecture)
            if args.command == "fetch-inputs":
                fetch_inputs(inputs, args.directory)
            else:
                verify_inputs(inputs, args.directory)
        elif args.command == "context":
            create_context(args.bundle, args.downloads, args.architecture, args.output)
        elif args.command == "verify-image":
            print(
                json.dumps(
                    asdict(inspect_image(args.bundle, args.archive, args.architecture)),
                    sort_keys=True,
                )
            )
            return 0
        elif args.command == "manifest":
            create_release(args.bundle, args.artifacts)
            write_bundle_asset(args.bundle, args.artifacts)
        elif args.command == "load":
            load_release(
                args.manifest,
                args.architecture,
                args.workspace,
                args.output_env,
                docker=args.docker,
                uid=args.uid,
                gid=args.gid,
            )
    except (
        ManifestError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        subprocess.SubprocessError,
    ) as error:
        diagnostic = (
            containerd_store_diagnostic(error)
            if isinstance(error, ManifestError)
            else None
        )
        detail = ""
        if diagnostic is not None:
            code, recovery = diagnostic
            detail = f"; {code}: {recovery}"
        print(
            "runtime_packaging_failed" + detail + "; preserve inputs and inspect "
            "selected artifact evidence",
            file=sys.stderr,
        )
        return 1
    print(
        "runtime packaging operation completed; "
        "publication and qualification remain separate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Explicit source, pinned-input and Docker-archive packaging operations."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from health_buddy.runtime_bundle import create_bundle
from health_buddy.runtime_context import create_context
from health_buddy.runtime_inputs import fetch_inputs, load_inputs, verify_inputs
from health_buddy.runtime_manifest import ManifestError, verify_source_identity
from health_buddy.runtime_release import create_release, inspect_image, load_release


def main() -> int:
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
    args = parser.parse_args()
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
    ):
        print(
            "runtime_packaging_failed; preserve inputs and inspect selected artifact evidence",
            file=sys.stderr,
        )
        return 1
    print(
        "runtime packaging operation completed; publication and qualification remain separate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

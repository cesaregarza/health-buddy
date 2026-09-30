#!/usr/bin/env python3
"""Create or verify a history-free exact runtime source bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Fixed adjacent release code, never an owner workspace module path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from health_buddy.runtime_bundle import create_bundle  # noqa: E402
from health_buddy.runtime_manifest import ManifestError, verify_source_identity  # noqa: E402


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
    args = parser.parse_args()
    try:
        if args.command == "bundle":
            create_bundle(args.repository, args.revision, args.output)
        else:
            verify_source_identity(args.source, args.manifest)
    except (ManifestError, OSError, ValueError, KeyError, TypeError):
        print("runtime_source_verification_failed; preserve inputs and inspect the selected artifact", file=sys.stderr)
        return 1
    print("runtime source verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

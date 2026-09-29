"""Compatibility launcher for the canonical workspace server.

Legacy database migration, token issuance/revocation and loose export commands
are retired. Security administration belongs to the workspace authority.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Never


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--development", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", allow_abbrev=False)
    serve.add_argument("--port", type=int, default=8791)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()

    def refuse(message: str) -> Never:
        raise ValueError("retired receiver command")

    parser.error = refuse  # type: ignore[method-assign]
    try:
        args = parser.parse_args(argv)
    except ValueError:
        print(
            "Use health-ingest --workspace PATH [--development] serve. "
            "Legacy standalone database and device-token commands are retired.",
            file=sys.stderr,
        )
        return 2
    from health_buddy.cli import main as canonical_main

    selected = ["--workspace", args.workspace]
    if args.development:
        selected.append("--development")
    return canonical_main([*selected, "serve", "--port", str(args.port)])


if __name__ == "__main__":
    raise SystemExit(main())

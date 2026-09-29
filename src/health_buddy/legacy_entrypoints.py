"""Explicit-workspace compatibility entrypoints, never legacy loose writers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Never


def delegate_logger(kind: str, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Use this logger through the canonical private workspace.",
        allow_abbrev=False,
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--new-write", action="store_true")

    def refuse(message: str) -> Never:
        raise ValueError("explicit workspace required")

    parser.error = refuse  # type: ignore[method-assign]
    try:
        options, fields = parser.parse_known_args(argv)
        if kind == "workout":
            if not fields or fields[0] not in {"start", "set", "cardio", "finish"}:
                raise ValueError("workout operation required")
            kind = "workout-" + fields.pop(0)
    except ValueError:
        print(
            "Legacy logging requires explicit --workspace and a supported "
            "operation; loose-file output is retired.",
            file=sys.stderr,
        )
        return 2
    from .cli import main

    selected = ["--workspace", options.workspace]
    if options.development:
        selected.append("--development")
    if options.new_write:
        selected.append("--new-write")
    return main([*selected, "log", kind, *fields])

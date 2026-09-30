#!/usr/bin/env python3
"""Materialize one deterministic daily training prescription for the dashboard."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

try:
    from scripts.next_workout import (
        DEFAULT_PROGRAM,
        DEFAULT_SESSIONS,
        DEFAULT_SETS,
        ProgramError,
        build_recommendation,
        load_program,
        load_sessions,
    )
    from scripts.prescription_progression import ProgressionError, load_sets
except ModuleNotFoundError:  # direct execution: python scripts/publish_training_day.py
    from next_workout import (  # type: ignore[no-redef, import-not-found]
        DEFAULT_PROGRAM,
        DEFAULT_SESSIONS,
        DEFAULT_SETS,
        ProgramError,
        build_recommendation,
        load_program,
        load_sessions,
    )
    from prescription_progression import (  # type: ignore[no-redef, import-not-found]
        ProgressionError,
        load_sets,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "plans" / "training-days"


def build_snapshot(
    target_date: date,
    program_path: Path,
    sessions_path: Path,
    sets_path: Path,
) -> dict[str, Any]:
    """Build a display artifact without creating a second prescription source."""
    recommendation = build_recommendation(
        load_program(program_path),
        load_sessions(sessions_path),
        target_date,
        load_sets(sets_path),
    )
    return {
        "schema_version": 1,
        "date": target_date.isoformat(),
        "generator": "scripts/publish_training_day.py",
        "recommendation": recommendation,
    }


def render_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def publish_snapshot(
    snapshot: dict[str, Any], output_dir: Path, *, check: bool = False
) -> tuple[Path, str]:
    target = output_dir / f"{snapshot['date']}.json"
    rendered = render_snapshot(snapshot)
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    if check:
        if existing != rendered:
            raise RuntimeError(f"training snapshot is missing or stale: {target}")
        return target, "current"
    if existing == rendered:
        return target, "unchanged"
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, target)
    return target, "updated" if existing is not None else "created"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a committed dashboard snapshot from the canonical daily "
            "training selector."
        )
    )
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    parser.add_argument("--sessions-file", type=Path, default=DEFAULT_SESSIONS)
    parser.add_argument("--sets-file", type=Path, default=DEFAULT_SETS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail unless the committed snapshot already matches current inputs",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the JSON instead of writing a file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        snapshot = build_snapshot(
            args.date, args.program, args.sessions_file, args.sets_file
        )
        if args.stdout:
            print(render_snapshot(snapshot), end="")
            return 0
        target, status = publish_snapshot(snapshot, args.output_dir, check=args.check)
    except (OSError, ProgramError, ProgressionError, RuntimeError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    display_path = (
        target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target
    )
    print(f"{status}: {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

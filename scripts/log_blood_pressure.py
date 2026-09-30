#!/usr/bin/env python3
"""Insert or correct one validated home blood-pressure reading."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

FIELDNAMES = [
    "measured_at_local",
    "timezone",
    "systolic_mm_hg",
    "diastolic_mm_hg",
    "pulse_bpm",
    "arm",
    "reading_number",
    "measurement_session",
    "device",
    "source",
    "notes",
    "protocol_status",
    "protocol_notes",
]
DEFAULT_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "blood_pressure.csv"


def _bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"not an integer: {value}") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}: {value}"
            )
        return parsed

    return parse


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Insert one validated BP reading without double-counting it.",
        epilog=(
            "Example: scripts/log_blood_pressure.py --measured-at-local "
            "2026-09-01T08:00:00 --systolic 112 --diastolic 77 --pulse 79 "
            "--reading-number 1 --measurement-session morning "
            "--source user_reported --protocol-status unknown"
        ),
    )
    parser.add_argument("--measured-at-local", required=True)
    parser.add_argument("--timezone", default=os.environ.get("HEALTH_TIMEZONE", "UTC"))
    parser.add_argument("--systolic", required=True, type=_bounded_integer(40, 300))
    parser.add_argument("--diastolic", required=True, type=_bounded_integer(20, 200))
    parser.add_argument("--pulse", type=_bounded_integer(20, 250))
    parser.add_argument("--arm", default="")
    parser.add_argument("--reading-number", required=True, type=_bounded_integer(1, 20))
    parser.add_argument("--measurement-session", default="other")
    parser.add_argument("--device", default="")
    parser.add_argument("--source", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--protocol-status",
        choices=("valid", "invalid", "unknown"),
        default="unknown",
    )
    parser.add_argument("--protocol-notes", default="")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace the sole existing row at --measured-at-local.",
    )
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    return parser


def _timestamp(value: str) -> str:
    if "T" not in value:
        raise ValueError("measured_at_local must include date and time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("measured_at_local must be a valid ISO-8601 datetime") from exc
    return parsed.isoformat(timespec="seconds")


def _row(namespace: argparse.Namespace) -> dict[str, str]:
    for name in ("measurement_session", "source"):
        if not getattr(namespace, name).strip():
            raise ValueError(f"{name} must not be empty")
    return {
        "measured_at_local": _timestamp(namespace.measured_at_local),
        "timezone": namespace.timezone.strip(),
        "systolic_mm_hg": str(namespace.systolic),
        "diastolic_mm_hg": str(namespace.diastolic),
        "pulse_bpm": "" if namespace.pulse is None else str(namespace.pulse),
        "arm": namespace.arm.strip(),
        "reading_number": str(namespace.reading_number),
        "measurement_session": namespace.measurement_session.strip(),
        "device": namespace.device.strip(),
        "source": namespace.source.strip(),
        "notes": namespace.notes,
        "protocol_status": namespace.protocol_status,
        "protocol_notes": namespace.protocol_notes,
    }


def _load(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDNAMES:
            raise ValueError(f"unexpected blood-pressure CSV header in {path}")
        return [dict(row) for row in reader]


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def log_blood_pressure(
    path: Path, row: dict[str, str], *, replace_existing: bool = False
) -> str:
    path = path.expanduser().resolve()
    rows = _load(path)
    matching = [
        index
        for index, existing in enumerate(rows)
        if existing["measured_at_local"] == row["measured_at_local"]
    ]
    if replace_existing:
        if len(matching) != 1:
            raise ValueError(
                "--replace-existing requires exactly one row at "
                f"{row['measured_at_local']}; found {len(matching)}"
            )
        if rows[matching[0]] == row:
            return "unchanged"
        rows[matching[0]] = row
        status = "replaced"
    elif row in rows:
        return "unchanged"
    elif matching:
        raise ValueError(
            f"a different reading already exists at {row['measured_at_local']}; "
            "use --replace-existing to correct it"
        )
    else:
        rows.append(row)
        status = "inserted"
    rows.sort(key=lambda existing: existing["measured_at_local"])
    _write(path, rows)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    try:
        from health_buddy.legacy_entrypoints import delegate_logger
    except ImportError:
        print("Install Health Buddy and use an explicit --workspace; "
              "loose-file logging is retired.", file=sys.stderr)
        return 2
    return delegate_logger('blood-pressure', argv)


if __name__ == "__main__":
    raise SystemExit(main())

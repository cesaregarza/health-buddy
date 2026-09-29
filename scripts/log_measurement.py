#!/usr/bin/env python3
"""Append one validated body measurement to the canonical CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDNAMES = [
    "measured_at_local",
    "timezone",
    "weight_lb",
    "body_fat_pct",
    "muscle_mass_pct",
    "water_pct",
    "bmi",
    "bone_mass_pct",
    "source",
    "notes",
]
DEFAULT_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "measurements.csv"


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append one validated measurement without creating duplicates.",
        epilog=(
            "Fabricated example: scripts/log_measurement.py --measured-at-local "
            "2030-01-01T08:00:00 --weight-lb 150 --source manual_entry"
        ),
    )
    parser.add_argument("--measured-at-local", required=True)
    parser.add_argument("--timezone", default="")
    parser.add_argument("--weight-lb", required=True, type=_decimal)
    parser.add_argument("--body-fat-pct", type=_decimal)
    parser.add_argument("--muscle-mass-pct", type=_decimal)
    parser.add_argument("--water-pct", type=_decimal)
    parser.add_argument("--bmi", type=_decimal)
    parser.add_argument("--bone-mass-pct", type=_decimal)
    parser.add_argument("--source", default="manual_entry")
    parser.add_argument("--notes", default="")
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    return parser


def _bounded(name: str, value: Decimal, lower: Decimal, upper: Decimal) -> None:
    if not lower < value <= upper:
        raise ValueError(f"{name} must be greater than {lower} and at most {upper}")


def _timestamp(value: str) -> str:
    if "T" not in value:
        raise ValueError("measured_at_local must include date and time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("measured_at_local must be a valid ISO-8601 datetime") from exc
    return parsed.isoformat(timespec="seconds")


def _one_decimal(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.1f}"


def _row(namespace: argparse.Namespace) -> dict[str, str]:
    _bounded("weight_lb", namespace.weight_lb, Decimal("0"), Decimal("1500"))
    optional_bounds = (
        ("body_fat_pct", namespace.body_fat_pct),
        ("muscle_mass_pct", namespace.muscle_mass_pct),
        ("water_pct", namespace.water_pct),
        ("bmi", namespace.bmi),
        ("bone_mass_pct", namespace.bone_mass_pct),
    )
    for name, value in optional_bounds:
        if value is not None:
            _bounded(name, value, Decimal("0"), Decimal("100"))
    if not namespace.source.strip():
        raise ValueError("source must not be empty")

    return {
        "measured_at_local": _timestamp(namespace.measured_at_local),
        "timezone": namespace.timezone,
        "weight_lb": _one_decimal(namespace.weight_lb),
        "body_fat_pct": _one_decimal(namespace.body_fat_pct),
        "muscle_mass_pct": _one_decimal(namespace.muscle_mass_pct),
        "water_pct": _one_decimal(namespace.water_pct),
        "bmi": _one_decimal(namespace.bmi),
        "bone_mass_pct": _one_decimal(namespace.bone_mass_pct),
        "source": namespace.source,
        "notes": namespace.notes,
    }


def append_measurement(path: Path, row: dict[str, str]) -> str:
    path = path.expanduser().resolve()
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDNAMES:
                raise ValueError(f"unexpected measurement CSV header in {path}")
            for existing in reader:
                if existing["measured_at_local"] != row["measured_at_local"]:
                    continue
                if existing == row:
                    return "unchanged"
                raise ValueError(
                    "a different measurement already exists at "
                    f"{row['measured_at_local']}"
                )

    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    return "inserted"


def main(argv: Sequence[str] | None = None) -> int:
    namespace = _parser().parse_args(argv)
    try:
        row = _row(namespace)
        status = append_measurement(namespace.data_file, row)
    except (OSError, ValueError) as exc:
        print(f"log_measurement: error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "data_file": str(namespace.data_file.expanduser().resolve()),
                "measured_at_local": row["measured_at_local"],
                "status": status,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

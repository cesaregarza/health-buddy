#!/usr/bin/env python3
"""Insert or correct one validated food, beverage, or supplement event."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDNAMES = [
    "event_at_local",
    "timezone",
    "status",
    "category",
    "item_name",
    "brand",
    "serving_quantity",
    "serving_unit",
    "calories_kcal",
    "protein_g",
    "carbohydrate_g",
    "fat_g",
    "sodium_mg",
    "caffeine_mg",
    "source",
    "notes",
]
LEGACY_FIELDNAMES = [field for field in FIELDNAMES if field != "sodium_mg"]
DEFAULT_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "intake.csv"


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must not be negative: {value}")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Insert one validated intake event without double-counting it.",
        epilog=(
            "Fabricated example: scripts/log_intake.py --event-at-local "
            "2030-01-01T12:00:00 --status consumed --category meal "
            "--item-name 'Example meal' --serving-quantity 1 --serving-unit portion "
            "--calories-kcal 400 --protein-g 20 --carbohydrate-g 50 --fat-g 10 "
            "--sodium-mg 300 --source synthetic_example"
        ),
    )
    parser.add_argument("--event-at-local", required=True)
    parser.add_argument("--timezone", default=os.environ.get("HEALTH_TIMEZONE", "UTC"))
    parser.add_argument(
        "--status", required=True, choices=("ordered", "planned", "consumed")
    )
    parser.add_argument("--category", required=True)
    parser.add_argument("--item-name", required=True)
    parser.add_argument("--brand", default="")
    parser.add_argument("--serving-quantity", type=_decimal)
    parser.add_argument("--serving-unit", default="")
    parser.add_argument("--calories-kcal", type=_decimal)
    parser.add_argument("--protein-g", type=_decimal)
    parser.add_argument("--carbohydrate-g", type=_decimal)
    parser.add_argument("--fat-g", type=_decimal)
    parser.add_argument("--sodium-mg", type=_decimal)
    parser.add_argument("--caffeine-mg", type=_decimal)
    parser.add_argument("--source", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace the sole existing row at --event-at-local.",
    )
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    return parser


def _timestamp(value: str) -> str:
    if "T" not in value:
        raise ValueError("event_at_local must include date and time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("event_at_local must be a valid ISO-8601 datetime") from exc
    return parsed.isoformat(timespec="seconds")


def _format(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _row(namespace: argparse.Namespace) -> dict[str, str]:
    for name in ("category", "item_name", "source"):
        if not getattr(namespace, name).strip():
            raise ValueError(f"{name} must not be empty")
    if namespace.serving_quantity is not None and not namespace.serving_unit.strip():
        raise ValueError("serving_unit is required with serving_quantity")

    return {
        "event_at_local": _timestamp(namespace.event_at_local),
        "timezone": namespace.timezone,
        "status": namespace.status,
        "category": namespace.category.strip(),
        "item_name": namespace.item_name.strip(),
        "brand": namespace.brand.strip(),
        "serving_quantity": _format(namespace.serving_quantity),
        "serving_unit": namespace.serving_unit.strip(),
        "calories_kcal": _format(namespace.calories_kcal),
        "protein_g": _format(namespace.protein_g),
        "carbohydrate_g": _format(namespace.carbohydrate_g),
        "fat_g": _format(namespace.fat_g),
        "sodium_mg": _format(namespace.sodium_mg),
        "caffeine_mg": _format(namespace.caffeine_mg),
        "source": namespace.source.strip(),
        "notes": namespace.notes,
    }


def _load(path: Path) -> tuple[list[dict[str, str]], bool]:
    if not path.exists():
        return [], False
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames not in (FIELDNAMES, LEGACY_FIELDNAMES):
            raise ValueError(f"unexpected intake CSV header in {path}")
        migrated = reader.fieldnames == LEGACY_FIELDNAMES
        rows = []
        for existing in reader:
            if migrated:
                existing["sodium_mg"] = ""
            rows.append({field: existing.get(field, "") for field in FIELDNAMES})
        return rows, migrated


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


def log_intake(
    path: Path, row: dict[str, str], *, replace_existing: bool = False
) -> tuple[str, bool]:
    path = path.expanduser().resolve()
    rows, migrated = _load(path)
    matching = [
        index
        for index, existing in enumerate(rows)
        if existing["event_at_local"] == row["event_at_local"]
    ]

    if replace_existing:
        if len(matching) != 1:
            raise ValueError(
                "--replace-existing requires exactly one row at "
                f"{row['event_at_local']}; found {len(matching)}"
            )
        if rows[matching[0]] == row:
            if migrated:
                _write(path, rows)
            return "unchanged", migrated
        rows[matching[0]] = row
        status = "replaced"
    elif row in rows:
        if migrated:
            rows.sort(key=lambda existing: existing["event_at_local"])
            _write(path, rows)
        return "unchanged", migrated
    else:
        rows.append(row)
        status = "inserted"

    rows.sort(key=lambda existing: existing["event_at_local"])
    _write(path, rows)
    return status, migrated


def main(argv: Sequence[str] | None = None) -> int:
    try:
        from health_buddy.legacy_entrypoints import delegate_logger
    except ImportError:
        print("Install Health Buddy and use an explicit --workspace; "
              "loose-file logging is retired.", file=sys.stderr)
        return 2
    return delegate_logger('intake', argv)


if __name__ == "__main__":
    raise SystemExit(main())

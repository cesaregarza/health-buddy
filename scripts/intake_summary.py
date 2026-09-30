#!/usr/bin/env python3
"""Summarize known nutrition totals for one local calendar date."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypedDict

DEFAULT_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "intake.csv"
NUTRIENTS = (
    "calories_kcal",
    "protein_g",
    "carbohydrate_g",
    "fat_g",
    "sodium_mg",
    "caffeine_mg",
)
LABELS = {
    "calories_kcal": ("Calories", "kcal"),
    "protein_g": ("Protein", "g"),
    "carbohydrate_g": ("Carbohydrate", "g"),
    "fat_g": ("Fat", "g"),
    "sodium_mg": ("Sodium", "mg"),
    "caffeine_mg": ("Caffeine", "mg"),
}
REQUIRED_FIELDS = {"event_at_local", "status", "item_name", *NUTRIENTS}


class NutrientSummary(TypedDict):
    known_total: str
    known_events: int
    missing_events: int


class IntakeSummary(TypedDict):
    date: str
    status: str
    event_count: int
    items: list[str]
    nutrients: dict[str, NutrientSummary]


def _parse_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def summarize_intake(path: Path, local_date: str, status: str) -> IntakeSummary:
    if not path.is_file():
        raise ValueError(f"intake file does not exist: {path}")

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing_fields = sorted(REQUIRED_FIELDS - fields)
        if missing_fields:
            raise ValueError(
                "intake CSV is missing required fields: " + ", ".join(missing_fields)
            )
        rows = [
            row
            for row in reader
            if row["event_at_local"].startswith(f"{local_date}T")
            and row["status"] == status
        ]

    if not rows:
        raise ValueError(f"no {status} intake events found for {local_date}")

    nutrient_summary: dict[str, NutrientSummary] = {}
    for nutrient in NUTRIENTS:
        total = Decimal("0")
        known_events = 0
        for row in rows:
            raw = row[nutrient].strip()
            if not raw:
                continue
            try:
                total += Decimal(raw)
            except InvalidOperation as exc:
                raise ValueError(
                    f"invalid {nutrient} value {raw!r} for {row['item_name']!r}"
                ) from exc
            known_events += 1
        nutrient_summary[nutrient] = {
            "known_total": _format_decimal(total),
            "known_events": known_events,
            "missing_events": len(rows) - known_events,
        }

    return {
        "date": local_date,
        "status": status,
        "event_count": len(rows),
        "items": [row["item_name"] for row in rows],
        "nutrients": nutrient_summary,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sum known nutrient values for one local date without treating blank "
            "fields as zero."
        ),
        epilog=(
            "Example: scripts/intake_summary.py --date 2026-08-30 "
            "--status consumed"
        ),
    )
    parser.add_argument("--date", required=True, type=_parse_date)
    parser.add_argument(
        "--status",
        choices=("ordered", "planned", "consumed"),
        default="consumed",
    )
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    return parser


def _render_text(summary: IntakeSummary) -> str:
    lines = [
        f"Date: {summary['date']}",
        f"Status: {summary['status']}",
        f"Events: {summary['event_count']}",
        "Known central totals (blank source values are not counted as zero):",
    ]
    for nutrient in NUTRIENTS:
        label, unit = LABELS[nutrient]
        value = summary["nutrients"][nutrient]
        coverage = f"{value['known_events']}/{summary['event_count']} events"
        if value["missing_events"]:
            coverage += f"; {value['missing_events']} missing"
        lines.append(f"  {label}: {value['known_total']} {unit} ({coverage})")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = summarize_intake(args.data_file, args.date, args.status)
    except ValueError as exc:
        print(f"intake_summary: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(_render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

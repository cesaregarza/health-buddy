#!/usr/bin/env python3
"""Import timestamp-matched Weight Gurus readings from the read-only Pi query.

Preview by default; pass --apply to append missing rows to measurements.csv.
Use --recent-days for a repeatable daily reconciliation window.
"""

from __future__ import annotations

import argparse
import os
import csv
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.log_measurement import DEFAULT_DATA_FILE, FIELDNAMES, append_measurement

QUERY_SCRIPT = Path(__file__).resolve().with_name("health_db_pi")
TYPES = {
    "weight": "HKQuantityTypeIdentifierBodyMass",
    "body_fat": "HKQuantityTypeIdentifierBodyFatPercentage",
    "bmi": "HKQuantityTypeIdentifierBodyMassIndex",
    "lean": "HKQuantityTypeIdentifierLeanBodyMass",
}
KG_TO_LB = Decimal("2.20462262185")


def _one_decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def recent_window(today: date, days: int) -> tuple[date, date]:
    if days < 1:
        raise ValueError("--recent-days must be at least 1")
    return today - timedelta(days=days - 1), today


def _fetch(query_script: Path, identifier: str, since: date, limit: int) -> list[dict]:
    command = [
        str(query_script),
        "--format",
        "json",
        "latest",
        "--type",
        identifier,
        "--since",
        since.isoformat(),
        "--limit",
        str(limit),
    ]
    # The fixed repository query script reads the private Pi database without writes.
    result = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, check=True
    )
    records = json.loads(result.stdout)
    if not isinstance(records, list):
        raise ValueError("HealthKit query did not return a record list")
    if len(records) >= limit:
        raise ValueError(f"{identifier} reached query limit {limit}; raise --limit")
    return records


def _index(records: list[dict], identifier: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for record in records:
        if record.get("type_identifier") != identifier:
            raise ValueError(f"unexpected HealthKit type in {identifier} query")
        if record.get("source_name") != "Weight Gurus":
            continue
        instant = record["start_at"]
        if instant in indexed and indexed[instant] != record:
            raise ValueError(f"conflicting {identifier} records at {instant}")
        indexed[instant] = record
    return indexed


def _value(record: dict | None, unit: str) -> Decimal | None:
    if record is None:
        return None
    if record.get("unit") != unit:
        raise ValueError(
            f"unexpected {record.get('type_identifier')} unit: {record.get('unit')}"
        )
    return Decimal(str(record["value"]))


def _rows(
    by_type: dict[str, dict[str, dict]], since: date, through: date
) -> list[dict[str, str]]:
    zone = ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))
    rows = []
    for instant, weight_record in by_type["weight"].items():
        moment = datetime.fromisoformat(instant.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError(f"HealthKit timestamp lacks UTC offset: {instant}")
        local = moment.astimezone(zone)
        if not since <= local.date() <= through:
            continue
        weight = _value(weight_record, "kg")
        if weight is None or not Decimal("0") < weight < Decimal("700"):
            raise ValueError(f"invalid weight at {instant}")
        fat = _value(by_type["body_fat"].get(instant), "%")
        if fat is not None:
            fat = fat * 100 if fat <= Decimal("1.5") else fat
            if not Decimal("0") < fat <= Decimal("100"):
                raise ValueError(f"invalid body fat at {instant}")
        bmi = _value(by_type["bmi"].get(instant), "count")
        lean = _value(by_type["lean"].get(instant), "kg")
        lean_note = (
            f"Lean body mass {_one_decimal(lean * KG_TO_LB)} lb; "
            if lean is not None
            else ""
        )
        rows.append(
            {
                "measured_at_local": local.replace(tzinfo=None).isoformat(
                    timespec="seconds"
                ),
                "timezone": zone.key,
                "weight_lb": _one_decimal(weight * KG_TO_LB),
                "body_fat_pct": _one_decimal(fat) if fat is not None else "",
                "muscle_mass_pct": "",
                "water_pct": "",
                "bmi": _one_decimal(bmi) if bmi is not None else "",
                "bone_mass_pct": "",
                "source": "apple_health_weight_gurus",
                "notes": (
                    "Backfilled from synchronized HealthKit records with "
                    "Weight Gurus provenance. "
                    f"{lean_note}muscle, water and bone percentages unavailable."
                ),
            }
        )
    return sorted(rows, key=lambda row: row["measured_at_local"])


def _existing(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDNAMES:
            raise ValueError(f"unexpected measurement CSV header in {path}")
        return {row["measured_at_local"]: row for row in reader}


def import_weights(
    query_script: Path,
    data_file: Path,
    since: date,
    through: date,
    limit: int,
    apply: bool,
) -> list[dict[str, str]]:
    if since > through:
        raise ValueError("--since must not be after --through")
    by_type = {
        name: _index(_fetch(query_script, identifier, since, limit), identifier)
        for name, identifier in TYPES.items()
    }
    rows = _rows(by_type, since, through)
    existing = _existing(data_file)
    preview = []
    for row in rows:
        prior = existing.get(row["measured_at_local"])
        if prior is not None and any(
            prior[key] != row[key] for key in ("weight_lb", "body_fat_pct", "bmi")
        ):
            raise ValueError(f"conflicting measurement at {row['measured_at_local']}")
        preview.append(
            {
                "measured_at_local": row["measured_at_local"],
                "weight_lb": row["weight_lb"],
                "status": "unchanged" if prior else "ready",
            }
        )
    if apply:
        for row, item in zip(rows, preview, strict=True):
            if item["status"] == "ready":
                append_measurement(data_file, row)
                item["status"] = "inserted"
    return preview


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument(
        "--recent-days",
        type=int,
        help="include this many Chicago calendar days through today",
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 2:
        parser.error("--limit must be at least 2")
    if args.recent_days is not None:
        if args.since is not None or args.through is not None:
            parser.error("--recent-days cannot be combined with --since or --through")
        try:
            since, through = recent_window(
                datetime.now(ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))).date(), args.recent_days
            )
        except ValueError as exc:
            parser.error(str(exc))
    else:
        if args.since is None or args.through is None:
            parser.error("provide --recent-days or both --since and --through")
        since, through = args.since, args.through
    try:
        result = import_weights(
            QUERY_SCRIPT,
            args.data_file,
            since,
            through,
            args.limit,
            args.apply,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"import_healthkit_weights: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

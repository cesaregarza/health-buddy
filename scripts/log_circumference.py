#!/usr/bin/env python3
"""Preview or append one repeatable tape measurement to the canonical CSVs."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
WAIST_FIELDS = [
    "measured_at_local",
    "timezone",
    "waist_in",
    "measurement_site",
    "source",
    "notes",
]
BODY_FIELDS = [
    "measured_at_local",
    "timezone",
    "body_site",
    "side",
    "circumference_in",
    "measurement_site",
    "source",
    "notes",
]
SITES = {"waist", "chest", "upper_arm", "thigh"}
SIDED_SITES = {"upper_arm", "thigh"}
FRACTION = re.compile(r"(?:(\d+)\s+)?(\d+)/(2|4|8|16|32)")


def inches(raw: str) -> Decimal:
    """Accept decimal inches or common mixed fractions such as '40 7/8'."""
    value = raw.strip()
    match = FRACTION.fullmatch(value)
    if match:
        whole, numerator, denominator = match.groups()
        if int(numerator) >= int(denominator):
            raise argparse.ArgumentTypeError(
                "fraction numerator must be smaller than denominator"
            )
        result = Decimal(whole or 0) + Decimal(numerator) / Decimal(denominator)
    else:
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise argparse.ArgumentTypeError(f"invalid inch reading: {raw}") from exc
    if not result.is_finite() or not Decimal("0") < result <= Decimal("200"):
        raise argparse.ArgumentTypeError(
            "inch reading must be greater than 0 and at most 200"
        )
    return result


def plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def build_row(args: argparse.Namespace) -> tuple[Path, list[str], dict[str, str]]:
    if (args.site in SIDED_SITES) != (args.side is not None):
        raise ValueError(
            "--side is required for arms/thighs and forbidden for waist/chest"
        )
    if len(args.reading) not in (2, 3):
        raise ValueError("provide two or three --reading values")
    try:
        measured = datetime.fromisoformat(args.measured_at_local)
    except ValueError as exc:
        raise ValueError("--measured-at-local must be an ISO datetime") from exc
    if measured.tzinfo is not None:
        raise ValueError("--measured-at-local must be local time without an offset")
    if not args.measurement_site.strip() or not args.source.strip():
        raise ValueError("measurement site and source must not be empty")

    values = [inches(reading) for reading in args.reading]
    result = (values[0] + values[1]) / 2 if len(values) == 2 else median(values)
    method = "mean" if len(values) == 2 else "median"
    notes = (
        f"Readings: {', '.join(args.reading)} in; stored {method} {plain(result)} in. "
        "Timestamp approximates chat-report time; stored value does not imply "
        "greater tape precision."
    )
    if args.notes:
        notes += f" {args.notes}"
    common = {
        "measured_at_local": measured.isoformat(timespec="seconds"),
        "timezone": args.timezone,
        "measurement_site": args.measurement_site,
        "source": args.source,
        "notes": notes,
    }
    if args.site == "waist":
        return (
            args.data_dir / "waist.csv",
            WAIST_FIELDS,
            {**common, "waist_in": plain(result)},
        )
    return (
        args.data_dir / "body_circumferences.csv",
        BODY_FIELDS,
        {
            **common,
            "body_site": args.site,
            "side": args.side or "",
            "circumference_in": plain(result),
        },
    )


def append_once(path: Path, fields: list[str], row: dict[str, str], apply: bool) -> str:
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != fields:
                raise ValueError(f"unexpected CSV header in {path}")
            for existing in reader:
                if existing["measured_at_local"][:10] != row["measured_at_local"][:10]:
                    continue
                if "body_site" in row and (
                    existing["body_site"] != row["body_site"]
                    or existing["side"] != row["side"]
                ):
                    continue
                if existing == row:
                    return "unchanged"
                site = row.get("body_site", "waist")
                raise ValueError(
                    f"different {site} reading already exists on this date"
                )
    if not apply:
        return "ready"
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    return "inserted"


def main(argv: list[str] | None = None) -> int:
    try:
        from health_buddy.legacy_entrypoints import delegate_logger
    except ImportError:
        print("Install Health Buddy and use an explicit --workspace; "
              "loose-file logging is retired.", file=sys.stderr)
        return 2
    return delegate_logger('circumference', argv)


if __name__ == "__main__":
    raise SystemExit(main())

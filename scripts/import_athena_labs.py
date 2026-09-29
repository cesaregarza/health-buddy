#!/usr/bin/env python3
"""Import Athena lab results from a validated workbook extraction or raw CSV."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABS_FILE = REPO_ROOT / "data" / "labs.csv"
DEFAULT_SNAPSHOT_FILE = REPO_ROOT / "data" / "imports" / "athena-lab-results.csv"

SOURCE_HEADERS = (
    "Collection Date",
    "Panel",
    "Ordered By",
    "Out-of-Range Panel",
    "Detail #",
    "Analyte",
    "Portal Status",
    "Reported Result",
    "Numeric Value",
    "Unit",
    "Reference Range",
    "Analyte Note",
    "Exact Portal Text",
)
SOURCE_CSV_HEADERS = (*SOURCE_HEADERS, "Source")
LAB_HEADERS = (
    "collected_on",
    "test_name",
    "value",
    "unit",
    "reference_low",
    "reference_high",
    "flag",
    "fasting",
    "source",
    "notes",
)
SIMPLE_RANGE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$"
)
ATHENA_KEY = re.compile(r"(?:^|; )athena_key=([^;]+)")


class ImportError(ValueError):
    """Raised when the extraction cannot be imported without guessing."""


def collection_date_to_iso(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ImportError(f"invalid ISO collection date: {value!r}") from exc
        if parsed.isoformat() != value:
            raise ImportError(f"collection date is not canonical ISO format: {value!r}")
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ImportError(f"invalid Excel collection date: {value!r}")
    if int(value) != value:
        raise ImportError(f"collection date unexpectedly includes a time: {value!r}")
    return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def slug(value: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not compact:
        raise ImportError("panel name cannot produce an Athena source key")
    return compact


def source_key(row: dict[str, Any], collected_on: str) -> str:
    detail = text(row["Detail #"])
    if not detail:
        raise ImportError("Athena row has no detail number")
    return f"{collected_on}:{slug(text(row['Panel']))}:{detail}"


def parse_simple_range(value: Any) -> tuple[str, str]:
    match = SIMPLE_RANGE.fullmatch(text(value))
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def load_extraction(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportError(f"could not read artifact-tool extraction: {exc}") from exc

    for sheet_name in ("Summary", "Lab Results", "QA"):
        if sheet_name not in payload or "values" not in payload[sheet_name]:
            raise ImportError(f"extraction is missing the {sheet_name!r} worksheet")

    qa_values = payload["QA"]["values"]
    try:
        qa_header_index = next(
            index for index, row in enumerate(qa_values) if row[:4] == [
                "Check",
                "Expected",
                "Workbook Value",
                "Status",
            ]
        )
    except StopIteration as exc:
        raise ImportError("Athena workbook QA header is missing") from exc
    qa_checks = []
    for row in qa_values[qa_header_index + 1 :]:
        if not row or row[0] in (None, ""):
            break
        qa_checks.append(row)
    if not qa_checks:
        raise ImportError("Athena workbook contains no QA checks")
    qa_failures = [row for row in qa_checks if len(row) < 4 or row[3] != "PASS"]
    if qa_failures:
        raise ImportError(f"Athena workbook QA did not pass: {qa_failures!r}")
    return payload


def source_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload["Lab Results"]["values"]
    if not values or tuple(values[0]) != SOURCE_HEADERS:
        raise ImportError("Lab Results headers do not match the expected Athena export")
    rows = [
        dict(zip(SOURCE_HEADERS, row, strict=True))
        for row in values[1:]
        if any(value not in (None, "") for value in row)
    ]

    summary_metrics = {
        row[0]: row[1]
        for row in payload["Summary"]["values"]
        if len(row) >= 2 and row[0] in {"Panels", "Analytes"}
    }
    if summary_metrics.get("Analytes") != len(rows):
        raise ImportError(
            "analyte count does not match workbook summary: "
            f"{len(rows)} != {summary_metrics.get('Analytes')!r}"
        )

    validate_source_rows(rows)
    return rows


def validate_source_rows(rows: Sequence[dict[str, Any]]) -> None:
    identities = [
        (
            row["Collection Date"],
            row["Panel"],
            row["Detail #"],
            row["Analyte"],
        )
        for row in rows
    ]
    if len(set(identities)) != len(identities):
        raise ImportError("duplicate Athena source identities found")
    if any(not text(row["Reported Result"]) for row in rows):
        raise ImportError("Athena extraction contains a blank reported result")
    if any(not text(row["Exact Portal Text"]) for row in rows):
        raise ImportError("Athena extraction contains blank exact portal text")


def source_rows_from_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            if headers not in {SOURCE_HEADERS, SOURCE_CSV_HEADERS}:
                raise ImportError(
                    "Athena CSV headers do not match the expected raw export"
                )
            rows = [dict(row) for row in reader]
    except OSError as exc:
        raise ImportError(f"could not read Athena CSV: {exc}") from exc

    if not rows:
        raise ImportError("Athena CSV contains no analyte rows")
    validate_source_rows(rows)
    return rows


def snapshot_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def normalized_lab(
    row: dict[str, Any], *, source_row_number: int, snapshot: str
) -> dict[str, str]:
    collected_on = collection_date_to_iso(row["Collection Date"])
    key = source_key(row, collected_on)
    low, high = parse_simple_range(row["Reference Range"])
    numeric = row["Numeric Value"]
    result_value = (
        text(numeric) if numeric is not None else text(row["Reported Result"])
    )
    notes = "; ".join(
        (
            f"athena_key={key}",
            f"panel={text(row['Panel'])}",
            f"detail={text(row['Detail #'])}",
            f"reported_result={text(row['Reported Result'])}",
            f"reference_range={text(row['Reference Range'])}",
            f"portal_status={text(row['Portal Status'])}",
            f"source_snapshot={snapshot}#row={source_row_number}",
        )
    )
    return {
        "collected_on": collected_on,
        "test_name": text(row["Analyte"]),
        "value": result_value,
        "unit": text(row["Unit"]),
        "reference_low": low,
        "reference_high": high,
        "flag": text(row["Portal Status"]),
        "fasting": "",
        "source": "athena_portal_export",
        "notes": notes,
    }


def write_csv(
    path: Path, headers: Sequence[str], rows: Sequence[dict[str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_existing_labs(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LAB_HEADERS:
            raise ImportError(f"{path} does not have the canonical labs.csv header")
        return [dict(row) for row in reader]


def import_labs(
    *,
    source_json: Path | None,
    source_csv: Path | None,
    labs_file: Path,
    snapshot_file: Path | None,
    fasting_dates: frozenset[str] = frozenset(),
) -> dict[str, int | str]:
    if (source_json is None) == (source_csv is None):
        raise ImportError("provide exactly one Athena source: JSON or CSV")

    if source_json is not None:
        payload = load_extraction(source_json)
        rows = source_rows(payload)
        effective_snapshot = snapshot_file or DEFAULT_SNAPSHOT_FILE
        snapshot = snapshot_label(effective_snapshot)
    else:
        assert source_csv is not None
        rows = source_rows_from_csv(source_csv)
        effective_snapshot = source_csv
        snapshot = snapshot_label(source_csv)

    snapshot_rows = []
    normalized_rows = []
    for source_row_number, row in enumerate(rows, start=2):
        snapshot_row = {header: text(row[header]) for header in SOURCE_HEADERS}
        snapshot_row["Collection Date"] = collection_date_to_iso(
            row["Collection Date"]
        )
        snapshot_rows.append(snapshot_row)
        normalized_rows.append(
            normalized_lab(
                row,
                source_row_number=source_row_number,
                snapshot=snapshot,
            )
        )

    existing = read_existing_labs(labs_file)
    fasting_updated = 0
    for row in existing:
        if row["collected_on"] in fasting_dates and row["fasting"] != "yes":
            row["fasting"] = "yes"
            fasting_updated += 1
    for row in normalized_rows:
        if row["collected_on"] in fasting_dates:
            row["fasting"] = "yes"
    existing_by_key: dict[str, dict[str, str]] = {}
    for row in existing:
        match = ATHENA_KEY.search(row["notes"])
        if match:
            key = match.group(1)
            if key in existing_by_key:
                raise ImportError(f"duplicate Athena key already in labs.csv: {key}")
            existing_by_key[key] = row

    inserted = 0
    unchanged = 0
    merged = list(existing)
    comparison_fields = tuple(header for header in LAB_HEADERS if header != "notes")
    for row in normalized_rows:
        key_match = ATHENA_KEY.search(row["notes"])
        assert key_match is not None
        key = key_match.group(1)
        prior = existing_by_key.get(key)
        if prior is None:
            merged.append(row)
            existing_by_key[key] = row
            inserted += 1
            if row["collected_on"] in fasting_dates:
                fasting_updated += 1
            continue
        if all(prior[field] == row[field] for field in comparison_fields):
            unchanged += 1
            continue
        raise ImportError(f"conflicting Athena result already exists for {key}")

    if source_json is not None:
        write_csv(effective_snapshot, SOURCE_HEADERS, snapshot_rows)
    write_csv(labs_file, LAB_HEADERS, merged)
    return {
        "source_rows": len(rows),
        "inserted": inserted,
        "unchanged": unchanged,
        "fasting_updated": fasting_updated,
        "labs_total": len(merged),
        "snapshot": snapshot,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Athena lab results from artifact-tool JSON or raw CSV."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-json", type=Path)
    source.add_argument("--source-csv", type=Path)
    parser.add_argument("--labs-file", type=Path, default=DEFAULT_LABS_FILE)
    parser.add_argument("--snapshot-file", type=Path)
    parser.add_argument(
        "--fasting-date",
        action="append",
        default=[],
        metavar="YYYY-MM-DD",
        help="mark every analyte collected on this confirmed fasting date as yes",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = import_labs(
            source_json=args.source_json,
            source_csv=args.source_csv,
            labs_file=args.labs_file,
            snapshot_file=args.snapshot_file,
            fasting_dates=frozenset(args.fasting_date),
        )
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

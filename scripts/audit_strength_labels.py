#!/usr/bin/env python3
"""Report exercise/equipment/load-basis variants and duplicate set ordinals.

This is a review aid, not an instruction to merge labels: different machines
and per-hand versus total-stack loads must remain distinct unless verified.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

try:
    from scripts.strength_identity import (
        EXERCISE_NAMES,
        comparable_identity,
        exercise_key,
    )
except ModuleNotFoundError:  # direct scripts/audit_strength_labels.py execution
    from strength_identity import EXERCISE_NAMES, comparable_identity, exercise_key


REQUIRED = {
    "session_id",
    "session_date",
    "exercise",
    "equipment",
    "set_number",
    "load_basis",
    "status",
    "notes",
}


def audit(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            missing = sorted(REQUIRED - set(reader.fieldnames or []))
            raise ValueError(f"sets CSV missing required columns: {missing}")
        variants: dict[str, dict[tuple[str, str, str], list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        ordinals: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
        untracked_loaded_exercises: set[str] = set()
        evidence_flags: list[dict[str, str | int]] = []
        rows = 0
        for row in reader:
            rows += 1
            key = exercise_key(row["exercise"])
            if not key or not row["session_id"] or not row["session_date"]:
                raise ValueError(
                    f"invalid exercise/session identity at CSV row {rows + 1}"
                )
            variant = (
                row["exercise"].strip(),
                row["equipment"].strip(),
                row["load_basis"].strip(),
            )
            variants[key][variant].append(row["session_date"])
            if row["load_basis"].strip() != "bodyweight" and key not in EXERCISE_NAMES:
                untracked_loaded_exercises.add(key)
            reasons = []
            if row["status"].strip() == "reported_aggregate":
                reasons.append("reported_aggregate")
            if "inferred" in row["notes"].casefold():
                reasons.append("inferred_value_note")
            if reasons:
                evidence_flags.append(
                    {
                        "csv_row": rows + 1,
                        "session_id": row["session_id"],
                        "exercise": key,
                        "reason": ",".join(reasons),
                    }
                )
            ordinal = row["set_number"].strip()
            if ordinal and row["status"] == "completed":
                identity = comparable_identity(
                    row["exercise"], row["equipment"], row["load_basis"]
                )
                ordinals[(row["session_id"], *identity, ordinal)] += 1
    return {
        "rows": rows,
        "untracked_loaded_exercises": sorted(untracked_loaded_exercises),
        "evidence_flags": evidence_flags,
        "exercises": [
            {
                "key": key,
                "variants": [
                    {
                        "exercise": exercise,
                        "equipment": equipment,
                        "load_basis": basis,
                        "rows": len(dates),
                        "first_date": min(dates),
                        "last_date": max(dates),
                    }
                    for (exercise, equipment, basis), dates in sorted(group.items())
                ],
            }
            for key, group in sorted(variants.items())
            if len(group) > 1
        ],
        "duplicate_completed_ordinals": [
            {
                "session_id": sid,
                "exercise": key,
                "equipment": equipment,
                "load_basis": basis,
                "set_number": ordinal,
                "rows": count,
            }
            for (sid, key, equipment, basis, ordinal), count in sorted(ordinals.items())
            if count > 1
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sets-file", type=Path, required=True, help="Canonical or fixture sets.csv"
    )
    args = parser.parse_args()
    try:
        result = audit(args.sets_file)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"strength label audit: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

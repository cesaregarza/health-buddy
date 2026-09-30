#!/usr/bin/env python3
"""Summarize workout attendance, work capacity, and repeated lifts."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import mean, median
from typing import Any

try:
    from scripts.set_classification import is_nonworking_set
    from scripts.strength_identity import (
        EXERCISE_NAMES,
        comparable_identity,
        comparison_is_unverified,
        equipment_label,
    )
except ModuleNotFoundError:  # direct scripts/training_progress.py execution
    from set_classification import is_nonworking_set
    from strength_identity import (
        EXERCISE_NAMES,
        comparable_identity,
        comparison_is_unverified,
        equipment_label,
    )

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


class DataError(ValueError):
    """Raised when workout data cannot support a trustworthy summary."""


@dataclass(frozen=True)
class Session:
    session_id: str
    session_date: date
    workout_type: str
    status: str


@dataclass(frozen=True)
class StrengthSet:
    session_id: str
    session_date: date
    exercise: str
    equipment: str
    set_count: int
    load_lb: float | None
    load_basis: str
    reps: int
    rir: int | None
    status: str
    notes: str


def require_headers(path: Path, headers: Iterable[str], actual: Sequence[str]) -> None:
    missing = sorted(set(headers) - set(actual))
    if missing:
        raise DataError(f"{path}: missing required columns: {', '.join(missing)}")


def load_sessions(path: Path) -> list[Session]:
    sessions: list[Session] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = ("session_id", "date", "workout_type", "status")
        require_headers(path, required, reader.fieldnames or [])
        for line_number, row in enumerate(reader, start=2):
            try:
                session_date = date.fromisoformat(row["date"])
            except (TypeError, ValueError) as exc:
                raise DataError(f"{path}:{line_number}: invalid date") from exc
            sessions.append(
                Session(
                    session_id=row["session_id"].strip(),
                    session_date=session_date,
                    workout_type=row["workout_type"].strip(),
                    status=row["status"].strip(),
                )
            )
    return sessions


def load_sets(path: Path) -> list[StrengthSet]:
    strength_sets: list[StrengthSet] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = (
            "session_id",
            "session_date",
            "exercise",
            "equipment",
            "set_count",
            "load_lb",
            "load_basis",
            "reps",
            "rir",
            "status",
            "notes",
        )
        require_headers(path, required, reader.fieldnames or [])
        for line_number, row in enumerate(reader, start=2):
            try:
                session_date = date.fromisoformat(row["session_date"])
                set_count = int(row["set_count"]) if row["set_count"] else 1
                load_lb = float(row["load_lb"]) if row["load_lb"] else None
                reps = int(row["reps"])
                rir = int(row["rir"]) if row["rir"] else None
            except (TypeError, ValueError) as exc:
                raise DataError(f"{path}:{line_number}: invalid strength set") from exc
            if set_count < 1 or reps < 0 or (load_lb is not None and load_lb < 0):
                raise DataError(f"{path}:{line_number}: negative or empty set value")
            if rir is not None and not 0 <= rir <= 10:
                raise DataError(f"{path}:{line_number}: RIR must be between 0 and 10")
            strength_sets.append(
                StrengthSet(
                    session_id=row["session_id"].strip(),
                    session_date=session_date,
                    exercise=row["exercise"].strip(),
                    equipment=row["equipment"].strip(),
                    set_count=set_count,
                    load_lb=load_lb,
                    load_basis=row["load_basis"].strip(),
                    reps=reps,
                    rir=rir,
                    status=row["status"].strip(),
                    notes=row["notes"].strip(),
                )
            )
    return strength_sets


def load_cardio_session_ids(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        require_headers(path, ("session_id",), reader.fieldnames or [])
        return {
            row["session_id"].strip() for row in reader if row["session_id"].strip()
        }


def is_working_set(strength_set: StrengthSet) -> bool:
    """Return whether a logged row represents working resistance volume."""

    if strength_set.status == "reported_aggregate":
        return True
    return strength_set.status == "completed" and not is_nonworking_set(
        strength_set.status, strength_set.notes
    )


def summarize_set_group(rows: list[StrengthSet]) -> dict[str, Any]:
    loads = [row.load_lb for row in rows if row.load_lb is not None]
    top_load = max(loads) if loads else None
    top_rows = [row for row in rows if row.load_lb == top_load]
    rir_values = [row.rir for row in rows if row.rir is not None]
    return {
        "working_sets": sum(row.set_count for row in rows),
        "total_reps": sum(row.reps * row.set_count for row in rows),
        "top_load_lb": top_load,
        "reps_at_top_load": max((row.reps for row in top_rows), default=None),
        "reported_rir_mean": mean(rir_values) if rir_values else None,
        "reported_rir_min": min(rir_values) if rir_values else None,
        "rir_reported_sets": sum(row.set_count for row in rows if row.rir is not None),
        "reported_aggregate": any(row.status == "reported_aggregate" for row in rows),
    }


def build_summary(
    repo_root: Path,
    *,
    as_of: date | None = None,
    lookback_days: int = 28,
    equipment_aliases: dict[str, dict[str, str]] | None = None,
    unverified_comparisons: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, Any]:
    if lookback_days < 1:
        raise DataError("lookback_days must be positive")

    data_dir = repo_root / "data"
    sessions = load_sessions(data_dir / "sessions.csv")
    strength_sets = load_sets(data_dir / "sets.csv")
    cardio_session_ids = load_cardio_session_ids(data_dir / "cardio.csv")

    if as_of is None:
        completed_dates = [
            session.session_date for session in sessions if session.status == "complete"
        ]
        if not completed_dates:
            raise DataError("sessions.csv: no completed sessions")
        as_of = max(completed_dates)
    window_start = as_of - timedelta(days=lookback_days - 1)

    completed = [
        session
        for session in sessions
        if session.status == "complete"
        and window_start <= session.session_date <= as_of
    ]
    completed_ids = {session.session_id for session in completed}
    working_sets = [
        row
        for row in strength_sets
        if row.session_id in completed_ids
        and window_start <= row.session_date <= as_of
        and is_working_set(row)
    ]

    sets_by_session: dict[str, list[StrengthSet]] = defaultdict(list)
    for row in working_sets:
        sets_by_session[row.session_id].append(row)

    session_summaries: list[dict[str, Any]] = []
    for session in sorted(
        completed, key=lambda item: (item.session_date, item.session_id)
    ):
        rows = sets_by_session.get(session.session_id, [])
        working_count = sum(row.set_count for row in rows)
        rir_count = sum(row.set_count for row in rows if row.rir is not None)
        session_summaries.append(
            {
                "session_id": session.session_id,
                "date": session.session_date.isoformat(),
                "workout_type": session.workout_type,
                "working_sets": working_count,
                "rir_coverage_percent": (
                    100.0 * rir_count / working_count if working_count else None
                ),
                "has_cardio": session.session_id in cardio_session_ids,
            }
        )

    resistance_session_ids = set(sets_by_session)
    resistance_counts = [
        summary["working_sets"]
        for summary in session_summaries
        if summary["session_id"] in resistance_session_ids
    ]
    training_dates = sorted({session.session_date for session in completed})
    gaps = [(right - left).days for left, right in pairwise(training_dates)]
    latest_resistance = next(
        (
            summary
            for summary in reversed(session_summaries)
            if summary["session_id"] in resistance_session_ids
        ),
        None,
    )

    groups: dict[tuple[str, str, str], list[StrengthSet]] = defaultdict(list)
    for row in working_sets:
        # A bodyweight row without a load cannot support a top-load comparison.
        # It still counts toward session attendance and working-set capacity.
        if row.load_lb is None:
            continue
        groups[comparable_identity(row.exercise, row.equipment, row.load_basis, equipment_aliases=equipment_aliases)].append(
            row
        )

    exercise_progress: list[dict[str, Any]] = []
    for (exercise_id, equipment, load_basis), rows in sorted(groups.items()):
        by_session: dict[tuple[date, str], list[StrengthSet]] = defaultdict(list)
        for row in rows:
            by_session[(row.session_date, row.session_id)].append(row)
        ordered = sorted(by_session.items())
        first_key, first_rows = ordered[0]
        latest_key, latest_rows = ordered[-1]
        first = summarize_set_group(first_rows)
        latest = summarize_set_group(latest_rows)
        first_load = first["top_load_lb"]
        latest_load = latest["top_load_lb"]
        load_change = (
            latest_load - first_load
            if first_load is not None
            and latest_load is not None
            and not comparison_is_unverified(exercise_id, equipment, unverified_comparisons)
            else None
        )
        exercise_progress.append(
            {
                "exercise": EXERCISE_NAMES.get(
                    exercise_id, exercise_id.replace("_", " ").title()
                ),
                "exercise_id": exercise_id,
                "equipment": equipment,
                "comparison_unverified": comparison_is_unverified(exercise_id, equipment, unverified_comparisons),
                "load_basis": load_basis,
                "sessions_observed": len(ordered),
                "first": {
                    "date": first_key[0].isoformat(),
                    "session_id": first_key[1],
                    **first,
                },
                "latest": {
                    "date": latest_key[0].isoformat(),
                    "session_id": latest_key[1],
                    **latest,
                },
                "top_load_change_lb": load_change,
            }
        )

    recent_start = as_of - timedelta(days=6)
    prior_start = recent_start - timedelta(days=7)
    recent_sessions = sum(
        recent_start <= session.session_date <= as_of for session in completed
    )
    prior_sessions = sum(
        prior_start <= session.session_date < recent_start for session in completed
    )

    return {
        "as_of": as_of.isoformat(),
        "window": {
            "start": window_start.isoformat(),
            "end": as_of.isoformat(),
            "days": lookback_days,
        },
        "attendance": {
            "complete_sessions": len(completed),
            "training_days": len(training_dates),
            "resistance_sessions": len(resistance_session_ids),
            "cardio_including_sessions": sum(
                session.session_id in cardio_session_ids for session in completed
            ),
            "sessions_per_week": len(completed) * 7.0 / lookback_days,
            "median_gap_days": median(gaps) if gaps else None,
            "recent_7_day_sessions": recent_sessions,
            "prior_7_day_sessions": prior_sessions,
        },
        "work_capacity": {
            "latest_resistance_session": latest_resistance,
            "median_working_sets_per_resistance_session": (
                median(resistance_counts) if resistance_counts else None
            ),
            "max_working_sets_in_session": (
                max(resistance_counts) if resistance_counts else None
            ),
        },
        "session_summaries": session_summaries,
        "exercise_progress": exercise_progress,
    }


def render_text(summary: dict[str, Any]) -> str:
    attendance = summary["attendance"]
    capacity = summary["work_capacity"]
    lines = [
        f"Training progress through {summary['as_of']}",
        (
            f"Attendance: {attendance['complete_sessions']} sessions on "
            f"{attendance['training_days']} days "
            f"({attendance['sessions_per_week']:.2f}/week)"
        ),
        (
            f"Resistance: {attendance['resistance_sessions']} sessions; "
            f"latest {capacity['latest_resistance_session']['working_sets']} "
            f"working sets; max {capacity['max_working_sets_in_session']}"
            if capacity["latest_resistance_session"]
            else "Resistance: no completed sessions in window"
        ),
        "Repeated exercises:",
    ]
    for item in summary["exercise_progress"]:
        first = item["first"]
        latest = item["latest"]
        caveats = []
        if item["comparison_unverified"]:
            caveats.append("machine identity/load scale unverified")
        if first["reported_aggregate"] or latest["reported_aggregate"]:
            caveats.append("reported aggregate; individual sets unconfirmed")
        caveat = f" [{'; '.join(caveats)}]" if caveats else ""
        name = equipment_label(item["exercise_id"], item["equipment"])
        identity_label = f"{item['exercise']} ({name}, "
        lines.append(
            "- "
            f"{identity_label}{item['load_basis']}): "
            f"{first['top_load_lb']} lb x {first['reps_at_top_load']} "
            f"on {first['date']} -> "
            f"{latest['top_load_lb']} lb x {latest['reps_at_top_load']} "
            f"on {latest['date']}{caveat}"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize workout attendance, capacity, and repeated lifts."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="health repository root",
    )
    parser.add_argument(
        "--as-of", type=date.fromisoformat, help="ignore sessions after YYYY-MM-DD"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=28,
        help="inclusive analysis window (default: 28)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build_summary(
            args.repo_root,
            as_of=args.as_of,
            lookback_days=args.lookback_days,
        )
    except (DataError, OSError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

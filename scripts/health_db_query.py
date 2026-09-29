#!/usr/bin/env python3
"""Read common Apple Health views from a health-ingest SQLite database."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DATABASE = Path(".local/data/healthkit.db")
RECORD_KINDS = ("quantity", "category", "dailyAggregate", "workout")
OUTPUT_FORMATS = ("table", "json", "csv")
SLEEP_TYPE_IDENTIFIER = "HKCategoryTypeIdentifierSleepAnalysis"
SLEEP_STAGE_VALUES = {
    "asleepCore": "core",
    "asleepDeep": "deep",
    "asleepREM": "rem",
    "asleepUnspecified": "unspecified",
}
BODY_TYPE_IDENTIFIERS = (
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierBodyFatPercentage",
    "HKQuantityTypeIdentifierBodyMassIndex",
    "HKQuantityTypeIdentifierLeanBodyMass",
)
DAILY_ACTIVITY_NAMES = {
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_energy_kcal",
    "HKQuantityTypeIdentifierAppleExerciseTime": "exercise_minutes",
    "HKQuantityTypeIdentifierAppleStandTime": "stand_minutes",
    "HKQuantityTypeIdentifierBasalEnergyBurned": "basal_energy_kcal",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "walking_running_meters",
    "HKQuantityTypeIdentifierFlightsClimbed": "flights_climbed",
    "HKQuantityTypeIdentifierStepCount": "steps",
}
WORKOUT_ACTIVITY_NAMES = {
    "37": "running",
    "44": "stair_climbing",
    "50": "traditional_strength_training",
    "52": "walking",
    "79": "pickleball",
}


class QueryError(RuntimeError):
    """Raised when a read-only health database query cannot be completed."""


def _database_path(value: str | None) -> Path:
    configured = value or os.environ.get("HEALTH_INGEST_SQLITE_PATH")
    return Path(configured) if configured else DEFAULT_DATABASE


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise argparse.ArgumentTypeError(f"unknown IANA timezone: {value}") from exc
    return value


def _connect_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = database_path.expanduser().resolve()
    try:
        is_file = resolved.is_file()
    except OSError as exc:
        raise QueryError(f"unable to access database: {resolved}: {exc}") from exc
    if not is_file:
        raise QueryError(f"database does not exist: {resolved}")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise QueryError(f"unable to open database read-only: {resolved}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != 1:
        connection.close()
        raise QueryError(f"unsupported health-ingest database version: {version}")
    return connection


def _json_value(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise QueryError("database contains malformed JSON") from exc


def _source_name(value: str | None) -> str | None:
    source = _json_value(value)
    return source.get("name") if isinstance(source, dict) else None


def _minutes(value: Any) -> float | None:
    if isinstance(value, int | float):
        return round(float(value) / 60, 2)
    return None


def _distance_miles(value: Any, unit: Any) -> float | None:
    if not isinstance(value, int | float) or not isinstance(unit, str):
        return None
    conversions = {
        "mi": 1.0,
        "mile": 1.0,
        "miles": 1.0,
        "km": 0.6213711922,
        "m": 0.0006213711922,
    }
    factor = conversions.get(unit.lower())
    return round(float(value) * factor, 3) if factor is not None else None


def _pace_minutes_per_mile(duration_seconds: Any, distance_miles: Any) -> float | None:
    if (
        not isinstance(duration_seconds, int | float)
        or not isinstance(distance_miles, int | float)
        or distance_miles <= 0
    ):
        return None
    return round(float(duration_seconds) / 60 / float(distance_miles), 2)


def _instant(value: str, default_zone: ZoneInfo) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise QueryError(f"database contains an invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_zone)
    return parsed.astimezone(UTC)


def _local_iso(value: datetime, zone: ZoneInfo) -> str:
    return value.astimezone(zone).isoformat(timespec="seconds")


def _night_window(selected_date: str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    wake_date = date.fromisoformat(selected_date)
    end_local = datetime.combine(wake_date, time(18), tzinfo=zone)
    start_local = end_local - timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _merge_intervals(
    intervals: list[tuple[datetime, datetime]], *, adjacency_seconds: float = 2
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    merged: list[tuple[datetime, datetime]] = []
    tolerance = timedelta(seconds=adjacency_seconds)
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1] + tolerance:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _interval_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    return sum((end - start).total_seconds() for start, end in intervals)


def _minutes_from_seconds(value: float) -> float:
    return round(value / 60, 2)


def _mass_to_lb(value: Any, unit: Any) -> float | None:
    if not isinstance(value, int | float) or not isinstance(unit, str):
        return None
    factors = {"kg": 2.2046226218, "g": 0.0022046226218, "lb": 1.0}
    factor = factors.get(unit.lower())
    return round(float(value) * factor, 1) if factor is not None else None


def _heart_rate_summary(
    connection: sqlite3.Connection, start_at: str, end_at: str
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT value_json
        FROM records
        WHERE deleted_at IS NULL
          AND record_kind = 'quantity'
          AND type_identifier = 'HKQuantityTypeIdentifierHeartRate'
          AND start_at >= ? AND start_at <= ?
        ORDER BY start_at
        """,
        (start_at, end_at),
    ).fetchall()
    values = [
        float(value)
        for row in rows
        if isinstance((value := _json_value(row["value_json"])), int | float)
    ]
    if not values:
        return {
            "heart_rate_samples": 0,
            "avg_heart_rate_bpm": None,
            "min_heart_rate_bpm": None,
            "max_heart_rate_bpm": None,
        }
    return {
        "heart_rate_samples": len(values),
        "avg_heart_rate_bpm": round(sum(values) / len(values), 1),
        "min_heart_rate_bpm": round(min(values), 1),
        "max_heart_rate_bpm": round(max(values), 1),
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    """Return a linearly interpolated percentile for already collected values."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 1)


def _heart_rate_samples(
    connection: sqlite3.Connection, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    rows = connection.execute(
        """
        SELECT start_at, value_json
        FROM records
        WHERE deleted_at IS NULL
          AND record_kind = 'quantity'
          AND type_identifier = 'HKQuantityTypeIdentifierHeartRate'
          AND julianday(start_at) >= julianday(?)
          AND julianday(start_at) <= julianday(?)
        ORDER BY julianday(start_at)
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    samples = []
    for row in rows:
        value = _json_value(row["value_json"])
        if isinstance(value, int | float):
            samples.append((_instant(row["start_at"], ZoneInfo("UTC")), float(value)))
    return samples


def _window_average(
    samples: list[tuple[datetime, float]], start: datetime, end: datetime
) -> float | None:
    values = [value for timestamp, value in samples if start < timestamp <= end]
    return round(sum(values) / len(values), 1) if values else None


def _difference(start: float | None, end: float | None) -> float | None:
    return round(start - end, 1) if start is not None and end is not None else None


def query_workout_analysis(
    connection: sqlite3.Connection,
    *,
    since: str | None,
    activity_type: str | None,
    start_at: str | None,
    recovery_minutes: int,
    timezone_name: str,
) -> list[dict[str, Any]]:
    """Summarize intensity and immediate recovery for one selected workout."""
    parameters: list[Any] = []
    filters = ["deleted_at IS NULL", "record_kind = 'workout'"]
    if start_at:
        filters.append("start_at = ?")
        parameters.append(start_at)
    elif since:
        filters.append("substr(start_at, 1, 10) >= ?")
        parameters.append(since)
    rows = connection.execute(
        f"""
        SELECT start_at, end_at, workout_json, source_json, received_at
        FROM records
        WHERE {' AND '.join(filters)}
        ORDER BY julianday(start_at) DESC
        """,
        parameters,
    ).fetchall()

    selected: sqlite3.Row | None = None
    workout: dict[str, Any] | None = None
    for row in rows:
        candidate = _json_value(row["workout_json"])
        if not isinstance(candidate, dict):
            continue
        if activity_type and str(candidate.get("activityType")) != activity_type:
            continue
        selected = row
        workout = candidate
        break
    if selected is None or workout is None:
        raise QueryError("no matching workout was found")

    zone = ZoneInfo(timezone_name)
    start = _instant(selected["start_at"], zone)
    end = _instant(selected["end_at"], zone)
    elapsed_seconds = max((end - start).total_seconds(), 0.0)
    active_seconds_value = workout.get("durationSeconds")
    active_seconds = (
        float(active_seconds_value)
        if isinstance(active_seconds_value, int | float)
        else elapsed_seconds
    )
    workout_samples = _heart_rate_samples(connection, start, end)
    recovery_end = end + timedelta(minutes=recovery_minutes)
    recovery_samples = _heart_rate_samples(connection, end, recovery_end)
    values = [value for _, value in workout_samples]
    sample_intervals = [
        (current[0] - previous[0]).total_seconds()
        for previous, current in zip(workout_samples, workout_samples[1:])
        if current[0] > previous[0]
    ]

    last_minute = _window_average(workout_samples, end - timedelta(minutes=1), end)
    post_1_min = _window_average(recovery_samples, end, end + timedelta(minutes=1))
    post_3_min = _window_average(
        recovery_samples, end + timedelta(minutes=2), end + timedelta(minutes=3)
    )
    post_5_min = _window_average(
        recovery_samples, end + timedelta(minutes=4), end + timedelta(minutes=5)
    )

    def band_percentage(lower: float | None, upper: float | None) -> float | None:
        if not values:
            return None
        count = sum(
            1
            for value in values
            if (lower is None or value >= lower) and (upper is None or value < upper)
        )
        return round(100 * count / len(values), 1)

    energy = workout.get("totalEnergyValue")
    energy_kcal = float(energy) if isinstance(energy, int | float) else None
    activity = str(workout.get("activityType"))
    return [
        {
            "start_local": _local_iso(start, zone),
            "end_local": _local_iso(end, zone),
            "activity_type": activity,
            "activity_name": WORKOUT_ACTIVITY_NAMES.get(activity),
            "elapsed_minutes": round(elapsed_seconds / 60, 2),
            "active_minutes": round(active_seconds / 60, 2),
            "paused_minutes": round(max(elapsed_seconds - active_seconds, 0) / 60, 2),
            "active_fraction_percent": (
                round(100 * active_seconds / elapsed_seconds, 1)
                if elapsed_seconds > 0
                else None
            ),
            "energy_kcal": round(energy_kcal, 1) if energy_kcal is not None else None,
            "energy_kcal_per_active_minute": (
                round(energy_kcal / (active_seconds / 60), 2)
                if energy_kcal is not None and active_seconds > 0
                else None
            ),
            "heart_rate_samples": len(values),
            "median_sample_interval_seconds": _percentile(sample_intervals, 0.5),
            "avg_heart_rate_bpm": round(sum(values) / len(values), 1) if values else None,
            "min_heart_rate_bpm": round(min(values), 1) if values else None,
            "p25_heart_rate_bpm": _percentile(values, 0.25),
            "median_heart_rate_bpm": _percentile(values, 0.5),
            "p75_heart_rate_bpm": _percentile(values, 0.75),
            "p90_heart_rate_bpm": _percentile(values, 0.9),
            "max_heart_rate_bpm": round(max(values), 1) if values else None,
            "samples_under_100_bpm_percent": band_percentage(None, 100),
            "samples_100_to_109_bpm_percent": band_percentage(100, 110),
            "samples_110_to_119_bpm_percent": band_percentage(110, 120),
            "samples_120_to_129_bpm_percent": band_percentage(120, 130),
            "samples_130_plus_bpm_percent": band_percentage(130, None),
            "last_workout_minute_avg_bpm": last_minute,
            "post_1_minute_avg_bpm": post_1_min,
            "post_3_minute_avg_bpm": post_3_min,
            "post_5_minute_avg_bpm": post_5_min,
            "drop_to_post_1_minute_bpm": _difference(last_minute, post_1_min),
            "drop_to_post_3_minutes_bpm": _difference(last_minute, post_3_min),
            "drop_to_post_5_minutes_bpm": _difference(last_minute, post_5_min),
            "source_name": _source_name(selected["source_json"]),
            "received_at": selected["received_at"],
        }
    ]


def query_overview(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    row = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM devices WHERE revoked_at IS NULL) AS active_devices,
            (SELECT COUNT(*) FROM batches) AS batches,
            (SELECT COUNT(*) FROM records WHERE deleted_at IS NULL) AS active_records,
            (SELECT COUNT(*)
             FROM records
             WHERE deleted_at IS NOT NULL) AS deleted_records,
            (SELECT COUNT(*) FROM tombstones) AS tombstones,
            (SELECT MAX(received_at) FROM batches) AS latest_batch_received_at,
            (SELECT MAX(start_at)
             FROM records
             WHERE deleted_at IS NULL) AS latest_record_start_at
        """
    ).fetchone()
    assert row is not None
    metrics = [{"metric": key, "value": row[key]} for key in row.keys()]
    kind_rows = connection.execute(
        """
        SELECT record_kind, COUNT(*) AS count
        FROM records
        WHERE deleted_at IS NULL
        GROUP BY record_kind
        ORDER BY record_kind
        """
    ).fetchall()
    metrics.extend(
        {
            "metric": f"active_{kind_row['record_kind']}_records",
            "value": kind_row["count"],
        }
        for kind_row in kind_rows
    )
    return metrics


def query_batches(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT generated_at, received_at, record_count, deletion_count
        FROM batches
        ORDER BY received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def query_types(
    connection: sqlite3.Connection, kind: str | None
) -> list[dict[str, Any]]:
    clause = "AND record_kind = ?" if kind else ""
    parameters: tuple[Any, ...] = (kind,) if kind else ()
    rows = connection.execute(
        f"""
        SELECT record_kind, type_identifier, COUNT(*) AS record_count,
               MIN(start_at) AS first_start_at, MAX(start_at) AS latest_start_at
        FROM records
        WHERE deleted_at IS NULL {clause}
        GROUP BY record_kind, type_identifier
        ORDER BY record_kind, type_identifier
        """,  # noqa: S608 -- the optional clause is fixed above
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def query_workouts(
    connection: sqlite3.Connection, *, since: str | None, limit: int
) -> list[dict[str, Any]]:
    clause = "AND substr(start_at, 1, 10) >= ?" if since else ""
    parameters: tuple[Any, ...] = (since, limit) if since else (limit,)
    rows = connection.execute(
        f"""
        SELECT start_at, end_at, local_date, timezone, workout_json, source_json,
               received_at
        FROM records
        WHERE deleted_at IS NULL AND record_kind = 'workout' {clause}
        ORDER BY start_at DESC
        LIMIT ?
        """,  # noqa: S608 -- the optional clause is fixed above
        parameters,
    ).fetchall()
    results = []
    for row in rows:
        workout = _json_value(row["workout_json"])
        if not isinstance(workout, dict):
            raise QueryError("workout record is missing its workout descriptor")
        activity_type = workout.get("activityType")
        distance = workout.get("totalDistanceValue")
        distance_unit = workout.get("totalDistanceUnit")
        distance_miles = _distance_miles(distance, distance_unit)
        heart_rate = _heart_rate_summary(connection, row["start_at"], row["end_at"])
        results.append(
            {
                "start_at": row["start_at"],
                "end_at": row["end_at"],
                "local_date": row["local_date"],
                "timezone": row["timezone"],
                "activity_type": activity_type,
                "activity_name": WORKOUT_ACTIVITY_NAMES.get(str(activity_type)),
                "duration_seconds": workout.get("durationSeconds"),
                "duration_minutes": _minutes(workout.get("durationSeconds")),
                "energy": workout.get("totalEnergyValue"),
                "energy_unit": workout.get("totalEnergyUnit"),
                "distance": distance,
                "distance_unit": distance_unit,
                "distance_miles": distance_miles,
                "pace_minutes_per_mile": _pace_minutes_per_mile(
                    workout.get("durationSeconds"), distance_miles
                ),
                **heart_rate,
                "source_name": _source_name(row["source_json"]),
                "received_at": row["received_at"],
            }
        )
    return results


def query_daily(
    connection: sqlite3.Connection,
    *,
    selected_date: str | None,
    days: int,
    type_identifier: str | None,
) -> list[dict[str, Any]]:
    type_filter = "AND type_identifier = ?" if type_identifier else ""
    date_filter = "AND local_date = ?" if selected_date else ""
    date_limit = "" if selected_date else "LIMIT ?"
    selected_parameters: list[Any] = []
    if type_identifier:
        selected_parameters.append(type_identifier)
    selected_parameters.append(selected_date if selected_date else days)
    final_type_filter = "AND r.type_identifier = ?" if type_identifier else ""
    final_parameters = [type_identifier] if type_identifier else []
    rows = connection.execute(
        f"""
        WITH selected_dates AS (
            SELECT DISTINCT local_date
            FROM records
            WHERE deleted_at IS NULL AND record_kind = 'dailyAggregate'
              {type_filter} {date_filter}
            ORDER BY local_date DESC
            {date_limit}
        )
        SELECT r.local_date, r.timezone, r.type_identifier, r.value_json, r.unit,
               r.source_json, r.received_at
        FROM records r
        JOIN selected_dates d ON d.local_date = r.local_date
        WHERE r.deleted_at IS NULL AND r.record_kind = 'dailyAggregate'
          {final_type_filter}
        ORDER BY r.local_date DESC, r.type_identifier
        """,  # noqa: S608 -- clauses contain no user-provided SQL
        (*selected_parameters, *final_parameters),
    ).fetchall()
    return [
        {
            "local_date": row["local_date"],
            "timezone": row["timezone"],
            "type_identifier": row["type_identifier"],
            "value": _json_value(row["value_json"]),
            "unit": row["unit"],
            "source_name": _source_name(row["source_json"]),
            "received_at": row["received_at"],
        }
        for row in rows
    ]


def query_latest(
    connection: sqlite3.Connection,
    *,
    kind: str | None,
    type_identifier: str | None,
    since: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = ["deleted_at IS NULL"]
    parameters: list[Any] = []
    if kind:
        clauses.append("record_kind = ?")
        parameters.append(kind)
    if type_identifier:
        clauses.append("type_identifier = ?")
        parameters.append(type_identifier)
    if since:
        clauses.append("substr(start_at, 1, 10) >= ?")
        parameters.append(since)
    parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT record_kind, type_identifier, start_at, end_at, local_date,
               timezone, value_json, unit, source_json, workout_json, received_at
        FROM records
        WHERE {" AND ".join(clauses)}
        ORDER BY start_at DESC, type_identifier
        LIMIT ?
        """,  # noqa: S608 -- clauses contain only fixed SQL fragments
        tuple(parameters),
    ).fetchall()
    return [
        {
            "record_kind": row["record_kind"],
            "type_identifier": row["type_identifier"],
            "start_at": row["start_at"],
            "end_at": row["end_at"],
            "local_date": row["local_date"],
            "timezone": row["timezone"],
            "value": _json_value(row["value_json"]),
            "unit": row["unit"],
            "source_name": _source_name(row["source_json"]),
            "workout": _json_value(row["workout_json"]),
            "received_at": row["received_at"],
        }
        for row in rows
    ]


def _sleep_source_summaries(
    connection: sqlite3.Connection,
    *,
    selected_date: str,
    zone: ZoneInfo,
) -> tuple[list[dict[str, Any]], tuple[datetime, datetime] | None]:
    window_start, window_end = _night_window(selected_date, zone)
    rows = connection.execute(
        """
        SELECT start_at, end_at, value_json, source_json
        FROM records
        WHERE deleted_at IS NULL
          AND record_kind = 'category'
          AND type_identifier = ?
        ORDER BY start_at
        """,
        (SLEEP_TYPE_IDENTIFIER,),
    ).fetchall()
    grouped: dict[str, dict[str, list[tuple[datetime, datetime]]]] = {}
    for row in rows:
        start = _instant(row["start_at"], zone)
        end = _instant(row["end_at"], zone)
        if end <= window_start or start >= window_end:
            continue
        value = _json_value(row["value_json"])
        if value not in {*SLEEP_STAGE_VALUES, "awake", "inBed"}:
            continue
        source = _source_name(row["source_json"]) or "Unknown source"
        clipped = (max(start, window_start), min(end, window_end))
        grouped.setdefault(source, {}).setdefault(str(value), []).append(clipped)

    summaries: list[dict[str, Any]] = []
    bounds_by_source: dict[str, tuple[datetime, datetime]] = {}
    for source, values in grouped.items():
        sleep_intervals = _merge_intervals(
            [
                interval
                for stage in SLEEP_STAGE_VALUES
                for interval in values.get(stage, [])
            ]
        )
        awake_intervals = _merge_intervals(values.get("awake", []))
        in_bed_intervals = _merge_intervals(values.get("inBed", []))
        observed_intervals = [
            interval for intervals in values.values() for interval in intervals
        ]
        if not observed_intervals or not sleep_intervals:
            continue
        session_start = min(start for start, _ in observed_intervals)
        session_end = max(end for _, end in observed_intervals)
        bounds_by_source[source] = (session_start, session_end)
        asleep_seconds = _interval_seconds(sleep_intervals)
        session_span_seconds = (session_end - session_start).total_seconds()
        in_bed_seconds = (
            _interval_seconds(in_bed_intervals)
            if in_bed_intervals
            else session_span_seconds
        )
        efficiency_denominator = max(in_bed_seconds, session_span_seconds)
        stage_seconds = {
            label: _interval_seconds(_merge_intervals(values.get(stage, [])))
            for stage, label in SLEEP_STAGE_VALUES.items()
        }
        summaries.append(
            {
                "source_name": source,
                "session_start_local": _local_iso(session_start, zone),
                "session_end_local": _local_iso(session_end, zone),
                "session_span_minutes": _minutes_from_seconds(session_span_seconds),
                "in_bed_minutes": _minutes_from_seconds(in_bed_seconds),
                "asleep_minutes": _minutes_from_seconds(asleep_seconds),
                "awake_minutes": _minutes_from_seconds(
                    _interval_seconds(awake_intervals)
                ),
                "sleep_efficiency_pct": (
                    round(asleep_seconds / efficiency_denominator * 100, 1)
                    if efficiency_denominator > 0
                    else None
                ),
                "core_minutes": _minutes_from_seconds(stage_seconds["core"]),
                "deep_minutes": _minutes_from_seconds(stage_seconds["deep"]),
                "rem_minutes": _minutes_from_seconds(stage_seconds["rem"]),
                "unspecified_minutes": _minutes_from_seconds(
                    stage_seconds["unspecified"]
                ),
            }
        )

    summaries.sort(
        key=lambda row: (row["asleep_minutes"], row["session_span_minutes"]),
        reverse=True,
    )
    if not summaries:
        return [], None
    primary_source = summaries[0]["source_name"]
    return summaries, bounds_by_source[primary_source]


def _latest_quantities_for_date(
    connection: sqlite3.Connection,
    *,
    selected_date: str,
    zone: ZoneInfo,
    identifiers: Sequence[str],
) -> dict[str, dict[str, Any]]:
    placeholders = ", ".join("?" for _ in identifiers)
    rows = connection.execute(
        f"""
        SELECT type_identifier, start_at, value_json, unit, source_json
        FROM records
        WHERE deleted_at IS NULL
          AND record_kind = 'quantity'
          AND type_identifier IN ({placeholders})
        ORDER BY start_at
        """,  # noqa: S608 -- placeholders are generated, not user-provided
        tuple(identifiers),
    ).fetchall()
    selected: dict[str, tuple[datetime, dict[str, Any]]] = {}
    wanted_date = date.fromisoformat(selected_date)
    for row in rows:
        instant = _instant(row["start_at"], zone)
        if instant.astimezone(zone).date() != wanted_date:
            continue
        record = {
            "measured_at_local": _local_iso(instant, zone),
            "value": _json_value(row["value_json"]),
            "unit": row["unit"],
            "source_name": _source_name(row["source_json"]),
        }
        previous = selected.get(row["type_identifier"])
        if previous is None or instant > previous[0]:
            selected[row["type_identifier"]] = (instant, record)
    return {identifier: record for identifier, (_, record) in selected.items()}


def _body_snapshot(
    connection: sqlite3.Connection, *, selected_date: str, zone: ZoneInfo
) -> dict[str, Any] | None:
    records = _latest_quantities_for_date(
        connection,
        selected_date=selected_date,
        zone=zone,
        identifiers=BODY_TYPE_IDENTIFIERS,
    )
    weight = records.get("HKQuantityTypeIdentifierBodyMass")
    if weight is None:
        return None
    body_fat = records.get("HKQuantityTypeIdentifierBodyFatPercentage")
    bmi = records.get("HKQuantityTypeIdentifierBodyMassIndex")
    lean = records.get("HKQuantityTypeIdentifierLeanBodyMass")
    body_fat_value = body_fat.get("value") if body_fat else None
    if isinstance(body_fat_value, int | float) and abs(body_fat_value) <= 1.5:
        body_fat_value = round(float(body_fat_value) * 100, 1)
    elif isinstance(body_fat_value, int | float):
        body_fat_value = round(float(body_fat_value), 1)
    return {
        "measured_at_local": weight["measured_at_local"],
        "weight_lb": _mass_to_lb(weight["value"], weight["unit"]),
        "body_fat_pct": body_fat_value,
        "bmi": round(float(bmi["value"]), 1) if bmi else None,
        "lean_body_mass_lb": (
            _mass_to_lb(lean["value"], lean["unit"]) if lean else None
        ),
        "source_name": weight["source_name"],
    }


def _quantity_stats_in_window(
    connection: sqlite3.Connection,
    *,
    type_identifier: str,
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT start_at, value_json, unit, source_json
        FROM records
        WHERE deleted_at IS NULL
          AND record_kind = 'quantity'
          AND type_identifier = ?
        ORDER BY start_at
        """,
        (type_identifier,),
    ).fetchall()
    values: list[float] = []
    units: set[str] = set()
    sources: set[str] = set()
    for row in rows:
        instant = _instant(row["start_at"], zone)
        if not start <= instant <= end:
            continue
        value = _json_value(row["value_json"])
        if not isinstance(value, int | float):
            continue
        values.append(float(value))
        if row["unit"]:
            units.add(row["unit"])
        source = _source_name(row["source_json"])
        if source:
            sources.add(source)
    if not values:
        return {
            "samples": 0,
            "average": None,
            "minimum": None,
            "maximum": None,
            "unit": None,
            "source_names": [],
        }
    return {
        "samples": len(values),
        "average": round(sum(values) / len(values), 1),
        "minimum": round(min(values), 1),
        "maximum": round(max(values), 1),
        "unit": next(iter(units)) if len(units) == 1 else None,
        "source_names": sorted(sources),
    }


def _activity_snapshot(
    connection: sqlite3.Connection,
    *,
    selected_date: str,
    zone: ZoneInfo,
) -> dict[str, Any]:
    rows = query_daily(
        connection,
        selected_date=selected_date,
        days=1,
        type_identifier=None,
    )
    current_date = datetime.now(zone).date()
    status = "partial" if date.fromisoformat(selected_date) >= current_date else "final"
    values = {
        DAILY_ACTIVITY_NAMES[row["type_identifier"]]: row["value"]
        for row in rows
        if row["type_identifier"] in DAILY_ACTIVITY_NAMES
    }
    return {"date": selected_date, "status": status, **values}


def _recent_workouts(
    connection: sqlite3.Connection,
    *,
    selected_date: str,
    zone: ZoneInfo,
) -> list[dict[str, Any]]:
    window_start, window_end = _night_window(selected_date, zone)
    candidates = query_workouts(
        connection,
        since=(date.fromisoformat(selected_date) - timedelta(days=1)).isoformat(),
        limit=100,
    )
    selected = []
    for workout in candidates:
        start = _instant(workout["start_at"], zone)
        if not window_start <= start < window_end:
            continue
        end = _instant(workout["end_at"], zone)
        selected.append(
            {
                **workout,
                "start_local": _local_iso(start, zone),
                "end_local": _local_iso(end, zone),
            }
        )
    return sorted(selected, key=lambda row: row["start_at"])


def query_snapshot(
    connection: sqlite3.Connection,
    *,
    selected_date: str,
    timezone_name: str,
) -> list[dict[str, Any]]:
    zone = ZoneInfo(timezone_name)
    sleep_sources, primary_bounds = _sleep_source_summaries(
        connection, selected_date=selected_date, zone=zone
    )
    sleep: dict[str, Any] = {
        "primary_source": sleep_sources[0]["source_name"] if sleep_sources else None,
        "sources": sleep_sources,
        "cross_source_durations_summed": False,
    }
    if primary_bounds is not None:
        start, end = primary_bounds
        sleep["primary_window_metrics"] = {
            "heart_rate": _quantity_stats_in_window(
                connection,
                type_identifier="HKQuantityTypeIdentifierHeartRate",
                start=start,
                end=end,
                zone=zone,
            ),
            "hrv_sdnn": _quantity_stats_in_window(
                connection,
                type_identifier="HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
                start=start,
                end=end,
                zone=zone,
            ),
            "respiratory_rate": _quantity_stats_in_window(
                connection,
                type_identifier="HKQuantityTypeIdentifierRespiratoryRate",
                start=start,
                end=end,
                zone=zone,
            ),
        }

    resting = _latest_quantities_for_date(
        connection,
        selected_date=selected_date,
        zone=zone,
        identifiers=("HKQuantityTypeIdentifierRestingHeartRate",),
    ).get("HKQuantityTypeIdentifierRestingHeartRate")
    previous_date = (date.fromisoformat(selected_date) - timedelta(days=1)).isoformat()
    return [
        {
            "snapshot_date": selected_date,
            "timezone": timezone_name,
            "body": _body_snapshot(connection, selected_date=selected_date, zone=zone),
            "sleep": sleep,
            "resting_heart_rate": resting,
            "recent_workouts": _recent_workouts(
                connection, selected_date=selected_date, zone=zone
            ),
            "activity": {
                "previous_day": _activity_snapshot(
                    connection, selected_date=previous_date, zone=zone
                ),
                "snapshot_day": _activity_snapshot(
                    connection, selected_date=selected_date, zone=zone
                ),
            },
        }
    ]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def render_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No matching records."
    columns = list(rows[0])
    widths = {
        column: max(len(column), *(len(_cell(row.get(column))) for row in rows))
        for column in columns
    }
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    separator = "  ".join("-" * widths[column] for column in columns)
    body = [
        "  ".join(_cell(row.get(column)).ljust(widths[column]) for column in columns)
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def render_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(value) for key, value in row.items()})
    return output.getvalue().rstrip("\r\n")


def render(rows: list[dict[str, Any]], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True)
    if output_format == "csv":
        return render_csv(rows)
    return render_table(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        help=(
            "SQLite path (default: HEALTH_INGEST_SQLITE_PATH or "
            ".local/data/healthkit.db)"
        ),
    )
    parser.add_argument("--format", choices=OUTPUT_FORMATS, default="table")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("overview", help="show safe database and sync counts")
    batches = commands.add_parser("batches", help="show newest sync batches")
    batches.add_argument("--limit", type=_positive_int, default=10)
    types = commands.add_parser("types", help="list available record types")
    types.add_argument("--kind", choices=RECORD_KINDS)
    workouts = commands.add_parser("workouts", help="show newest workout summaries")
    workouts.add_argument("--since", type=_iso_date)
    workouts.add_argument("--limit", type=_positive_int, default=20)
    workout_analysis = commands.add_parser(
        "workout-analysis",
        help="analyze intensity and immediate recovery for one workout",
    )
    workout_analysis.add_argument("--since", type=_iso_date)
    workout_analysis.add_argument("--activity-type")
    workout_analysis.add_argument("--start-at")
    workout_analysis.add_argument(
        "--recovery-minutes", type=_positive_int, default=5
    )
    workout_analysis.add_argument(
        "--timezone", type=_timezone_name, default=os.environ.get("HEALTH_TIMEZONE", "UTC")
    )
    daily = commands.add_parser("daily", help="show daily aggregate values")
    dates = daily.add_mutually_exclusive_group()
    dates.add_argument("--date", dest="selected_date", type=_iso_date)
    dates.add_argument("--days", type=_positive_int, default=7)
    daily.add_argument("--type", dest="type_identifier")
    latest = commands.add_parser("latest", help="show newest filtered records")
    latest.add_argument("--kind", choices=RECORD_KINDS)
    latest.add_argument("--type", dest="type_identifier")
    latest.add_argument("--since", type=_iso_date)
    latest.add_argument("--limit", type=_positive_int, default=20)
    snapshot = commands.add_parser(
        "snapshot",
        help="show one reconciled morning snapshot without summing sleep sources",
    )
    snapshot.add_argument("--date", dest="selected_date", required=True, type=_iso_date)
    snapshot.add_argument("--timezone", type=_timezone_name, default=os.environ.get("HEALTH_TIMEZONE", "UTC"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    try:
        with _connect_read_only(_database_path(namespace.database)) as connection:
            if namespace.command == "overview":
                rows = query_overview(connection)
            elif namespace.command == "batches":
                rows = query_batches(connection, namespace.limit)
            elif namespace.command == "types":
                rows = query_types(connection, namespace.kind)
            elif namespace.command == "workouts":
                rows = query_workouts(
                    connection, since=namespace.since, limit=namespace.limit
                )
            elif namespace.command == "workout-analysis":
                rows = query_workout_analysis(
                    connection,
                    since=namespace.since,
                    activity_type=namespace.activity_type,
                    start_at=namespace.start_at,
                    recovery_minutes=namespace.recovery_minutes,
                    timezone_name=namespace.timezone,
                )
            elif namespace.command == "daily":
                rows = query_daily(
                    connection,
                    selected_date=namespace.selected_date,
                    days=namespace.days,
                    type_identifier=namespace.type_identifier,
                )
            elif namespace.command == "latest":
                rows = query_latest(
                    connection,
                    kind=namespace.kind,
                    type_identifier=namespace.type_identifier,
                    since=namespace.since,
                    limit=namespace.limit,
                )
            elif namespace.command == "snapshot":
                rows = query_snapshot(
                    connection,
                    selected_date=namespace.selected_date,
                    timezone_name=namespace.timezone,
                )
            else:
                raise AssertionError(f"unsupported command: {namespace.command}")
        print(render(rows, namespace.format))
        return 0
    except (QueryError, sqlite3.Error) as exc:
        print(f"health_db_query: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

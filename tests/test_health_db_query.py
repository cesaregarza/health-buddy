from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.health_db_query import (
    QueryError,
    _connect_read_only,
    query_daily,
    query_overview,
    query_snapshot,
    query_workout_analysis,
    query_workouts,
    render,
)


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE devices (
                device_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                token_salt BLOB NOT NULL,
                token_hash BLOB NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE batches (
                device_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                deletion_count INTEGER NOT NULL,
                PRIMARY KEY (device_id, batch_id)
            );
            CREATE TABLE records (
                device_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                record_kind TEXT NOT NULL,
                type_identifier TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                creation_at TEXT,
                local_date TEXT,
                timezone TEXT,
                value_json TEXT,
                unit TEXT,
                source_json TEXT,
                device_json TEXT,
                workout_json TEXT,
                record_sha256 TEXT NOT NULL,
                received_at TEXT NOT NULL,
                deleted_at TEXT,
                PRIMARY KEY (device_id, record_id)
            );
            CREATE TABLE tombstones (
                device_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                type_identifier TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (device_id, record_id)
            );
            PRAGMA user_version = 1;
            """
        )
        connection.execute(
            "INSERT INTO devices VALUES (?, ?, ?, ?, ?, NULL)",
            ("device", "phone", b"salt", b"hash", "2026-08-30T10:00:00Z"),
        )
        connection.execute(
            "INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "device",
                "batch",
                "hash",
                "2026-08-30T18:00:00Z",
                "2026-08-30T18:00:01Z",
                2,
                0,
            ),
        )
        connection.execute(
            """
            INSERT INTO records VALUES (
                ?, ?, 'workout', 'HKWorkoutTypeIdentifier', ?, ?, NULL, NULL,
                NULL, 'null', NULL, ?, NULL, ?, 'hash', ?, NULL
            )
            """,
            (
                "device",
                "walk",
                "2026-08-30T16:00:00-05:00",
                "2026-08-30T16:30:00-05:00",
                json.dumps({"name": "Workout"}),
                json.dumps(
                    {
                        "activityType": "52",
                        "durationSeconds": 1800,
                        "totalDistanceValue": 1.2,
                        "totalDistanceUnit": "mi",
                        "totalEnergyValue": 150,
                        "totalEnergyUnit": "kcal",
                    }
                ),
                "2026-08-30T18:00:01Z",
            ),
        )
        for index, value in enumerate((90, 100, 110), start=1):
            timestamp = f"2026-08-30T16:{index:02d}:00-05:00"
            connection.execute(
                """
                INSERT INTO records VALUES (
                    ?, ?, 'quantity', 'HKQuantityTypeIdentifierHeartRate',
                    ?, ?, NULL, NULL, NULL, ?, 'count/min', ?, NULL, NULL,
                    'hash', ?, NULL
                )
                """,
                (
                    "device",
                    f"heart-rate-{index}",
                    timestamp,
                    timestamp,
                    json.dumps(value),
                    json.dumps({"name": "Workout"}),
                    "2026-08-30T18:00:01Z",
                ),
            )
        connection.execute(
            """
            INSERT INTO records VALUES (
                ?, ?, 'dailyAggregate', 'HKQuantityTypeIdentifierStepCount',
                ?, ?, NULL, ?, ?, ?, 'count', ?, NULL, NULL, 'hash', ?, NULL
            )
            """,
            (
                "device",
                "steps",
                "2026-08-30T00:00:00-05:00",
                "2026-08-31T00:00:00-05:00",
                "2026-08-30",
                "America/Chicago",
                json.dumps(4321),
                json.dumps({"name": "Health"}),
                "2026-08-30T18:00:01Z",
            ),
        )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "healthkit.db"
    create_database(path)
    return path


def test_read_only_queries_return_safe_summary_and_workout(database: Path) -> None:
    with _connect_read_only(database) as connection:
        overview = query_overview(connection)
        workouts = query_workouts(connection, since="2026-08-30", limit=5)

    assert {row["metric"]: row["value"] for row in overview}["active_records"] == 5
    assert workouts == [
        {
            "start_at": "2026-08-30T16:00:00-05:00",
            "end_at": "2026-08-30T16:30:00-05:00",
            "local_date": None,
            "timezone": None,
            "activity_type": "52",
            "activity_name": "walking",
            "duration_seconds": 1800,
            "duration_minutes": 30.0,
            "energy": 150,
            "energy_unit": "kcal",
            "distance": 1.2,
            "distance_unit": "mi",
            "distance_miles": 1.2,
            "pace_minutes_per_mile": 25.0,
            "heart_rate_samples": 3,
            "avg_heart_rate_bpm": 100.0,
            "min_heart_rate_bpm": 90.0,
            "max_heart_rate_bpm": 110.0,
            "source_name": "Workout",
            "received_at": "2026-08-30T18:00:01Z",
        }
    ]


def test_daily_query_and_machine_readable_rendering(database: Path) -> None:
    with _connect_read_only(database) as connection:
        rows = query_daily(
            connection,
            selected_date="2026-08-30",
            days=7,
            type_identifier="HKQuantityTypeIdentifierStepCount",
        )

    assert rows[0]["value"] == 4321
    assert json.loads(render(rows, "json"))[0]["unit"] == "count"
    assert "local_date,timezone,type_identifier" in render(rows, "csv")


def test_workout_analysis_summarizes_intensity_and_recovery(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        for record_id, timestamp, value in (
            ("heart-rate-end", "2026-08-30T16:29:45-05:00", 120),
            ("heart-rate-post-1", "2026-08-30T16:30:45-05:00", 100),
            ("heart-rate-post-3", "2026-08-30T16:32:45-05:00", 90),
        ):
            connection.execute(
                """
                INSERT INTO records VALUES (
                    ?, ?, 'quantity', 'HKQuantityTypeIdentifierHeartRate',
                    ?, ?, NULL, NULL, NULL, ?, 'count/min', ?, NULL, NULL,
                    'hash', ?, NULL
                )
                """,
                (
                    "device",
                    record_id,
                    timestamp,
                    timestamp,
                    json.dumps(value),
                    json.dumps({"name": "Workout"}),
                    "2026-08-30T18:00:01Z",
                ),
            )

    with _connect_read_only(database) as connection:
        rows = query_workout_analysis(
            connection,
            since="2026-08-30",
            activity_type="52",
            start_at=None,
            recovery_minutes=5,
            timezone_name="America/Chicago",
        )

    result = rows[0]
    assert result["elapsed_minutes"] == 30.0
    assert result["active_minutes"] == 30.0
    assert result["paused_minutes"] == 0.0
    assert result["heart_rate_samples"] == 4
    assert result["avg_heart_rate_bpm"] == 105.0
    assert result["median_heart_rate_bpm"] == 105.0
    assert result["samples_under_100_bpm_percent"] == 25.0
    assert result["samples_120_to_129_bpm_percent"] == 25.0
    assert result["last_workout_minute_avg_bpm"] == 120.0
    assert result["post_1_minute_avg_bpm"] == 100.0
    assert result["post_3_minute_avg_bpm"] == 90.0
    assert result["drop_to_post_1_minute_bpm"] == 20.0
    assert result["drop_to_post_3_minutes_bpm"] == 30.0


def test_missing_database_is_rejected_without_creation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(QueryError, match="does not exist"):
        _connect_read_only(missing)
    assert not missing.exists()


def test_snapshot_keeps_overlapping_sleep_sources_separate(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        sleep_rows = [
            (
                "pillow-in-bed",
                "2026-09-01T08:30:00Z",
                "2026-09-01T15:00:00Z",
                "inBed",
                "Pillow",
            ),
            (
                "pillow-core",
                "2026-09-01T08:30:00Z",
                "2026-09-01T11:30:00Z",
                "asleepCore",
                "Pillow",
            ),
            (
                "pillow-awake",
                "2026-09-01T11:30:00Z",
                "2026-09-01T12:00:00Z",
                "awake",
                "Pillow",
            ),
            (
                "pillow-deep",
                "2026-09-01T12:00:00Z",
                "2026-09-01T13:30:00Z",
                "asleepDeep",
                "Pillow",
            ),
            (
                "pillow-rem",
                "2026-09-01T13:30:00Z",
                "2026-09-01T15:00:00Z",
                "asleepREM",
                "Pillow",
            ),
            (
                "watch-in-bed",
                "2026-09-01T08:20:00Z",
                "2026-09-01T13:00:00Z",
                "inBed",
                "Watch",
            ),
            (
                "watch-core",
                "2026-09-01T08:20:00Z",
                "2026-09-01T13:00:00Z",
                "asleepCore",
                "Watch",
            ),
        ]
        for record_id, start, end, value, source in sleep_rows:
            connection.execute(
                """
                INSERT INTO records VALUES (
                    ?, ?, 'category', ?, ?, ?, NULL, NULL, NULL, ?, NULL,
                    ?, NULL, NULL, 'hash', ?, NULL
                )
                """,
                (
                    "device",
                    record_id,
                    "HKCategoryTypeIdentifierSleepAnalysis",
                    start,
                    end,
                    json.dumps(value),
                    json.dumps({"name": source}),
                    "2026-09-01T15:10:00Z",
                ),
            )

        quantity_rows = [
            ("weight", "HKQuantityTypeIdentifierBodyMass", 91.081347896, "kg"),
            (
                "body-fat",
                "HKQuantityTypeIdentifierBodyFatPercentage",
                0.219,
                "%",
            ),
            ("bmi", "HKQuantityTypeIdentifierBodyMassIndex", 27.2, "count"),
            (
                "lean",
                "HKQuantityTypeIdentifierLeanBodyMass",
                71.1345327068,
                "kg",
            ),
        ]
        for record_id, type_identifier, value, unit in quantity_rows:
            connection.execute(
                """
                INSERT INTO records VALUES (
                    ?, ?, 'quantity', ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, NULL,
                    NULL, 'hash', ?, NULL
                )
                """,
                (
                    "device",
                    record_id,
                    type_identifier,
                    "2026-09-01T15:24:34Z",
                    "2026-09-01T15:24:34Z",
                    json.dumps(value),
                    unit,
                    json.dumps({"name": "Weight Gurus"}),
                    "2026-09-01T15:26:00Z",
                ),
            )

    with _connect_read_only(database) as connection:
        row = query_snapshot(
            connection,
            selected_date="2026-09-01",
            timezone_name="America/Chicago",
        )[0]

    assert row["body"] == {
        "measured_at_local": "2026-09-01T10:24:34-05:00",
        "weight_lb": 200.8,
        "body_fat_pct": 21.9,
        "bmi": 27.2,
        "lean_body_mass_lb": 156.8,
        "source_name": "Weight Gurus",
    }
    assert row["sleep"]["primary_source"] == "Pillow"
    assert row["sleep"]["cross_source_durations_summed"] is False
    assert len(row["sleep"]["sources"]) == 2
    assert row["sleep"]["sources"][0]["asleep_minutes"] == 360.0
    assert row["sleep"]["sources"][1]["asleep_minutes"] == 280.0

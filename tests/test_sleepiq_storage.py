from __future__ import annotations

import csv
import sqlite3
import stat
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from sleepiq_exporter.config import Settings
from sleepiq_exporter.domain import BedInfo, SleeperInfo, SleepMetrics
from sleepiq_exporter.normalization import normalize_record
from sleepiq_exporter.storage import (
    SqlAlchemySleepRepository,
    create_database_engine,
    open_repository,
)


def make_record(
    score: int = 80,
    *,
    query_date: date = date(2026, 8, 2),
    fetched_at: datetime = datetime(2026, 8, 2, 15, tzinfo=UTC),
):
    return normalize_record(
        bed=BedInfo("bed", None, None, None),
        sleeper=SleeperInfo("key", "bed", "sleeper", None, "left", True),
        query_date=query_date,
        metrics=SleepMetrics(
            start_date="2026-08-02T04:00:00Z",
            duration_seconds=27000,
            sleep_score=score,
        ),
        timezone=ZoneInfo("America/Chicago"),
        include_names=False,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
        fetched_at=fetched_at,
    )


def test_sqlite_migration_persistence_idempotency_and_changed_upsert(
    settings: Settings,
) -> None:
    repository = open_repository(settings)
    assert isinstance(repository, SqlAlchemySleepRepository)
    try:
        first = make_record()
        inserted = repository.upsert([first])
        repeated = repository.upsert(
            [make_record(fetched_at=datetime(2026, 8, 2, 16, tzinfo=UTC))]
        )
        changed = repository.upsert(
            [make_record(score=84, fetched_at=datetime(2026, 8, 2, 17, tzinfo=UTC))]
        )
        rows = repository.all_rows()

        assert inserted.inserted == 1
        assert repeated.unchanged == 1
        assert changed.changed == 1
        assert len(rows) == 1
        assert rows[0]["sleep_score"] == 84
        assert (
            repository.apply_retention(as_of=date(2026, 9, 1), retention_days=None) == 0
        )
    finally:
        repository.close()

    mode = stat.S_IMODE(settings.sqlite_path.stat().st_mode)
    assert mode == 0o600
    with sqlite3.connect(settings.sqlite_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_two_query_dates_for_one_canonical_night_do_not_duplicate(
    settings: Settings,
) -> None:
    repository = open_repository(settings)
    try:
        first = make_record(query_date=date(2026, 8, 2))
        later_query = make_record(query_date=date(2026, 8, 3))
        result = repository.upsert([first, later_query])
        rows = repository.all_rows()
        assert result.inserted == 1
        assert len(rows) == 1
        assert rows[0]["query_date"] == date(2026, 8, 3)
    finally:
        repository.close()


def test_csv_export_is_read_only_and_owner_only(
    settings: Settings, tmp_path: Path
) -> None:
    repository = open_repository(settings)
    output = tmp_path / "exports" / "sleep.csv"
    try:
        repository.upsert([make_record()])
        before = repository.all_rows()
        exported = repository.export_csv(
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 3),
            output=output,
        )
        after = repository.all_rows()
    finally:
        repository.close()

    assert exported == 1
    assert before == after
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sleep_score"] == "80"


def test_postgresql_engine_uses_psycopg_and_timeouts(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "DATABASE_URL": "postgresql://user:demo@example.invalid/health",
            "SQLITE_PATH": str(tmp_path / "unused.db"),
            "DATABASE_TIMEOUT_SECONDS": "12",
        },
        require_credentials=False,
    )
    engine = Mock(spec=Engine)
    with patch("sleepiq_exporter.storage.create_engine", return_value=engine) as create:
        result = create_database_engine(settings)

    assert result is engine
    args, kwargs = create.call_args
    assert args[0].startswith("postgresql+psycopg://")
    assert kwargs["connect_args"] == {"connect_timeout": 12}
    assert kwargs["pool_timeout"] == 12.0

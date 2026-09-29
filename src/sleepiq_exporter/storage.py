"""Transactional SQLAlchemy persistence for SQLite and PostgreSQL."""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast

from sqlalchemy import (
    Connection,
    Engine,
    create_engine,
    delete,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .domain import NightlyRecord, UpsertCounts
from .errors import StorageError
from .migration_runner import run_migrations
from .schema import CSV_COLUMNS, nightly_sleep


class SleepRepository(Protocol):
    def upsert(self, records: Sequence[NightlyRecord]) -> UpsertCounts: ...

    def apply_retention(self, *, as_of: date, retention_days: int | None) -> int: ...

    def export_csv(self, *, from_date: date, to_date: date, output: Path) -> int: ...

    def close(self) -> None: ...


def _sqlite_path(settings: Settings) -> Path | None:
    return None if settings.database_url else settings.sqlite_path.resolve()


def _configure_sqlite(
    dbapi_connection: Any, _connection_record: Any, *, timeout_ms: int
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={timeout_ms:d}")
    finally:
        cursor.close()


def create_database_engine(settings: Settings) -> Engine:
    url = settings.effective_database_url
    try:
        if url.startswith("sqlite+"):
            engine = create_engine(
                url,
                connect_args={
                    "timeout": settings.database_timeout_seconds,
                    "check_same_thread": False,
                },
                pool_pre_ping=True,
            )
            event.listen(
                engine,
                "connect",
                partial(
                    _configure_sqlite,
                    timeout_ms=int(settings.database_timeout_seconds * 1000),
                ),
            )
            return engine
        return create_engine(
            url,
            connect_args={"connect_timeout": int(settings.database_timeout_seconds)},
            pool_pre_ping=True,
            pool_timeout=settings.database_timeout_seconds,
        )
    except (SQLAlchemyError, ValueError) as exc:
        raise StorageError("database engine creation failed") from exc


def open_repository(
    settings: Settings, *, migrate: bool = True
) -> SqlAlchemySleepRepository:
    sqlite_path = _sqlite_path(settings)
    if migrate:
        run_migrations(settings.effective_database_url, sqlite_path)
    repository = SqlAlchemySleepRepository(
        create_database_engine(settings), sqlite_path=sqlite_path
    )
    try:
        repository.verify_connection()
    except Exception:
        repository.close()
        raise
    return repository


def _values(record: NightlyRecord) -> dict[str, Any]:
    return {column: getattr(record, column) for column in CSV_COLUMNS}


class SqlAlchemySleepRepository:
    def __init__(self, engine: Engine, *, sqlite_path: Path | None = None) -> None:
        self._engine = engine
        self._sqlite_path = sqlite_path

    def verify_connection(self) -> None:
        try:
            with self._engine.connect() as connection:
                connection.execute(select(1))
            self._protect_sqlite_file()
        except SQLAlchemyError as exc:
            raise StorageError("database connection failed") from exc

    def _protect_sqlite_file(self) -> None:
        if self._sqlite_path is None:
            return
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self._sqlite_path}{suffix}")
            if path.exists():
                path.chmod(0o600)

    def _insert_if_absent(
        self, connection: Connection, values: Mapping[str, Any]
    ) -> bool:
        if self._engine.dialect.name == "sqlite":
            sqlite_statement = sqlite_insert(nightly_sleep).values(**values)
            sqlite_statement = sqlite_statement.on_conflict_do_nothing(
                index_elements=[nightly_sleep.c.record_key]
            )
            result = connection.execute(sqlite_statement)
        elif self._engine.dialect.name == "postgresql":
            postgresql_statement = postgresql_insert(nightly_sleep).values(**values)
            postgresql_statement = postgresql_statement.on_conflict_do_nothing(
                index_elements=[nightly_sleep.c.record_key]
            )
            result = connection.execute(postgresql_statement)
        else:  # pragma: no cover - only supported backends reach production
            result = connection.execute(insert(nightly_sleep).values(**values))
        return bool(result.rowcount)

    def _existing_hash(self, connection: Connection, record_key: str) -> str | None:
        value = connection.execute(
            select(nightly_sleep.c.record_hash).where(
                nightly_sleep.c.record_key == record_key
            )
        ).scalar_one_or_none()
        return cast(str | None, value)

    def upsert(self, records: Sequence[NightlyRecord]) -> UpsertCounts:
        # If two query dates resolve to one canonical night in the same run, the
        # later query date is the final observation for that stable key.
        deduplicated = {record.record_key: record for record in records}
        inserted_count = 0
        changed_count = 0
        unchanged_count = 0
        try:
            with self._engine.begin() as connection:
                for record in deduplicated.values():
                    values = _values(record)
                    existing_hash = self._existing_hash(connection, record.record_key)
                    if existing_hash is None and self._insert_if_absent(
                        connection, values
                    ):
                        inserted_count += 1
                        continue

                    # A concurrent writer may have inserted after our first read.
                    existing_hash = self._existing_hash(connection, record.record_key)
                    if existing_hash == record.record_hash:
                        connection.execute(
                            update(nightly_sleep)
                            .where(nightly_sleep.c.record_key == record.record_key)
                            .values(
                                query_date=record.query_date,
                                fetched_at=record.fetched_at,
                            )
                        )
                        unchanged_count += 1
                    else:
                        connection.execute(
                            update(nightly_sleep)
                            .where(nightly_sleep.c.record_key == record.record_key)
                            .values(**values)
                        )
                        changed_count += 1
            self._protect_sqlite_file()
        except (SQLAlchemyError, OSError) as exc:
            raise StorageError("database upsert failed") from exc
        return UpsertCounts(inserted_count, changed_count, unchanged_count)

    def apply_retention(self, *, as_of: date, retention_days: int | None) -> int:
        if retention_days is None:
            return 0
        cutoff = as_of - timedelta(days=retention_days)
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    delete(nightly_sleep).where(nightly_sleep.c.night_date < cutoff)
                )
            return max(0, result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise StorageError("database retention failed") from exc

    def export_csv(self, *, from_date: date, to_date: date, output: Path) -> int:
        try:
            with self._engine.connect() as connection:
                rows = [
                    dict(row)
                    for row in connection.execute(
                        select(nightly_sleep)
                        .where(nightly_sleep.c.night_date >= from_date)
                        .where(nightly_sleep.c.night_date <= to_date)
                        .order_by(
                            nightly_sleep.c.night_date,
                            nightly_sleep.c.sleeper_id,
                        )
                    ).mappings()
                ]
            _write_csv_atomic(output, rows)
            return len(rows)
        except (SQLAlchemyError, OSError, csv.Error) as exc:
            raise StorageError("CSV export failed") from exc

    def all_rows(self) -> list[dict[str, Any]]:
        """Return deterministic rows for local diagnostics and unit tests."""

        try:
            with self._engine.connect() as connection:
                return [
                    dict(row)
                    for row in connection.execute(
                        select(nightly_sleep).order_by(nightly_sleep.c.record_key)
                    ).mappings()
                ]
        except SQLAlchemyError as exc:
            raise StorageError("database query failed") from exc

    def close(self) -> None:
        self._engine.dispose()


def _csv_value(value: Any) -> str | int | float | None:
    if isinstance(value, (date,)):
        return value.isoformat()
    return cast(str | int | float | None, value)


def _write_csv_atomic(
    output: Path, rows: Iterable[Mapping[str, Any] | RowMapping]
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {column: _csv_value(row[column]) for column in CSV_COLUMNS}
                )
        os.replace(temporary_name, output)
        output.chmod(0o600)
    finally:
        os.umask(old_umask)
        if temporary_name and Path(temporary_name).exists():
            Path(temporary_name).unlink()

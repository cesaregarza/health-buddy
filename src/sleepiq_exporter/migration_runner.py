"""Safe, forward-only Alembic migration entry point."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from .errors import StorageError


def run_migrations(database_url: str, sqlite_path: Path | None = None) -> None:
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().with_name("migrations"))
    )
    # ConfigParser treats percent characters in escaped database credentials as
    # interpolation markers. Doubling them preserves the URL without logging it.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    try:
        old_umask = os.umask(0o077)
        try:
            command.upgrade(config, "head")
        finally:
            os.umask(old_umask)
        if sqlite_path is not None and sqlite_path.exists():
            with sqlite3.connect(sqlite_path, timeout=30) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
            sqlite_path.chmod(0o600)
    except Exception as exc:
        raise StorageError("database migration failed") from exc

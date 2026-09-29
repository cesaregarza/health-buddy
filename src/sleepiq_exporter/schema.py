"""SQLAlchemy schema shared by storage and migration metadata."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

nightly_sleep = Table(
    "nightly_sleep",
    metadata,
    Column("record_key", String(64), primary_key=True),
    Column("query_date", Date, nullable=False),
    Column("night_date", Date, nullable=False),
    Column("bed_id", String(255), nullable=False),
    Column("bed_name", String(255), nullable=True),
    Column("bed_generation", String(64), nullable=True),
    Column("sleeper_id", String(255), nullable=False),
    Column("sleeper_name", String(255), nullable=True),
    Column("side", String(32), nullable=False),
    Column("session_start", DateTime(timezone=True), nullable=True),
    Column("session_end", DateTime(timezone=True), nullable=True),
    Column("duration_seconds", Integer, nullable=True),
    Column("session_count", Integer, nullable=True),
    Column("sleep_score", Integer, nullable=True),
    Column("heart_rate_bpm", Float, nullable=True),
    Column("respiratory_rate_bpm", Float, nullable=True),
    Column("hrv_ms", Float, nullable=True),
    Column("restful_seconds", Integer, nullable=True),
    Column("restless_seconds", Integer, nullable=True),
    Column("out_of_bed_seconds", Integer, nullable=True),
    Column("fall_asleep_seconds", Integer, nullable=True),
    Column("source_library", String(64), nullable=False),
    Column("source_library_version", String(64), nullable=False),
    Column("extractor_version", String(64), nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("record_hash", String(64), nullable=False),
)

Index("ix_nightly_sleep_night_date", nightly_sleep.c.night_date)
Index("ix_nightly_sleep_query_date", nightly_sleep.c.query_date)
Index("ix_nightly_sleep_fetched_at", nightly_sleep.c.fetched_at)

CSV_COLUMNS = tuple(column.name for column in nightly_sleep.columns)

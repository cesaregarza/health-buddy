"""Create the normalized nightly SleepIQ table.

Revision ID: 0001
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nightly_sleep",
        sa.Column("record_key", sa.String(length=64), nullable=False),
        sa.Column("query_date", sa.Date(), nullable=False),
        sa.Column("night_date", sa.Date(), nullable=False),
        sa.Column("bed_id", sa.String(length=255), nullable=False),
        sa.Column("bed_name", sa.String(length=255), nullable=True),
        sa.Column("bed_generation", sa.String(length=64), nullable=True),
        sa.Column("sleeper_id", sa.String(length=255), nullable=False),
        sa.Column("sleeper_name", sa.String(length=255), nullable=True),
        sa.Column("side", sa.String(length=32), nullable=False),
        sa.Column("session_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("session_count", sa.Integer(), nullable=True),
        sa.Column("sleep_score", sa.Integer(), nullable=True),
        sa.Column("heart_rate_bpm", sa.Float(), nullable=True),
        sa.Column("respiratory_rate_bpm", sa.Float(), nullable=True),
        sa.Column("hrv_ms", sa.Float(), nullable=True),
        sa.Column("restful_seconds", sa.Integer(), nullable=True),
        sa.Column("restless_seconds", sa.Integer(), nullable=True),
        sa.Column("out_of_bed_seconds", sa.Integer(), nullable=True),
        sa.Column("fall_asleep_seconds", sa.Integer(), nullable=True),
        sa.Column("source_library", sa.String(length=64), nullable=False),
        sa.Column("source_library_version", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("record_key", name="pk_nightly_sleep"),
    )
    op.create_index("ix_nightly_sleep_fetched_at", "nightly_sleep", ["fetched_at"])
    op.create_index("ix_nightly_sleep_night_date", "nightly_sleep", ["night_date"])
    op.create_index("ix_nightly_sleep_query_date", "nightly_sleep", ["query_date"])


def downgrade() -> None:
    # Downgrade exists for migration tooling, but startup never invokes it.
    op.drop_index("ix_nightly_sleep_query_date", table_name="nightly_sleep")
    op.drop_index("ix_nightly_sleep_night_date", table_name="nightly_sleep")
    op.drop_index("ix_nightly_sleep_fetched_at", table_name="nightly_sleep")
    op.drop_table("nightly_sleep")

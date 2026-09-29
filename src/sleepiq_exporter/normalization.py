"""Normalize library records into stable nightly rows."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import __version__
from .domain import BedInfo, NightlyRecord, SleeperInfo, SleepMetrics


def _timestamp(value: str | None, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Live SleepIQ validation showed that offset-less session timestamps
        # are wall-clock times in the bed's configured timezone. Query-date
        # selection is UTC-oriented, but that is a separate API behavior.
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _nonnegative_int(value: int | None) -> int | None:
    return value if value is not None and value >= 0 else None


def _positive_float(value: float | None) -> float | None:
    return value if value is not None and value > 0 else None


def _score(value: int | None) -> int | None:
    return value if value is not None and 0 <= value <= 100 else None


def _record_key(sleeper_id: str, night_date: date) -> str:
    identity = f"{sleeper_id}\x1f{night_date.isoformat()}".encode()
    return hashlib.sha256(identity).hexdigest()


def _record_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat(),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalize_record(
    *,
    bed: BedInfo,
    sleeper: SleeperInfo,
    query_date: date,
    metrics: SleepMetrics,
    timezone: ZoneInfo,
    include_names: bool,
    source_library: str,
    source_library_version: str,
    fetched_at: datetime | None = None,
) -> NightlyRecord:
    session_start = _timestamp(metrics.start_date, timezone)
    session_end = _timestamp(metrics.end_date, timezone)
    night_date = (
        session_start.astimezone(timezone).date() if session_start else query_date
    )
    observed_at = fetched_at or datetime.now(UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    observed_at = observed_at.astimezone(UTC)

    values: dict[str, Any] = {
        "record_key": _record_key(sleeper.sleeper_id, night_date),
        "night_date": night_date,
        "bed_id": bed.bed_id,
        "bed_name": bed.name if include_names else None,
        "bed_generation": bed.generation,
        "sleeper_id": sleeper.sleeper_id,
        "sleeper_name": sleeper.name if include_names else None,
        "side": sleeper.side,
        "session_start": session_start,
        "session_end": session_end,
        "duration_seconds": _nonnegative_int(metrics.duration_seconds),
        "session_count": _nonnegative_int(metrics.session_count),
        "sleep_score": _score(metrics.sleep_score),
        "heart_rate_bpm": _positive_float(metrics.heart_rate_bpm),
        "respiratory_rate_bpm": _positive_float(metrics.respiratory_rate_bpm),
        "hrv_ms": _positive_float(metrics.hrv_ms),
        "restful_seconds": _nonnegative_int(metrics.restful_seconds),
        "restless_seconds": _nonnegative_int(metrics.restless_seconds),
        "out_of_bed_seconds": _nonnegative_int(metrics.out_of_bed_seconds),
        "fall_asleep_seconds": _nonnegative_int(metrics.fall_asleep_seconds),
        "source_library": source_library,
        "source_library_version": source_library_version,
        "extractor_version": __version__,
    }
    return NightlyRecord(
        query_date=query_date,
        fetched_at=observed_at,
        record_hash=_record_hash(values),
        **values,
    )

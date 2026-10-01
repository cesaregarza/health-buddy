"""Finite reviewed nightly-export mapping to the existing read-only daily view."""

from __future__ import annotations

import math
from dataclasses import fields
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from health_buddy.core.git_store import csv_text, parse_csv
from health_buddy.core.service_api import ServiceError
from health_buddy.legacy_import import MAX_RECORDS


def daily_export(raw: str, *, sleeper_id: str, timezone: str) -> tuple[str, int]:
    # Domain/normalization are library-independent; never load the network or
    # SQLAlchemy connector at core CLI import time.
    from sleepiq_exporter.domain import NightlyRecord
    from sleepiq_exporter.normalization import _record_hash, _record_key

    try:
        zone = ZoneInfo(timezone)
        if not sleeper_id or len(sleeper_id) > 255:
            raise ValueError
        rows = parse_csv(raw, [field.name for field in fields(NightlyRecord)])
        if not 1 <= len(rows) <= MAX_RECORDS:
            raise ValueError
        keys, days = set(), set()
        mapped = []
        for row in rows:
            if row["sleeper_id"] != sleeper_id:
                raise ValueError
            values: dict[str, object] = dict(row)
            for key in ("query_date", "night_date"):
                parsed = date.fromisoformat(row[key])
                if parsed.isoformat() != row[key]:
                    raise ValueError
                values[key] = parsed
            for key in ("session_start", "session_end", "fetched_at"):
                stamp = datetime.fromisoformat(row[key]) if row[key] else None
                if stamp is not None and stamp.utcoffset() is None:
                    raise ValueError
                values[key] = stamp
            end = values["session_end"]
            if not isinstance(end, datetime) or values["fetched_at"] is None:
                raise ValueError
            start = values["session_start"]
            if isinstance(start, datetime) and start > end:
                raise ValueError
            for key in (
                "duration_seconds",
                "session_count",
                "sleep_score",
                "restful_seconds",
                "restless_seconds",
                "out_of_bed_seconds",
                "fall_asleep_seconds",
            ):
                number = int(row[key]) if row[key] else None
                if number is not None and number < 0:
                    raise ValueError
                values[key] = number
            for key in ("heart_rate_bpm", "respiratory_rate_bpm", "hrv_ms"):
                floating = float(row[key]) if row[key] else None
                if floating is not None and (
                    not math.isfinite(floating) or floating <= 0
                ):
                    raise ValueError
                values[key] = floating
            if values["sleep_score"] is not None and int(row["sleep_score"]) > 100:
                raise ValueError
            duration = values["duration_seconds"]
            if not isinstance(duration, int) or not 0 <= duration <= 86400:
                raise ValueError
            for key in ("bed_name", "bed_generation", "sleeper_name"):
                values[key] = row[key] or None
            if any(len(value) > 255 for value in row.values()):
                raise ValueError
            if any(
                not row[key]
                for key in (
                    "bed_id",
                    "side",
                    "source_library",
                    "source_library_version",
                    "extractor_version",
                )
            ):
                raise ValueError
            night = values["night_date"]
            assert isinstance(night, date)
            if row["record_key"] != _record_key(sleeper_id, night):
                raise ValueError
            payload = {
                key: value
                for key, value in values.items()
                if key not in {"query_date", "fetched_at", "record_hash"}
            }
            if row["record_hash"] != _record_hash(payload):
                raise ValueError
            day = end.astimezone(zone).date().isoformat()
            if row["record_key"] in keys or day in days:
                raise ValueError
            keys.add(row["record_key"])
            days.add(day)
            mapped.append({"date": day, "sleep_hours": str(duration / 3600)})
        return csv_text(["date", "sleep_hours"], mapped), len(rows)
    except (ValueError, TypeError, KeyError, OverflowError, ZoneInfoNotFoundError):
        raise ServiceError(422, "import_unsupported_sleepiq_export") from None

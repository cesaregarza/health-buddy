"""Library-independent domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class BedInfo:
    bed_id: str
    name: str | None
    generation: str | None
    model: str | None


@dataclass(frozen=True, slots=True)
class SleeperInfo:
    key: str
    bed_id: str
    sleeper_id: str
    name: str | None
    side: str
    active: bool


@dataclass(frozen=True, slots=True)
class Discovery:
    beds: tuple[BedInfo, ...]
    sleepers: tuple[SleeperInfo, ...]

    @property
    def active_sleepers(self) -> tuple[SleeperInfo, ...]:
        return tuple(sleeper for sleeper in self.sleepers if sleeper.active)


@dataclass(frozen=True, slots=True)
class SleepMetrics:
    start_date: str | None = None
    end_date: str | None = None
    duration_seconds: int | None = None
    session_count: int | None = None
    sleep_score: int | None = None
    heart_rate_bpm: float | None = None
    respiratory_rate_bpm: float | None = None
    hrv_ms: float | None = None
    restful_seconds: int | None = None
    restless_seconds: int | None = None
    out_of_bed_seconds: int | None = None
    fall_asleep_seconds: int | None = None

    @property
    def has_data(self) -> bool:
        if self.start_date or self.end_date:
            return True
        return any(
            value is not None and value > 0
            for value in (
                self.duration_seconds,
                self.session_count,
                self.sleep_score,
                self.heart_rate_bpm,
                self.respiratory_rate_bpm,
                self.hrv_ms,
                self.restful_seconds,
                self.restless_seconds,
                self.out_of_bed_seconds,
                self.fall_asleep_seconds,
            )
        )


@dataclass(frozen=True, slots=True)
class SleepSessionMetrics:
    """Sanitized values from one SleepIQ session.

    Identifiers and unmodeled response fields are intentionally omitted so a
    session-level audit cannot leak sleeper, bed, or upstream record IDs.
    """

    start_date: str | None = None
    end_date: str | None = None
    duration_seconds: int | None = None
    total_sleep_seconds: int | None = None
    sleep_score: int | None = None
    heart_rate_bpm: float | None = None
    respiratory_rate_bpm: float | None = None
    hrv_ms: float | None = None
    restful_seconds: int | None = None
    restless_seconds: int | None = None
    out_of_bed_seconds: int | None = None
    fall_asleep_seconds: int | None = None
    longest: bool = False


@dataclass(frozen=True, slots=True)
class NightlyRecord:
    record_key: str
    query_date: date
    night_date: date
    bed_id: str
    bed_name: str | None
    bed_generation: str | None
    sleeper_id: str
    sleeper_name: str | None
    side: str
    session_start: datetime | None
    session_end: datetime | None
    duration_seconds: int | None
    session_count: int | None
    sleep_score: int | None
    heart_rate_bpm: float | None
    respiratory_rate_bpm: float | None
    hrv_ms: float | None
    restful_seconds: int | None
    restless_seconds: int | None
    out_of_bed_seconds: int | None
    fall_asleep_seconds: int | None
    source_library: str
    source_library_version: str
    extractor_version: str
    fetched_at: datetime
    record_hash: str


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    inserted: int = 0
    changed: int = 0
    unchanged: int = 0


@dataclass(slots=True)
class RunSummary:
    run_id: str
    command: str
    requested_from: date | None = None
    requested_to: date | None = None
    bed_count: int = 0
    sleeper_count: int = 0
    api_requests: int = 0
    inserted_records: int = 0
    changed_records: int = 0
    unchanged_records: int = 0
    empty_results: int = 0
    failed_records: int = 0
    elapsed_seconds: float = 0.0
    status: str = "failure"

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "requested_date_range": {
                "from": self.requested_from.isoformat()
                if self.requested_from
                else None,
                "to": self.requested_to.isoformat() if self.requested_to else None,
            },
            "bed_count": self.bed_count,
            "sleeper_count": self.sleeper_count,
            "api_requests": self.api_requests,
            "inserted_records": self.inserted_records,
            "changed_records": self.changed_records,
            "unchanged_records": self.unchanged_records,
            "empty_results": self.empty_results,
            "failed_records": self.failed_records,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class FetchResult:
    records: tuple[NightlyRecord, ...]
    probe_rows: tuple[dict[str, Any], ...] = field(default_factory=tuple)

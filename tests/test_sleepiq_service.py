"""sleepiq_exporter.service: run by the optional sleepiq-exporter console script."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from sleepiq_exporter.config import Settings
from sleepiq_exporter.domain import (
    BedInfo,
    Discovery,
    RunSummary,
    SleeperInfo,
    SleepMetrics,
    SleepSessionMetrics,
    UpsertCounts,
)
from sleepiq_exporter.errors import PartialFailureError
from sleepiq_exporter.retry import RequestExecutor
from sleepiq_exporter.service import ExporterService

DISCOVERY = Discovery(
    beds=(
        BedInfo("bed-1", "name-1", None, "i8"),
        BedInfo("bed-2", "name-2", "fuzion", "i10"),
    ),
    sleepers=(
        SleeperInfo("1", "bed-1", "s1", "one", "left", True),
        SleeperInfo("2", "bed-1", "s2", "two", "right", False),
        SleeperInfo("3", "bed-2", "s3", "three", "right", True),
    ),
)


class FakeAdapter:
    source_library = "asyncsleepiq"
    source_library_version = "1.7.1"

    def __init__(
        self,
        responses: dict[tuple[str, date], SleepMetrics | Exception | None]
        | None = None,
        session_responses: dict[
            tuple[str, date], tuple[SleepSessionMetrics, ...] | Exception
        ]
        | None = None,
    ) -> None:
        self.responses = responses or {}
        self.session_responses = session_responses or {}
        self.calls: list[tuple[str, date]] = []
        self.session_calls: list[tuple[str, date]] = []
        self.initialize_calls = 0
        self.close_calls = 0

    async def initialize(self) -> Discovery:
        self.initialize_calls += 1
        return DISCOVERY

    async def get_sleep_data(
        self, sleeper: SleeperInfo, requested_at: datetime
    ) -> SleepMetrics | None:
        key = (sleeper.sleeper_id, requested_at.date())
        self.calls.append(key)
        value = self.responses.get(key)
        if isinstance(value, Exception):
            raise value
        if key in self.responses:
            return value
        return SleepMetrics(
            start_date=f"{requested_at.date().isoformat()}T04:00:00Z",
            sleep_score=80,
        )

    async def close(self) -> None:
        self.close_calls += 1

    async def get_sleep_sessions(
        self, sleeper: SleeperInfo, requested_at: datetime
    ) -> tuple[SleepSessionMetrics, ...]:
        key = (sleeper.sleeper_id, requested_at.date())
        self.session_calls.append(key)
        value = self.session_responses.get(key, ())
        if isinstance(value, Exception):
            raise value
        return value


class FakeRepository:
    def __init__(self) -> None:
        self.records = []
        self.closed = False

    def upsert(self, records):
        self.records.extend(records)
        return UpsertCounts(inserted=len(records))

    def apply_retention(self, *, as_of: date, retention_days: int | None) -> int:
        return 0

    def export_csv(self, **_: Any) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_sync_includes_today_and_skips_inactive_sleepers(
    settings: Settings, logger
) -> None:
    adapter = FakeAdapter()
    repository = FakeRepository()
    service = ExporterService(
        settings,
        logger,
        adapter_factory=lambda _: adapter,
        repository_factory=lambda _: repository,
    )
    summary = RunSummary("run", "sync")

    await service.sync(summary, today=date(2026, 8, 4))

    assert summary.requested_from == date(2026, 8, 3)
    assert summary.requested_to == date(2026, 8, 4)
    assert summary.bed_count == 2
    assert summary.sleeper_count == 2
    assert summary.api_requests == 4
    assert {sleeper for sleeper, _ in adapter.calls} == {"s1", "s3"}
    assert [day for _, day in adapter.calls] == [
        date(2026, 8, 3),
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 4),
    ]
    assert adapter.initialize_calls == 1
    assert adapter.close_calls == 1
    assert repository.closed


@pytest.mark.asyncio
async def test_empty_date_is_counted_without_failure(
    settings: Settings, logger
) -> None:
    adapter = FakeAdapter(
        {
            ("s1", date(2026, 8, 4)): None,
            ("s3", date(2026, 8, 4)): None,
        }
    )
    repository = FakeRepository()
    service = ExporterService(
        settings,
        logger,
        adapter_factory=lambda _: adapter,
        repository_factory=lambda _: repository,
    )
    summary = RunSummary("run", "backfill")

    await service.backfill(
        summary, from_date=date(2026, 8, 4), to_date=date(2026, 8, 4)
    )

    assert summary.empty_results == 2
    assert summary.failed_records == 0
    assert repository.records == []


@pytest.mark.asyncio
async def test_zero_filled_sleepiq_placeholder_is_empty(
    settings: Settings, logger
) -> None:
    placeholder = SleepMetrics(duration_seconds=0, sleep_score=0)
    adapter = FakeAdapter(
        {
            ("s1", date(2026, 8, 4)): placeholder,
            ("s3", date(2026, 8, 4)): placeholder,
        }
    )
    repository = FakeRepository()
    service = ExporterService(
        settings,
        logger,
        adapter_factory=lambda _: adapter,
        repository_factory=lambda _: repository,
    )
    summary = RunSummary("run", "backfill")

    await service.backfill(
        summary, from_date=date(2026, 8, 4), to_date=date(2026, 8, 4)
    )

    assert summary.empty_results == 2
    assert repository.records == []


@pytest.mark.asyncio
async def test_partial_failure_persists_success_and_raises(
    settings: Settings, logger
) -> None:
    adapter = FakeAdapter({("s1", date(2026, 8, 4)): ValueError("raw body")})
    repository = FakeRepository()
    service = ExporterService(
        settings,
        logger,
        adapter_factory=lambda _: adapter,
        repository_factory=lambda _: repository,
    )
    summary = RunSummary("run", "backfill")

    with pytest.raises(PartialFailureError):
        await service.backfill(
            summary, from_date=date(2026, 8, 4), to_date=date(2026, 8, 4)
        )

    assert summary.failed_records == 1
    assert summary.inserted_records == 1
    assert len(repository.records) == 1
    assert adapter.close_calls == 1
    assert repository.closed


@pytest.mark.asyncio
async def test_probe_is_sanitized_unless_verbose(settings: Settings, logger) -> None:
    adapter = FakeAdapter()
    service = ExporterService(
        settings,
        logger,
        adapter_factory=lambda _: adapter,
        repository_factory=lambda _: FakeRepository(),
    )
    summary = RunSummary("run", "probe")

    output = await service.probe(summary, verbose_data=False, today=date(2026, 8, 4))

    encoded = str(output)
    assert output["bed_count"] == 2
    assert output["active_sleeper_count"] == 2
    assert "sleep_score" not in encoded
    assert "name-1" not in encoded
    assert "s1" not in encoded
    assert {row["side"] for row in output["sleepers"]} == {"left", "right"}


@pytest.mark.asyncio
async def test_session_inspection_filters_side_and_deduplicates_api_dates(
    settings: Settings, logger
) -> None:
    overnight = SleepSessionMetrics(
        start_date="2026-08-01T03:00:00",
        end_date="2026-08-01T10:00:00",
        duration_seconds=25200,
        total_sleep_seconds=24000,
        sleep_score=82,
        fall_asleep_seconds=300,
        longest=True,
    )
    daytime = SleepSessionMetrics(
        start_date="2026-08-01T13:00:00",
        end_date="2026-08-01T14:00:00",
        duration_seconds=3600,
        total_sleep_seconds=3300,
        sleep_score=20,
    )
    adapter = FakeAdapter(
        session_responses={
            ("s3", date(2026, 8, 1)): (overnight, daytime),
            ("s3", date(2026, 8, 2)): (overnight,),
        }
    )
    service = ExporterService(
        settings,
        logger,
        adapter_factory=lambda _: adapter,
        repository_factory=lambda _: FakeRepository(),
    )
    summary = RunSummary("run", "inspect-sessions")

    output = await service.inspect_sessions(
        summary,
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 2),
        side="right",
    )

    assert adapter.session_calls == [
        ("s3", date(2026, 8, 1)),
        ("s3", date(2026, 8, 2)),
    ]
    assert output["session_count"] == 2
    assert output["sessions"][0]["query_dates"] == [
        "2026-08-01",
        "2026-08-02",
    ]
    assert output["sessions"][0]["session_start_local"].endswith("-05:00")
    assert output["sessions"][1]["duration_seconds"] == 3600


@pytest.mark.asyncio
async def test_transient_timeout_then_success_is_retried() -> None:
    calls = 0
    sleeps: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return "ok"

    async def no_sleep(value: float) -> None:
        sleeps.append(value)

    executor = RequestExecutor(
        delay_seconds=0,
        max_retries=2,
        sleep=no_sleep,
        clock=lambda: 0,
        random_value=lambda: 0,
    )

    assert await executor.run(operation) == "ok"
    assert executor.attempts == 2
    assert calls == 2
    assert sleeps == [1.0]
